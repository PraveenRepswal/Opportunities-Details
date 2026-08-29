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
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import dateparser

from config import settings

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
# Stage 2 — LLM fallback enrichment
# ---------------------------------------------------------------------------

_LLM_SYSTEM_PROMPT = (
    "You extract structured metadata from scholarship and opportunity postings. "
    "Respond with ONLY a valid JSON object, no markdown, no explanations."
)

_LLM_USER_TEMPLATE = (
    "Extract metadata from this opportunity posting. Respond with a JSON object "
    'with exactly these keys: "deadline" (ISO format YYYY-MM-DD or null), '
    '"organization" (host institution/foundation or null), '
    '"location" (country where the opportunity takes place or null), '
    '"type" (e.g. "Fully Funded Scholarship", "Fellowship", "Internship" or null). '
    "Use null for any field not clearly stated in the text.\n\n"
    "Title: {title}\n\nContent:\n{content}"
)


def _coerce_llm_metadata(raw: Dict[str, Any]) -> Dict[str, Optional[str]]:
    coerced: Dict[str, Optional[str]] = {}
    for field in METADATA_FIELDS:
        value = raw.get(field)
        if isinstance(value, str):
            value = value.strip()
            if not value or value.lower() in ("null", "none", "n/a", "unknown"):
                value = None
        elif isinstance(value, (int, float)):
            value = str(value)
        else:
            value = None
        coerced[field] = value
    if coerced.get("deadline"):
        parsed = dateparser.parse(
            coerced["deadline"],
            settings={"PREFER_DAY_OF_MONTH": "first", "DATE_ORDER": "DMY"},
        )
        coerced["deadline"] = parsed.date().isoformat() if parsed else None
    for field in METADATA_FIELDS:
        if coerced[field] and len(coerced[field]) > 100:
            coerced[field] = None
    return coerced


def _parse_llm_json(text: str) -> Optional[Dict[str, Any]]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
                return data if isinstance(data, dict) else None
            except json.JSONDecodeError:
                return None
    return None


async def _llm_extract_one(
    title: str,
    content: str,
    model: str,
    timeout: float,
) -> Optional[Dict[str, Optional[str]]]:
    import ollama

    prompt = _LLM_USER_TEMPLATE.format(
        title=title,
        content=content[: settings.scraper.metadata_content_chars],
    )
    client = ollama.AsyncClient(host=settings.model.ollama_base_url)
    response = await asyncio.wait_for(
        client.chat(
            model=model,
            messages=[
                {"role": "system", "content": _LLM_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            format="json",
            options={"temperature": 0.0, "num_predict": 200},
        ),
        timeout=timeout,
    )
    payload = _parse_llm_json(response["message"]["content"])
    if payload is None:
        return None
    return _coerce_llm_metadata(payload)


async def enrich_missing_metadata(
    entries: Sequence[Tuple[int, Dict[str, Any]]],
) -> int:
    """LLM-enrich opportunities with incomplete metadata and persist results.

    Args:
        entries: sequence of ``(opportunity_db_id, item_dict)`` pairs whose
            rule-based metadata is incomplete.

    Returns:
        Number of opportunities successfully enriched.
    """
    if not entries:
        return 0

    from backend.database import update_opportunity_metadata

    model = "qwen3.5:4b"
    sem = asyncio.Semaphore(settings.scraper.llm_enrichment_concurrency)
    enriched = 0

    async def _enrich(db_id: int, item: Dict[str, Any]) -> None:
        nonlocal enriched
        async with sem:
            try:
                extracted = await _llm_extract_one(
                    item.get("name") or item.get("title") or "",
                    item.get("content") or "",
                    model=model,
                    timeout=settings.scraper.llm_enrichment_timeout,
                )
            except Exception as exc:
                logger.warning("LLM enrichment failed for opp %s: %s", db_id, exc)
                return
        if not extracted:
            return
        merged = dict(item.get("metadata") or {})
        changed = False
        for field in find_missing_fields(merged):
            if extracted.get(field):
                merged[field] = extracted[field]
                changed = True
        if changed:
            update_opportunity_metadata(db_id, merged)
            enriched += 1

    await asyncio.gather(*(_enrich(db_id, item) for db_id, item in entries))
    logger.info("LLM metadata enrichment finished: %d/%d updated", enriched, len(entries))
    return enriched
