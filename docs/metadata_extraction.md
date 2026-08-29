# `backend/metadata_extractor.py` — Hybrid Metadata Extraction (Rules + LLM)

> **File:** `backend/metadata_extractor.py` (558 lines)
> **Purpose:** Two-stage metadata extraction for scraped opportunity postings. Stage 1 (rules) runs deterministically inline at near-zero cost. Stage 2 (LLM fallback) enriches items where rules left critical fields missing, asynchronously so scraping stays fast.

---

## 1. Purpose & Overview

Every scraped opportunity has four key fields the assistant cares about:

- **`deadline`** — when applications close.
- **`organization`** — who offers it.
- **`location`** — country where the opportunity takes place.
- **`type`** — category (Scholarship, Internship, Fellowship, Grant…) and funding level (Fully Funded, Partially Funded).

The scrapers (`scrapers/*.py`) produce raw `title` + `content` text. `metadata_extractor.py` converts those into structured metadata that:

- Powers search/filter UIs in the frontend.
- Drives the per-card metadata line the LLM emits (`• Deadline: … \| Organization: … \| Location: … \| Type: …`).
- Enables keyword aggregations in the dashboard.

The **hybrid** design — fast rules first, LLM only for missing fields — keeps scraping latency low (no LLM call per item) while still hitting >90% field coverage on realistic scraped content.

---

## 2. Architecture

```
                    scraped title + content
                              │
                              ▼
              ┌───────────────────────────────┐
              │  STAGE 1: RULES               │
              │  extract_metadata_rules(…)    │  ← inline, microseconds
              │                               │
              │   _extract_deadline           │
              │   _extract_organization       │
              │   _extract_location           │
              │   _extract_type               │
              └───────────────────────────────┘
                              │
                              ▼
                       metadata = {deadline, organization,
                                   location, type}
                              │
                              ▼
                    find_missing_fields(metadata)
                              │
                              ▼
                  any fields still None? ──── no ──► store as-is
                              │
                              │ yes
                              ▼
              ┌───────────────────────────────┐
              │  STAGE 2: LLM ENRICHMENT      │
              │  enrich_missing_metadata([…]) │  ← async, background
              │                               │
              │  • asyncio.Semaphore(2)       │
              │  • ollama.AsyncClient         │
              │  • _llm_extract_one per item  │
              │  • _coerce_llm_metadata       │
              │  • update_opportunity_metadata│
              └───────────────────────────────┘
                              │
                              ▼
                       merged metadata persisted
```

### Module-level layout

| Lines         | Section                                                       |
| ------------- | ------------------------------------------------------------- |
| `1–27`        | Module docstring, imports, `METADATA_FIELDS` tuple           |
| `29–108`     | Deadline extraction (regexes + `dateparser`)                 |
| `111–220`    | Organization extraction (regex patterns + title heuristic)    |
| `223–330`    | Location extraction (country list + alias map)               |
| `333–386`    | Type extraction (funding + category patterns)                 |
| `389–412`    | Public API: `extract_metadata_rules`, `find_missing_fields`   |
| `415–558`    | LLM enrichment stage (prompts, JSON parsing, async enrich)    |

---

## 3. Key Classes & Functions

### 3.1 Constants

#### `METADATA_FIELDS = ("deadline", "organization", "location", "type")` — `backend/metadata_extractor.py:26`

The single source of truth for the four fields. Iterated by `find_missing_fields`, the LLM coercion routine, and the LLM user template.

---

### 3.2 Deadline extraction

#### `DEADLINE_KEYWORD_RE` — `backend/metadata_extractor.py:32`

A multilingual list of phrases signaling a deadline follows. Includes English variants plus French (`date limite`, `échéance`) for international coverage:

```
application deadline, deadline, closing date,
closes on, closed by, apply by, apply before,
applications close, applications due, last date,
submission date, submission deadline, due date,
extended to, date limite, échéance
```

#### `_MONTHS` — `backend/metadata_extractor.py:40`

Concatenated regex alternation covering **English, French, Spanish, and German** month names. e.g., `January|janvier|enero|Januar`.

#### `DATE_CANDIDATE_RES` — `backend/metadata_extractor.py:47`

