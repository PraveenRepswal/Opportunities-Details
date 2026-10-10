"""Hybrid metadata extraction for scraped opportunities.

Stage 1 (rules): fast, deterministic regex/heuristic extraction of
deadline, organization, location, and type. Runs inline in the scrape
pipeline at negligible cost.

Stage 2 (LLM fallback): async enrichment for items whose rules pass left
critical fields missing, using the local Ollama (or llama.cpp) server.
Runs as a background task so scraping responses stay fast.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import dateparser
import httpx

from config import settings
from backend.schemas import OpportunityProfile, SLMExtractionSchema
from backend.text_utils import (
    clean_opportunity_text,
    clean_opportunity_title,
    extract_lead_summary,
)

logger = logging.getLogger(__name__)

METADATA_FIELDS = ("deadline", "organization", "location", "type")

# ---------------------------------------------------------------------------
# Deadline extraction
# ---------------------------------------------------------------------------

DEADLINE_KEYWORD_RE = re.compile(
    r"(?:application\s+)?deadline|closing\s+date|closes?\s+on|closed?\s+by|"
    r"apply\s+by|apply\s+before|applications?\s+(?:close|due)|last\s+date|"
    r"submission\s+(?:date|deadline)|due\s+date|extended\s+to|"
    r"date\s+limite|échéance",
    re.IGNORECASE,
)

_MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|December"
    "|janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre"
    "|enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre"
    "|Januar|Februar|März|Juni|Juli|Oktober|Dezember"
)

DATE_CANDIDATE_RES = [
    # 8 September 2026 / 8th of September, 2026
    re.compile(
        rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:{_MONTHS})\s*,?\s*\d{{4}}\b",
        re.IGNORECASE,
    ),
    # September 8, 2026 / September 8th 2026
    re.compile(
        rf"\b(?:{_MONTHS})\s+\d{{1,2}}(?:st|nd|rd|th)?\s*,?\s*\d{{4}}\b",
        re.IGNORECASE,
    ),
    # ISO dates
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    # 08/09/2026 or 08-09-2026 (day-first preferred by parser settings)
    re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b"),
    # 8 September / September 8 (no year -> resolved to future occurrence)
    re.compile(
        rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:{_MONTHS})\b(?!\s*,?\s*\d{{4}})",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:{_MONTHS})\s+\d{{1,2}}(?:st|nd|rd|th)?\b(?!\s*,?\s*\d{{4}})",
        re.IGNORECASE,
    ),
]

_DATEPARSER_SETTINGS = {
    "PREFER_DAY_OF_MONTH": "first",
    "PREFER_DATES_FROM": "future",
    "RETURN_AS_TIMEZONE_AWARE": False,
    "STRICT_PARSING": False,
    "DATE_ORDER": "DMY",
}

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _parse_date_candidate(text: str, now: datetime) -> Optional[datetime]:
    parsed = dateparser.parse(text.strip(), settings=_DATEPARSER_SETTINGS)
    if not parsed:
        return None
    lower = now - timedelta(days=365)
    upper = now + timedelta(days=3 * 365)
    if lower <= parsed <= upper:
        return parsed
    return None


def _extract_deadline(title: str, content: str, now: datetime) -> Optional[str]:
    """Return the latest plausible deadline (ISO date) found near deadline keywords."""
    candidates: List[datetime] = []
    for segment in _SENTENCE_SPLIT_RE.split(content):
        if not DEADLINE_KEYWORD_RE.search(segment):
            continue
        for pattern in DATE_CANDIDATE_RES:
            for match in pattern.finditer(segment):
                dt = _parse_date_candidate(match.group(0), now)
                if dt:
                    candidates.append(dt)
    if not candidates:
        return None
    return max(candidates).date().isoformat()


# ---------------------------------------------------------------------------
# Organization extraction
# ---------------------------------------------------------------------------

# Capitalized token allowing internal abbreviation periods ("U.S.") but not
# sentence boundaries ("Service. Next") since a space must follow any period.
_ORG_TOKEN = r"[A-Z][\w''&\-]*(?:\.[A-Z][\w''&\-]*)*"

_ORG_BY_RE = re.compile(
    r"\b(?:offered|funded|provided|awarded|administered|hosted|presented|"
    r"organized|organised|supported|launched)\s+by\s+((?:the\s+)?"
    + _ORG_TOKEN
    + r"(?:\s+"
    + _ORG_TOKEN
    + r"){0,5})",
)

_UNIVERSITY_RES = [
    re.compile(
        r"\b((?:the\s+)?"
        + _ORG_TOKEN
        + r"(?:\s+(?:of|de)\s+"
        + _ORG_TOKEN
        + r")*(?:\s+"
        + _ORG_TOKEN
        + r"){0,3})\s+University\b"
    ),
    re.compile(
        r"\bUniversity\s+of\s+((?:the\s+)?"
        + _ORG_TOKEN
        + r"(?:\s+"
        + _ORG_TOKEN
        + r"){0,3})"
    ),
]

_TITLE_TYPE_NOUN_RE = re.compile(
    r"^(.{2,60}?)\s+(scholarships?|fellowships?|grants?|internships?|"
    r"programmes?|programs?|awards?|prizes?|competitions?)\b",
    re.IGNORECASE,
)

_TITLE_STOPWORDS = {
    "how", "what", "why", "when", "where", "guide", "tips", "steps", "best",
    "top", "list", "apply", "applying", "application", "applications", "your",
    "the", "a", "an", "for", "to", "of", "and", "in", "on", "at", "with",
    "fully", "partially", "funded", "funding", "international", "undergraduate",
    "graduate", "postgraduate", "masters", "master", "phd", "doctoral",
    "bachelor", "bachelors", "students", "student", "study", "prepare",
    "preparing", "write", "writing", "winning", "complete", "ultimate",
}

_ORG_TRAILING_STOPWORDS = {"at", "in", "for", "to", "and", "the", "on", "with", "of"}

_SMALL_WORDS = {"of", "the", "and", "for", "in", "de", "da", "di", "von", "van"}


def _clean_org(candidate: str) -> Optional[str]:
    candidate = re.sub(r"\s+", " ", candidate).strip()
    candidate = re.sub(r"^(the|a|an)\s+", "", candidate, flags=re.IGNORECASE)
    words = candidate.split(" ")
    while words and words[-1].lower() in _ORG_TRAILING_STOPWORDS:
        words.pop()
    candidate = " ".join(words).strip(" ,.;:-'\"()")
    if not (2 <= len(candidate) <= 70):
        return None
    if sum(w[0].isalnum() for w in words) < 1:
        return None
    if candidate.lower() == candidate:
        words = [
            w.capitalize() if w.lower() not in _SMALL_WORDS else w.lower()
            for w in candidate.split(" ")
        ]
        if words:
            words[0] = words[0].capitalize()
        candidate = " ".join(words)
    return candidate


def _org_from_title(title: str) -> Optional[str]:
    match = _TITLE_TYPE_NOUN_RE.match(title.strip())
    if not match:
        return None
    words = match.group(1).strip().split(" ")
    while words and words[-1].lower() in _TITLE_STOPWORDS:
        words.pop()
    start = 0
    while start < len(words) and words[start].lower() in _TITLE_STOPWORDS:
        start += 1
    words = words[start:]
    if not (1 <= len(words) <= 5):
        return None
    return _clean_org(" ".join(words))


def _extract_organization(title: str, content: str) -> Optional[str]:
    head = content[:1500]
    for pattern in (_ORG_BY_RE,):
        match = pattern.search(head)
        if match:
            org = _clean_org(match.group(1))
            if org:
                return org
    for pattern in _UNIVERSITY_RES:
        match = pattern.search(head)
        if match:
            org = _clean_org(match.group(1) + " University")
            if org:
                return org
    return _org_from_title(title)


# ---------------------------------------------------------------------------
# Location extraction
# ---------------------------------------------------------------------------

_COUNTRY_ALIASES: Dict[str, str] = {
    "usa": "United States", "u.s.a": "United States",
    "united states of america": "United States", "america": "United States",
    "uk": "United Kingdom", "u.k": "United Kingdom", "britain": "United Kingdom",
    "great britain": "United Kingdom", "england": "United Kingdom",
    "scotland": "United Kingdom", "wales": "United Kingdom",
    "united arab emirates": "United Arab Emirates", "uae": "United Arab Emirates",
    "south korea": "South Korea", "korea": "South Korea",
    "holland": "Netherlands", "the netherlands": "Netherlands",
    "slovak republic": "Slovakia", "czech republic": "Czechia",
    "russian federation": "Russia", "republic of korea": "South Korea",
    "hong kong sar": "Hong Kong", "macau": "Macao",
    "swaziland": "Eswatini", "burma": "Myanmar", "cape verde": "Cabo Verde",
    "cote d'ivoire": "Ivory Coast", "ivory coast": "Ivory Coast",
}

_COUNTRIES = [
    "Afghanistan", "Albania", "Algeria", "Andorra", "Angola", "Antigua and Barbuda",
    "Argentina", "Armenia", "Australia", "Austria", "Azerbaijan", "Bahamas",
    "Bahrain", "Bangladesh", "Barbados", "Belarus", "Belgium", "Belize", "Benin",
    "Bhutan", "Bolivia", "Bosnia and Herzegovina", "Botswana", "Brazil", "Brunei",
    "Bulgaria", "Burkina Faso", "Burundi", "Cabo Verde", "Cambodia", "Cameroon",
    "Canada", "Central African Republic", "Chad", "Chile", "China", "Colombia",
    "Comoros", "Congo", "Costa Rica", "Croatia", "Cuba", "Cyprus", "Czechia",
    "Denmark", "Djibouti", "Dominica", "Dominican Republic", "Ecuador", "Egypt",
    "El Salvador", "Equatorial Guinea", "Eritrea", "Estonia", "Eswatini",
    "Ethiopia", "Fiji", "Finland", "France", "Gabon", "Gambia", "Georgia",
    "Germany", "Ghana", "Greece", "Grenada", "Guatemala", "Guinea", "Guyana",
    "Haiti", "Honduras", "Hong Kong", "Hungary", "Iceland", "India", "Indonesia",
    "Iran", "Iraq", "Ireland", "Israel", "Italy", "Jamaica", "Japan", "Jordan",
    "Kazakhstan", "Kenya", "Kiribati", "Kuwait", "Kyrgyzstan", "Laos", "Latvia",
    "Lebanon", "Lesotho", "Liberia", "Libya", "Liechtenstein", "Lithuania",
    "Luxembourg", "Macao", "Madagascar", "Malawi", "Malaysia", "Maldives",
    "Mali", "Malta", "Marshall Islands", "Mauritania", "Mauritius", "Mexico",
    "Micronesia", "Moldova", "Monaco", "Mongolia", "Montenegro", "Morocco",
    "Mozambique", "Myanmar", "Namibia", "Nauru", "Nepal", "Netherlands",
    "New Zealand", "Nicaragua", "Niger", "Nigeria", "North Macedonia", "Norway",
    "Oman", "Pakistan", "Palau", "Palestine", "Panama", "Papua New Guinea",
    "Paraguay", "Peru", "Philippines", "Poland", "Portugal", "Qatar", "Romania",
    "Rwanda", "Samoa", "San Marino", "Sao Tome and Principe", "Saudi Arabia",
    "Senegal", "Serbia", "Seychelles", "Sierra Leone", "Singapore", "Slovakia",
    "Slovenia", "Solomon Islands", "Somalia", "South Africa", "South Korea",
    "South Sudan", "Spain", "Sri Lanka", "Sudan", "Suriname", "Sweden",
    "Switzerland", "Syria", "Taiwan", "Tajikistan", "Tanzania", "Thailand",
    "Timor-Leste", "Togo", "Tonga", "Trinidad and Tobago", "Tunisia", "Turkey",
    "Turkmenistan", "Tuvalu", "Uganda", "Ukraine", "United Arab Emirates",
    "United Kingdom", "United States", "Uruguay", "Uzbekistan", "Vanuatu",
    "Vatican City", "Venezuela", "Vietnam", "Yemen", "Zambia", "Zimbabwe",
]

_CANONICAL_LOOKUP: Dict[str, str] = {}
for _country in _COUNTRIES:
    _CANONICAL_LOOKUP[_country.lower()] = _country
for _alias, _canonical in _COUNTRY_ALIASES.items():
    _CANONICAL_LOOKUP[_alias] = _canonical

_LOCATION_TERM_RE = re.compile(
    r"\b("
    + "|".join(
        re.escape(key) for key in sorted(_CANONICAL_LOOKUP.keys(), key=len, reverse=True)
    )
    + r")\b",
    re.IGNORECASE,
)

_STUDY_IN_RE = re.compile(
    r"\bstud(?:y|ies|ying)\s+(?:in|at)\s+((?:the\s+)?[A-Za-z][\w''&.\-]*(?:\s+[A-Za-z][\w''&.\-]*){0,3})",
    re.IGNORECASE,
)


def _canonicalize_location(text: str) -> Optional[str]:
    text = re.sub(r"\s+", " ", text).strip()
    lowered = text.lower()
    if lowered in _CANONICAL_LOOKUP:
        return _CANONICAL_LOOKUP[lowered]
    probe = lowered
    for stop in ("the ",):
        if probe.startswith(stop):
            probe = probe[len(stop):]
    if probe in _CANONICAL_LOOKUP:
        return _CANONICAL_LOOKUP[probe]
    return None


def _extract_location(title: str, content: str) -> Optional[str]:
    study_match = _STUDY_IN_RE.search(content[:3000])
    if study_match:
        canonical = _canonicalize_location(study_match.group(1))
        if canonical:
            return canonical
    title_match = _LOCATION_TERM_RE.search(title)
    if title_match:
        return _CANONICAL_LOOKUP[title_match.group(1).lower()]
    head_match = _LOCATION_TERM_RE.search(content[:1200])
    if head_match:
        return _CANONICAL_LOOKUP[head_match.group(1).lower()]
    if study_match:
        cleaned = re.sub(r"\s+", " ", study_match.group(1)).strip()
        cleaned = re.sub(r"^(the)\s+", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.split(" ,")[0].strip(" ,.;:")
        if 2 <= len(cleaned) <= 40:
            return cleaned.title() if cleaned.lower() == cleaned else cleaned
    return None


# ---------------------------------------------------------------------------
# Type extraction
# ---------------------------------------------------------------------------

_FUNDING_PATTERNS: Tuple[Tuple[str, re.Pattern], ...] = (
    ("Fully Funded", re.compile(r"fully[\s-]?funded", re.IGNORECASE)),
    (
        "Partially Funded",
        re.compile(r"partial(?:ly)?[\s-]?funded|partial\s+scholarship", re.IGNORECASE),
    ),
)

_CATEGORY_PATTERNS: Tuple[Tuple[str, re.Pattern], ...] = (
    ("Internship", re.compile(r"\binternships?\b", re.IGNORECASE)),
    ("Fellowship", re.compile(r"\bfellowships?\b", re.IGNORECASE)),
    ("Hackathon", re.compile(r"\bhackathons?\b", re.IGNORECASE)),
    ("Competition", re.compile(r"\bcompetitions?\b|\bcontests?\b", re.IGNORECASE)),
    ("Conference", re.compile(r"\bconferences?\b|\bsummits?\b", re.IGNORECASE)),
    (
        "Exchange Program",
        re.compile(r"\bexchange\s+(?:programme|program)s?\b", re.IGNORECASE),
    ),
    ("Training", re.compile(r"\btrainings?\b|\bworkshops?\b", re.IGNORECASE)),
    ("Volunteering", re.compile(r"\bvolunteer(?:ing|ship)?s?\b", re.IGNORECASE)),
    ("Grant", re.compile(r"\bgrants?\b", re.IGNORECASE)),
    ("Award", re.compile(r"\bawards?\b|\bprizes?\b", re.IGNORECASE)),
    ("Scholarship", re.compile(r"\bscholarships?\b|\bbourses?\b", re.IGNORECASE)),
)


def _extract_type(title: str, content: str) -> Optional[str]:
    content_haystack = content[:1500]
    funding = next(
        (
            label
            for label, pattern in _FUNDING_PATTERNS
            if pattern.search(title) or pattern.search(content_haystack)
        ),
        None,
    )
    title_category = next(
        (label for label, pattern in _CATEGORY_PATTERNS if pattern.search(title)),
        None,
    )
    content_category = next(
        (label for label, pattern in _CATEGORY_PATTERNS if pattern.search(content_haystack)),
        None,
    )
    # Title-derived category wins: titles are curated labels while body text
    # frequently contains negated or incidental mentions ("not an exchange program").
    category = title_category or content_category
    if funding and category:
        return f"{funding} {category}"
    return funding or category


# ---------------------------------------------------------------------------
# Public API — rules stage
# ---------------------------------------------------------------------------

def extract_metadata_rules(title: str, content: str) -> Dict[str, Optional[str]]:
    """Extract metadata from a single opportunity using deterministic rules."""
    now = datetime.now()
    return {
        "deadline": _extract_deadline(title, content, now),
        "organization": _extract_organization(title, content),
        "location": _extract_location(title, content),
        "type": _extract_type(title, content),
    }


def find_missing_fields(metadata: Optional[Dict[str, Any]]) -> List[str]:
    """Return metadata fields that are absent or empty."""
    if not metadata:
        return list(METADATA_FIELDS)
    return [
        field
        for field in METADATA_FIELDS
        if not metadata.get(field)
    ]


# ---------------------------------------------------------------------------
# Stage 2: Small Language Model (SLM) Structured Ingestion Profiling
# ---------------------------------------------------------------------------

SLM_SYSTEM_PROMPT = (
    "You are a precise data extraction specialist. Extract a concise, structured "
    "opportunity profile from the text. Follow the exact JSON schema provided. "
    "Be factual, concise, and never include promotional or boilerplate text. "
    "If a field is not found in the text, use 'Not specified'. Never output null."
)


def build_fallback_profile(
    title: str,
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> OpportunityProfile:
    """Build a clean OpportunityProfile using deterministic metadata and text leads."""
    meta = metadata if metadata else extract_metadata_rules(title, content)
    clean_t = clean_opportunity_title(title)
    lead_summary = extract_lead_summary(content, max_sentences=2, max_chars=400)

    return OpportunityProfile(
        title=clean_t or "Untitled Opportunity",
        organization=meta.get("organization"),
        location=meta.get("location"),
        deadline=meta.get("deadline"),
        type=meta.get("type"),
        eligibility=meta.get("eligibility"),
        benefits=meta.get("benefits"),
        summary=lead_summary or "Details available in the full announcement.",
    )


async def is_slm_available(server_url: Optional[str] = None) -> bool:
    """Check if the local llama-server (Option B) is responsive."""
    url = (server_url or settings.model.llamacpp_server_url).rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            resp = await client.get(f"{url}/health")
            if resp.status_code in (200, 204):
                return True
            models_resp = await client.get(f"{url}/v1/models")
            return models_resp.status_code == 200
    except Exception:
        return False


def _coerce_profile_dict(data_dict: Dict[str, Any], default_title: str) -> OpportunityProfile:
    """Coerce raw JSON output dictionary into a sanitized OpportunityProfile."""
    out_title = data_dict.get("title") or default_title

    def _clean_field(val: Any) -> Optional[str]:
        if not val or str(val).lower() in ("not specified", "null", "none"):
            return None
        return str(val).strip()

    return OpportunityProfile(
        title=out_title,
        organization=_clean_field(data_dict.get("organization")),
        location=_clean_field(data_dict.get("location")),
        deadline=_clean_field(data_dict.get("deadline")),
        type=_clean_field(data_dict.get("type")),
        eligibility=_clean_field(data_dict.get("eligibility")),
        benefits=_clean_field(data_dict.get("benefits")),
        summary=data_dict.get("summary") or "Details available in the full announcement.",
    )


async def extract_profile_with_llamacpp(
    title: str,
    content: str,
    server_url: Optional[str] = None,
    model: str = "lfm-350m",
    timeout: float = 30.0,
) -> Optional[OpportunityProfile]:
    """Option B: Extract structured profile via llama-server GBNF schema decoding."""
    url = f"{(server_url or settings.model.llamacpp_server_url).rstrip('/')}/v1/chat/completions"

    clean_t = clean_opportunity_title(title)
    max_chars = min(settings.scraper.metadata_content_chars, 1800)
    cleaned_content = clean_opportunity_text(content)[:max_chars]
    prompt_text = f"Title: {clean_t}\n\nContent:\n{cleaned_content}"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SLM_SYSTEM_PROMPT},
            {"role": "user", "content": prompt_text},
        ],
        "response_format": {
            "type": "json_object",
            "schema": SLMExtractionSchema.model_json_schema(),
        },
        "temperature": 0.1,
        "repeat_penalty": 1.15,
        "max_tokens": 400,
        "reasoning_effort": "none",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                choices = data.get("choices") or []
                if not choices:
                    logger.warning("llama-server returned empty choices.")
                    return None
                raw_json = (choices[0].get("message", {}).get("content") or "").strip()
                if not raw_json:
                    logger.warning("llama-server returned empty content string.")
                    return None

                try:
                    data_dict = json.loads(raw_json)
                except Exception as json_err:
                    logger.warning("Invalid JSON from llama-server: %s (content: %s)", json_err, raw_json[:100])
                    return None

                return _coerce_profile_dict(data_dict, clean_t)
            else:
                logger.warning("llama-server returned HTTP %d: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.warning("llama-server extraction failed: %s", exc)
    return None


async def extract_profile_with_ollama(
    title: str,
    content: str,
    base_url: Optional[str] = None,
    model: str = "lfm-350m",
    timeout: float = 30.0,
) -> Optional[OpportunityProfile]:
    """Option A Alternative: Extract structured profile via Ollama JSON schema."""
    import ollama

    clean_t = clean_opportunity_title(title)
    max_chars = min(settings.scraper.metadata_content_chars, 1800)
    cleaned_content = clean_opportunity_text(content)[:max_chars]
    prompt_text = f"Title: {clean_t}\n\nContent:\n{cleaned_content}"
    url = (base_url or settings.model.ollama_base_url).rstrip("/")

    try:
        client = ollama.AsyncClient(host=url)
        resp = await asyncio.wait_for(
            client.chat(
                model=model,
                messages=[
                    {"role": "system", "content": SLM_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt_text},
                ],
                format=SLMExtractionSchema.model_json_schema(),
                options={"temperature": 0.1, "num_predict": 300},
            ),
            timeout=timeout,
        )
        raw_json = (resp.get("message", {}).get("content") or "").strip()
        if not raw_json:
            return None

        try:
            data_dict = json.loads(raw_json)
        except Exception as json_err:
            logger.warning("Invalid JSON from Ollama: %s", json_err)
            return None

        return _coerce_profile_dict(data_dict, clean_t)
    except Exception as exc:
        logger.warning("Ollama extraction failed: %s", exc)
    return None


async def get_or_extract_profile(
    raw_item: Dict[str, Any],
    backend: Optional[str] = None,
    model: Optional[str] = None,
) -> OpportunityProfile:
    """Extract opportunity profile using SLM if available, otherwise heuristic fallback."""
    title = raw_item.get("name") or raw_item.get("title") or "Opportunity"
    content = raw_item.get("content") or ""
    metadata = raw_item.get("metadata") or {}

    chosen_backend = backend or os.getenv("SLM_BACKEND", "llamacpp").lower()
    chosen_model = model or os.getenv("SLM_MODEL_NAME", "lfm-350m")

    # 1. Option B: llama-server
    if chosen_backend == "llamacpp" and await is_slm_available():
        profile = await extract_profile_with_llamacpp(
            title=title,
            content=content,
            model=chosen_model,
        )
        if profile:
            return profile

    # 2. Option A: Ollama alternative
    elif chosen_backend == "ollama":
        profile = await extract_profile_with_ollama(
            title=title,
            content=content,
            model=chosen_model,
        )
        if profile:
            return profile

    # 3. Graceful Heuristic Fallback
    return build_fallback_profile(title, content, metadata)


def format_profile_for_vectorstore(profile: OpportunityProfile, url: str = "") -> str:
    """Format a dense, semantic text representation of the profile for vector embedding."""
    meta_line = " | ".join(
        filter(
            None,
            [
                f"Organization: {profile.organization}" if profile.organization else None,
                f"Location: {profile.location}" if profile.location else None,
                f"Type: {profile.type}" if profile.type else None,
            ],
        )
    )
    parts = [f"Title: {profile.title}"]
    if meta_line:
        parts.append(meta_line)
    if profile.deadline:
        parts.append(f"Deadline: {profile.deadline}")
    if url:
        parts.append(f"Link: {url}")
    if profile.summary:
        parts.append(f"Summary: {profile.summary}")
    if profile.eligibility:
        parts.append(f"Eligibility: {profile.eligibility}")
    if profile.benefits:
        parts.append(f"Benefits: {profile.benefits}")

    return "\n".join(parts)


async def enrich_opportunity_profiles(
    entries: Sequence[Tuple[int, Dict[str, Any]]],
    concurrency: int = 2,
    timeout: float = 30.0,
) -> int:
    """Enrich opportunities with SLM-extracted OpportunityProfile and persist to SQLite."""
    if not entries or not await is_slm_available():
        return 0

    from backend.database import update_opportunity_profile_and_metadata

    sem = asyncio.Semaphore(concurrency)
    enriched = 0

    async def _worker(db_id: int, item: Dict[str, Any]) -> None:
        nonlocal enriched
        async with sem:
            try:
                prof = await get_or_extract_profile(item)
                if prof:
                    update_opportunity_profile_and_metadata(db_id, prof.model_dump())
                    enriched += 1
            except Exception as exc:
                logger.warning("SLM profile enrichment failed for id %s: %s", db_id, exc)

    await asyncio.gather(*(_worker(db_id, item) for db_id, item in entries))
    logger.info("SLM profile enrichment completed: %d/%d updated", enriched, len(entries))
    return enriched