An ordered list of six regex patterns tried in turn against sentences containing deadline keywords:

| Pattern | Example matches |
| ------- | --------------- |
| `\b\d{1,2}(?:st\|nd\|rd\|th)?\s+(?:of\s+)?(?:MONTHS)\s*,?\s*\d{4}\b` | `8 September 2026`, `8th of September, 2026` |
| `\b(?:MONTHS)\s+\d{1,2}(?:st\|nd\|rd\|th)?\s*,?\s*\d{4}\b` | `September 8, 2026`, `September 8th 2026` |
| `\b\d{4}-\d{2}-\d{2}\b` | `2026-09-08` |
| `\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b` | `08/09/2026`, `08-09-2026` |
| `\b\d{1,2}(?:st\|nd\|rd\|th)?\s+(?:of\s+)?(?:MONTHS)\b(?!\s*,?\s*\d{4})` | `8 September` (no year) |
| `\b(?:MONTHS)\s+\d{1,2}(?:st\|nd\|rd\|th)?\b(?!\s*,?\s*\d{4})` | `September 8` (no year) |

#### `_DATEPARSER_SETTINGS` — `backend/metadata_extractor.py:73`

```python
{
    "PREFER_DAY_OF_MONTH": "first",
    "PREFER_DATES_FROM": "future",   # ← no-year dates resolve to next occurrence
    "RETURN_AS_TIMEZONE_AWARE": False,
    "STRICT_PARSING": False,
    "DATE_ORDER": "DMY",             # ← day-first interpretation
}
```

Day-first ordering matches the European/international convention used by most scraped sources.

#### `_parse_date_candidate(text, now) -> Optional[datetime]` — `backend/metadata_extractor.py:84`

Calls `dateparser.parse(...)` with the settings above, then enforces a **plausibility window**: `now - 365 days ≤ parsed ≤ now + 3 years`. Anything outside this window is rejected. This prevents historical dates mentioned in the body (e.g., "test dates from 1 January 2021 up to the application deadline") from being mistaken for the deadline — a bug demonstrated in `tests/test_metadata_extractor.py:53–55`.

#### `_extract_deadline(title, content, now) -> Optional[str]` — `backend/metadata_extractor.py:95`

Main deadline extractor.

```python
def _extract_deadline(title, content, now):
    candidates: List[datetime] = []
    for segment in _SENTENCE_SPLIT_RE.split(content):       # sentence-by-sentence
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
```

Algorithm:
1. **Sentence-split** the content (split on `.!?` followed by whitespace).
2. For each sentence containing a deadline keyword, try every date pattern.
3. Collect every parsed date that passes the plausibility window.
4. Return the **maximum** (latest) date as ISO `YYYY-MM-DD`.

The "latest wins" rule handles phrases like `"the deadline has been extended to 8 September 2026"` when earlier dates appear in the same sentence (test scores, application windows, etc.).

---

### 3.3 Organization extraction

#### `_ORG_TOKEN` — `backend/metadata_extractor.py:117`

Single-token regex for organization names. Allows internal abbreviation periods` (e.g., `U.S.`, `A.B.`) but **not** sentence boundaries (a space must follow any period):

```python
r"[A-Z][\w''&\-]*(?:\.[A-Z][\w''&\-]*)*"
```

#### `_ORG_BY_RE` — `backend/metadata_extractor.py:119`

Matches `offered by X`, `funded by X`, `organized by X`, etc. Captures up to 6 tokens.

#### `_UNIVERSITY_RES` — `backend/metadata_extractor.py:128`

Two patterns for university names: `(The) X University` and `University of (the) X`.

#### `_TITLE_TYPE_NOUN_RE` — `backend/metadata_extractor.py:147`

Captures the noun phrase before a category word in the title:

```python
r"^(.{2,60}?)\s+(scholarships?|fellowships?|grants?|internships?|programmes?|programs?|awards?|prizes?|competitions?)\b"
```

E.g., `"Slovak Republic Scholarship Talented Students"` → group 1 = `"Slovak Republic Scholarship Talented Students"`, group 2 = `"Scholarship"`.

#### `_TITLE_STOPWORDS`, `_ORG_TRAILING_STOPWORDS`, `_SMALL_WORDS` — `backend/metadata_extractor.py:153–165`

Three stopword sets used by the cleaning functions. `_SMALL_WORDS` is title-cased (`of`, `the`) instead of capitalized — used to preserve correct title casing for phrases like `"University of Tokyo"`.

#### `_clean_org(candidate) -> Optional[str]` — `backend/metadata_extractor.py:168`

Normalizes a candidate organization string:

1. Collapse whitespace; strip leading articles (`the`, `a`, `an`).
2. Pop trailing stopwords (`at`, `in`, `for`, …).
3. Length check: 2–70 chars. Reject too-short or absurdly long strings.
4. If the candidate is all-lowercase, title-case it while keeping `_SMALL_WORDS` lowercase.

#### `_org_from_title(title) -> Optional[str]` — `backend/metadata_extractor.py:190`

Heuristic for titles like `"Slovak Republic Scholarship Talented Students"`:

1. Match against `_TITLE_TYPE_NOUN_RE`.
2. Strip leading/trailing `_TITLE_STOPWORDS`.
3. Keep 1–5 remaining words.
4. Return `_clean_org(...)` of the result.

The title heuristic is the **last-resort** fallback in `_extract_organization`.

#### `_extract_organization(title, content) -> Optional[str]` — `backend/metadata_extractor.py:206`

Three-tier extraction:

1. `_ORG_BY_RE` against the **first 1500 chars** of content (e.g., "offered by the Honjo Foundation").
2. `_UNIVERSITY_RES` against the first 1500 chars (e.g., "University of Tokyo").
3. `_org_from_title(title)` as fallback.

---

### 3.4 Location extraction

#### `_COUNTRY_ALIASES` — `backend/metadata_extractor.py:227`

Dict mapping common aliases to canonical country names:

```
"usa" / "u.s.a" / "america"        → "United States"
"uk" / "britain" / "england"       → "United Kingdom"
"uae"                               → "United Arab Emirates"
"korea"                             → "South Korea"
"holland" / "the netherlands"       → "Netherlands"
"slovak republic"                   → "Slovakia"
"burma"                             → "Myanmar"
"swaziland"                         → "Eswatini"
... 30+ aliases total
```

#### `_COUNTRIES` — `backend/metadata_extractor.py:243`

A list of all ~200 recognized country names (UN member states + a few common non-members).

#### `_CANONICAL_LOOKUP` — `backend/metadata_extractor.py:277`

Built by combining `_COUNTRIES` and `_COUNTRY_ALIASES`, all lowercase → canonical. Used as the lookup table.

#### `_LOCATION_TERM_RE` — `backend/metadata_extractor.py:283`

A single regex matching any country or alias, with alternatives sorted by length descending (longest first) to prefer `"United Kingdom"` over `"UK"` when both could match.

#### `_STUDY_IN_RE` — `backend/metadata_extractor.py:292`

Matches `study in X` / `study at X` / `studies in X` / `studying in X`, capturing up to 4 tokens.

#### `_canonicalize_location(text) -> Optional[str]` — `backend/metadata_extractor.py:298`

Looks up the lowercased text in `_CANONICAL_LOOKUP`. Strips a leading `"the "` and tries again. Returns `None` if no canonical match.

#### `_extract_location(title, content) -> Optional[str]` — `backend/metadata_extractor.py:312`

Four-tier extraction:

1. `_STUDY_IN_RE` against the first 3000 chars → canonicalize match.
2. `_LOCATION_TERM_RE` against the title.
3. `_LOCATION_TERM_RE` against the first 1200 chars of content.
4. Clean the `study in X` capture as a free-form location (e.g., `"Japan"` if not in the country list — though Japan is).

---

### 3.5 Type extraction

#### `_FUNDING_PATTERNS` — `backend/metadata_extractor.py:337`

```python
("Fully Funded",       re.compile(r"fully[\s-]?funded", re.IGNORECASE)),
("Partially Funded",   re.compile(r"partial(?:ly)?[\s-]?funded|partial\s+scholarship", re.IGNORECASE)),
```

#### `_CATEGORY_PATTERNS` — `backend/metadata_extractor.py:345`

11 categories in priority order:

```python
("Internship",       re.compile(r"\binternships?\b", re.IGNORECASE)),
("Fellowship",       re.compile(r"\bfellowships?\b", re.IGNORECASE)),
("Hackathon",        re.compile(r"\bhackathons?\b", re.IGNORECASE)),
("Competition",      re.compile(r"\bcompetitions?\b|\bcontests?\b", re.IGNORECASE)),
("Conference",       re.compile(r"\bconferences?\b|\bsummits?\b", re.IGNORECASE)),
("Exchange Program", re.compile(r"\bexchange\s+(?:programme|program)s?\b", re.IGNORECASE)),
("Training",         re.compile(r"\btrainings?\b|\bworkshops?\b", re.IGNORECASE)),
("Volunteering",     re.compile(r"\bvolunteer(?:ing|ship)?s?\b", re.IGNORECASE)),
("Grant",            re.compile(r"\bgrants?\b", re.IGNORECASE)),
("Award",            re.compile(r"\bawards?\b|\bprizes?\b", re.IGNORECASE)),
("Scholarship",      re.compile(r"\bscholarships?\b|\bbourses?\b", re.IGNORECASE)),
```

> **French `bourses?`** catches scholarships in French-language postings (a deliberate multilingual touch).

#### `_extract_type(title, content) -> Optional[str]` — `backend/metadata_extractor.py:363`

```python
def _extract_type(title, content):
    content_haystack = content[:1500]
    funding = next((label for label, pat in _FUNDING_PATTERNS
                    if pat.search(title) or pat.search(content_haystack)), None)
    title_category = next((label for label, pat in _CATEGORY_PATTERNS
                           if pat.search(title)), None)
    content_category = next((label for label, pat in _CATEGORY_PATTERNS
                             if pat.search(content_haystack)), None)
    category = title_category or content_category       # title wins
    if funding and category:
        return f"{funding} {category}"                 # e.g. "Fully Funded Scholarship"
    return funding or category
```

**Critical detail:** `category = title_category or content_category`. **Title-derived categories take precedence over content-derived ones** because:

- Titles are curated labels (clean, intentional).
- Body text frequently contains negated or incidental mentions (e.g., *"this is not an exchange program"*).

This priority rule is **reversed relative to naive regex extraction** and is documented in a code comment at `backend/metadata_extractor.py:381`.

---

### 3.6 Public API — Stage 1

#### `extract_metadata_rules(title, content) -> Dict[str, Optional[str]]` — `backend/metadata_extractor.py:393`

```python
def extract_metadata_rules(title: str, content: str) -> Dict[str, Optional[str]]:
    now = datetime.now()
    return {
        "deadline": _extract_deadline(title, content, now),
        "organization": _extract_organization(title, content),
        "location": _extract_location(title, content),
        "type": _extract_type(title, content),
    }
```

The one-call entry point for the rules stage. Always returns a dict with all four keys (values may be `None`). Pure synchronous, < 5 ms per call.

#### `find_missing_fields(metadata) -> List[str]` — `backend/metadata_extractor.py:404`

```python
def find_missing_fields(metadata: Optional[Dict[str, Any]]) -> List[str]:
    if not metadata:
        return list(METADATA_FIELDS)
    return [field for field in METADATA_FIELDS if not metadata.get(field)]
```

Returns the list of fields that are absent or empty. Used by:

- The scraper to decide which items need LLM enrichment.
- `_enrich()` to know which fields to fill from LLM output.

---

### 3.7 Stage 2 — LLM fallback enrichment

#### `_LLM_SYSTEM_PROMPT`, `_LLM_USER_TEMPLATE` — `backend/metadata_extractor.py:419–432`

System prompt tells the LLM to respond with *only* a JSON object. User template specifies the schema:

```python
{
    "deadline":      "ISO format YYYY-MM-DD or null",
    "organization":  "host institution/foundation or null",
    "location":      "country where the opportunity takes place or null",
    "type":          '"Fully Funded Scholarship" | "Fellowship" | "Internship" | ... or null'
}
```

Explicit `null` is the escape hatch — fields not clearly stated should be omitted as `null` rather than hallucinated.

#### `_coerce_llm_metadata(raw) -> Dict[str, Optional[str]]` — `backend/metadata_extractor.py:435`

Normalizes whatever the LLM returns:

1. For each field in `METADATA_FIELDS`:
   - If string: strip; treat `"null"`, `"none"`, `"n/a"`, `"unknown"` as `None`.
   - If int/float: convert to string.
   - Anything else (e.g., list, dict, bool): `None`.
2. **Re-parse** the deadline via `dateparser` to ensure ISO `YYYY-MM-DD` format.
3. **Length cap** — any field longer than 100 chars is dropped to `None`. Prevents garbage like `"according to the official website…"` from being accepted as `organization`.

#### `_parse_llm_json(text) -> Optional[Dict]` — `backend/metadata_extractor.py:460`

Robust JSON extraction:

1. Strip leading/trailing markdown fences (` ```json ` / ` ``` `).
2. Try `json.loads(text)` directly.
3. If that fails, regex-extract the first `{...}` block and try again.
4. Return `None` on any failure.

This handles LLMs that sometimes emit JSON wrapped in code fences or with surrounding prose.

#### `_llm_extract_one(title, content, model, timeout) -> Optional[Dict]` — `backend/metadata_extractor.py:479`

Per-item LLM call.

```python
async def _llm_extract_one(title, content, model, timeout):
    import ollama
    prompt = _LLM_USER_TEMPLATE.format(
        title=title,
        content=content[: settings.scraper.metadata_content_chars],   # 4000 chars by default
    )
    client = ollama.AsyncClient(host=settings.model.ollama_base_url)
    response = await asyncio.wait_for(
        client.chat(
            model=model,                                 # "qwen3.5:4b"
            messages=[
                {"role": "system", "content": _LLM_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            format="json",                               # ← forces JSON output
            options={"temperature": 0.0, "num_predict": 200},
        ),
        timeout=timeout,                                 # 45s default
    )
    payload = _parse_llm_json(response["message"]["content"])
    if payload is None:
        return None
    return _coerce_llm_metadata(payload)
```

| Parameter | Source | Notes |
| --------- | ------ | ----- |
| `title`, `content` | from caller | Content is **truncated** to `settings.scraper.metadata_content_chars` (4000 chars default) before sending. |
| `model` | hard-coded `"qwen3.5:4b"` | Could be made configurable via `settings`. |
| `timeout` | `settings.scraper.llm_enrichment_timeout` | 45.0 s default. Enforced via `asyncio.wait_for`. |
| `format="json"` | ollama SDK option | Asks the model to constrain its output to JSON. Combined with `temperature=0` and `num_predict=200`, this gives clean, deterministic output. |

#### `enrich_missing_metadata(entries) -> int` — `backend/metadata_extractor.py:510`

The async orchestrator for Stage 2.

```python
async def enrich_missing_metadata(entries: Sequence[Tuple[int, Dict[str, Any]]]) -> int:
    if not entries:
        return 0
    from backend.database import update_opportunity_metadata
    model = "qwen3.5:4b"
    sem = asyncio.Semaphore(settings.scraper.llm_enrichment_concurrency)  # 2
    enriched = 0

    async def _enrich(db_id: int, item: Dict[str, Any]) -> None:
        nonlocal enriched
        async with sem:                                                    # ← max 2 in flight
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
        for field in find_missing_fields(merged):                          # ← only fill gaps
            if extracted.get(field):
                merged[field] = extracted[field]
                changed = True
        if changed:
            update_opportunity_metadata(db_id, merged)
            enriched += 1

    await asyncio.gather(*(_enrich(db_id, item) for db_id, item in entries))
    logger.info("LLM metadata enrichment finished: %d/%d updated", enriched, len(entries))
    return enriched
```

| Parameter | Type | Description |
| --------- | ---- | ----------- |
| `entries` | `Sequence[Tuple[int, Dict[str, Any]]]` | Each entry is `(opportunity_db_id, item_dict)`. The item dict must have at least `name`/`title` and `content`. |
| **Returns** | `int` | Number of opportunities actually updated. |

Algorithm:
1. Initialize an `asyncio.Semaphore(2)` so at most 2 LLM calls are in flight (configurable via `settings.scraper.llm_enrichment_concurrency`).
2. For each entry, `_enrich` runs:
   1. Acquire semaphore.
   2. Call `_llm_extract_one` with a timeout.
   3. If it raises, log and return — other entries continue.
   4. Merge the LLM output into the item's existing metadata, **only filling fields that were missing**.
   5. If anything changed, persist via `update_opportunity_metadata(db_id, merged)`.
3. `asyncio.gather(...)` runs all `_enrich` coroutines concurrently (bounded by the semaphore).
4. Log and return the count of updated items.

**Merge semantics:** the LLM can return values for already-extracted fields; those are **ignored**. Only previously-missing fields are filled. This avoids the LLM overwriting a good rules-extracted value with a worse one.

---

## 4. Flow / Lifecycle

### Scrape + enrichment lifecycle

```
CombinedScraper.run_all_scrapers()       (scraper.py)
   │  for each source (youthop, greatyop, scholars4dev, …):
   │     1. Fetch sitemap / RSS / page list
   │     2. For each item: parse title + content
   │     3. metadata = extract_metadata_rules(title, content)   ← STAGE 1
   │     4. upsert_opportunities(items)   ← store in SQLite
   │
   ▼
CombinedScraper.await_enrichment()
   │  1. SELECT opportunities WHERE deadline IS NULL OR location IS NULL OR …   ← any missing
   │  2. entries = [(db_id, item_dict), …]
   │  3. asyncio.run(enrich_missing_metadata(entries))   ← STAGE 2
   │     ├─► max 2 concurrent Ollama calls
   │     ├─► fill only the missing fields
   │     └─► update_opportunity_metadata(db_id, merged)
   │
   ▼
   Database is now fully enriched (or as much as LLM could determine)
```

### Why two stages?

- **Stage 1 (rules)** runs inline in the scrape pipeline at **< 5 ms per item** — totally free. For a typical scrape run of 100 items, the total cost is < 500 ms.
- **Stage 2 (LLM)** is expensive: each call is 1–5 seconds of Ollama compute. Running it for every item would multiply scrape time by 50–100×. By gating on `find_missing_fields`, only items where rules genuinely failed get an LLM call.

This is the **"rules first, LLM only for what rules missed"** pattern that keeps the system fast while still hitting high coverage.

---

## 5. Dependencies

| Import | Used for | Why |
| ------ | -------- | --- |
| `asyncio` | `enrich_missing_metadata` (async orchestrator), `asyncio.wait_for`, `asyncio.Semaphore`, `asyncio.gather` | Async LLM calls. |
| `json` | `_parse_llm_json` (LLM response parsing) | Stdlib JSON. |
| `logging` | `logger = logging.getLogger(__name__)` | Per-module logger. |
| `re` | All regex compilation | Multilingual keyword/date/category patterns. |
| `datetime.{datetime, timedelta}` | `now` parameter, plausibility window | Deadline sanity bounds. |
| `typing.{Any, Dict, List, Optional, Sequence, Tuple}` | Static typing | Public API. |
| `dateparser` | Fuzzy date parsing (multilingual) | Industry-standard; handles `"8 Sept 2026"`, `"Sept 8"`, `"08/09/2026"`, etc. |
| `config.settings` | `settings.scraper.metadata_content_chars`, `settings.scraper.llm_enrichment_concurrency`, `settings.scraper.llm_enrichment_timeout`, `settings.model.ollama_base_url` | Config injection. |
| `ollama` (imported inside `_llm_extract_one`) | `AsyncClient(host=...).chat(model=..., format="json", ...)` | Async Ollama SDK; deferred import keeps module importable without ollama installed. |
| `backend.database.update_opportunity_metadata` (imported inside `enrich_missing_metadata`) | Persist enriched metadata | Avoids circular import at module top. |

---

## 6. Models & External Services

| Component | Detail |
| --------- | ------ |
| LLM | `qwen3.5:4b` via Ollama, hard-coded at `backend/metadata_extractor.py:527`. |
| LLM server | `settings.model.ollama_base_url` (default `http://localhost:11434`). |
| Output format | JSON, enforced via `format="json"` Ollama option. |
| Concurrency | `asyncio.Semaphore(settings.scraper.llm_enrichment_concurrency)` (default 2 in flight). |
| Timeout | `settings.scraper.llm_enrichment_timeout` (default 45 s per call). |
| Content cap | `settings.scraper.metadata_content_chars` (default 4000 chars sent to LLM). |
| Persistence | `backend.database.update_opportunity_metadata(db_id, merged_dict)` writes to SQLite. |

---

## 7. Notable Algorithms

### 7.1 Sentence-scoped deadline detection

Rather than scanning the entire document for dates, `_extract_deadline` first splits the content into sentences and only looks at sentences that contain a deadline keyword. This:

- Avoids picking up "test dates" or "founding date" unrelated to the deadline.
- Allows the same body to contain multiple date candidates and picks the **latest plausible** one — handles "deadline extended to …" gracefully.

### 7.2 Plausibility window filter

`now - 365 days ≤ parsed ≤ now + 3 years` filters out:

- Historical dates mentioned in passing (`"since 2021 we have …"`).
- Far-future typos (`"applications open in 2050"`).

The asymmetric window (1 year back, 3 years forward) reflects scholarship cycles, which often have ≥12-month forward visibility for the next academic year.

### 7.3 Title-priority category resolution

`category = title_category or content_category` in `_extract_type`. This single line of code eliminates a huge class of false positives because:

- Titles are short, curated, and intentional.
- Body text is verbose, contains negated phrases, comparative references, and historical context.

### 7.4 Country alias normalization

The `_COUNTRY_ALIASES` map + `_CANONICAL_LOOKUP` flatten the long tail of "USA vs United States vs America vs U.S.A." into a single canonical name. This is critical for deduplication and aggregation — without it, the system would record the same opportunity multiple times under different `location` values.

### 7.5 Length-bounded coercion

`_coerce_llm_metadata` rejects any field longer than 100 chars. This is a hard guard against the LLM returning prose instead of a value (e.g., `organization = "according to the official website of …"`). Short, atomic values only.

### 7.6 Semaphore-bounded async fan-out

`enrich_missing_metadata` uses `asyncio.Semaphore(llm_enrichment_concurrency)` to bound concurrent Ollama calls. With the default of 2, even 100 items complete in roughly `ceil(100/2) × 5s = 250s`, and we never saturate the LLM server.

### 7.7 Gap-filling merge

`merged[field] = extracted[field]` is only applied when `field` is in `find_missing_fields(merged)`. This means:

- Rules-extracted values are never overwritten.
- LLM is used purely as a fallback.

The semantic is "rules wins, LLM backfills" — predictable and safe.

### 7.8 Multilingual month names

The `_MONTHS` regex contains English, French, Spanish, and German month names. Combined with `dateparser`'s `PREFER_DATES_FROM=future` and `DATE_ORDER=DMY`, deadlines from European postings parse cleanly.

---

## 8. Error Handling

| Failure | Behavior |
| ------- | -------- |
| `_parse_date_candidate` returns `None` | Deadline candidate silently skipped. |
| `_extract_deadline` finds nothing | Returns `None`. |
| `_clean_org` rejects length/format | Returns `None`; tier falls through. |
| `_canonicalize_location` finds nothing | Returns `None`. |
| `extract_metadata_rules` on bad input | **Never crashes** — always returns a dict with all 4 keys (possibly all `None`). Verified by `tests/test_metadata_extractor.py:155`. |
| LLM call timeout (`asyncio.TimeoutError`) | `except Exception` in `_enrich` catches; logs warning; entry skipped. Other entries continue. |
| LLM returns invalid JSON | `_parse_llm_json` returns `None`; entry skipped. |
| LLM returns garbage values | `_coerce_llm_metadata` coerces strings; rejects long values; treats literal `"null"`/`"none"`/`"n/a"`/`"unknown"` as `None`. |
| `update_opportunity_metadata` raises | Propagates up. **Not caught** — would fail the whole gather. |
| `asyncio.gather` cancellation | Default behavior; partial results persisted so far. |
| Empty `entries` list | Returns `0` immediately, no work done. |

The rules stage is **bulletproof** (it never raises). The LLM stage is **best-effort** — failures on individual items don't fail the batch.

---

## 9. Notable Patterns & Design Decisions

1. **Two-stage hybrid extraction.** Rules first (free, deterministic, < 5 ms), LLM second (expensive, async, ~5 s per item) — only when rules left gaps. This is the same pattern used by production metadata pipelines at scale.

2. **Sentence-scoped pattern matching.** Regex-based extraction looks at sentences containing keyword hints, not raw text. This dramatically reduces false positives compared to naive "find any date in the body" approaches.

3. **Title-priority type categorization.** `category = title_category or content_category` is the most important single-line decision in the file — it's the difference between "Fully Funded Scholarship" and "Award" (from the phrase "this prestigious award…").

4. **Canonical country names with aliases.** The `_CANONICAL_LOOKUP` map makes the system robust to the messy reality of how countries are named in scraped content (`"USA"` vs `"United States"` vs `"U.S.A"` vs `"America"`).

5. **Plausibility window for dates.** `now - 365 days ≤ parsed ≤ now + 3 years` rejects both stale historical dates and suspicious future dates. Demonstrated by the test case `"test dates from 1 January 2021"` not being mistaken for the deadline (`tests/test_metadata_extractor.py:53`).

6. **Multilingual support.** `_MONTHS` regex includes French, Spanish, German month names; `_CATEGORY_PATTERNS` includes French `bourses?`. Combined with `dateparser`, this works on European postings without any extra code.

7. **Length-bounded LLM output.** `_coerce_llm_metadata` rejects fields > 100 chars. Catches the common LLM failure mode of "instead of a value, return a sentence explaining what the value would be".

8. **Gap-filling merge.** The LLM never overwrites a good rules-extracted value. The merge logic iterates `find_missing_fields(merged)` so only `None` slots get filled.

9. **Concurrency-bounded LLM fan-out.** `asyncio.Semaphore(2)` ensures the LLM server isn't overwhelmed even for 100+ item batches.

10. **Defer-imports for optional deps.** `import ollama` happens inside `_llm_extract_one`; `from backend.database import update_opportunity_metadata` happens inside `enrich_missing_metadata`. This avoids circular imports and keeps the rules stage usable without ollama installed.

11. **`format="json"` Ollama option.** Most modern Ollama-supported models (including qwen3.5:4b) honor this constraint and emit clean JSON. Combined with `temperature=0` and `num_predict=200`, output is highly deterministic.

12. **JSON parse with regex fallback.** `_parse_llm_json` handles three cases: clean JSON, JSON-in-fences, JSON-embedded-in-prose. The regex fallback (`re.search(r"\{.*\}", text, re.DOTALL)`) recovers from sloppy model output.

13. **`temperature=0.0` for LLM enrichment.** Deterministic output is critical here — we want the same input to always produce the same metadata, so duplicate detection in the database works correctly.

14. **Logger over print.** Uses `logging.getLogger(__name__)` so log levels can be configured globally (`config.settings.debug` toggles the level elsewhere).

---

## Cross-references

- Caller (rules stage): `scrapers/base.py` + each scraper in `scrapers/*.py` invokes `extract_metadata_rules(title, content)` inline.
- Caller (LLM stage): `scraper.py::CombinedScraper.await_enrichment()` calls `enrich_missing_metadata(entries)` after a successful scrape run.
- Persistence: `backend/database.py::update_opportunity_metadata(db_id, merged)` writes to the `opportunities` table's `metadata_json` column.
- Settings: `config.py:13–17` — `extract_metadata`, `llm_enrichment`, `llm_enrichment_concurrency`, `llm_enrichment_timeout`, `metadata_content_chars`.
- Tests: `tests/test_metadata_extractor.py` (161 lines) covers deadline edge cases (extended dates, US-style dates, no-year dates, historical rejection), organization patterns (offered-by, funded-by, title heuristic), location (study-in, alias matching, head matching), type (priority ordering, fully-funded combination), missing-fields detection, and never-crash invariants.