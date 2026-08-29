# Scraper Orchestrator (`scraper.py`)

## 1. Purpose & Overview

`scraper.py` is the **top-level entry point** of the Opportunities-Details scraping pipeline. It is a CLI-style script that:

1. Instantiates **six site-specific scrapers** (YouthOP, GreatYop, Scholars4Dev, ScholarshipsCorner, OpportunitiesCorners, OpportunitiesForYouth).
2. Runs them **concurrently** with per-scraper timeouts using `asyncio.gather`.
3. **Aggregates** the per-site results into a single combined list.
4. Persists the raw combined list to `scraped_data.txt`.
5. **Hands off** the data to downstream stages: rules-based metadata extraction, SQLite upsert, and (optionally) background LLM enrichment.
6. Prints a human-readable summary and returns the combined results.

It is the single script invoked by an operator or scheduler (`python scraper.py`) to refresh the opportunity index.

---

## 2. Architecture

The orchestrator sits at the top of the pipeline:

```
                ┌──────────────────────────────┐
                │  python scraper.py           │
                │  (asyncio.run(main()))       │
                └───────────────┬──────────────┘
                                │
                  ┌─────────────▼──────────────┐
                  │   CombinedScraper          │
                  │   .run_all_scrapers()      │
                  └─────────────┬──────────────┘
                                │
   ┌────────────┬───────────────┼───────────────┬───────────────┬──────────────┐
   ▼            ▼               ▼               ▼               ▼              ▼
YouthOP     GreatYopScraper  Scholars4Dev  ScholarshipsCorner OppCorners    OppForYouth
   │            │               │               │               │              │
   └────────────┴───────────────┴───────────────┴───────────────┴──────────────┘
                                │
                  combined_results = concat of all lists
                                │
        ┌───────────────────────┼────────────────────────────┐
        ▼                       ▼                            ▼
scraped_data.txt      backend.metadata_extractor       backend.database
(JSON dump)           .extract_metadata_rules          .upsert_opportunities
                      (inline, per-item)               (SQLite)
                                │
                                ▼
              asyncio.create_task(enrich_missing_metadata)  ← optional LLM
```

### Async pipeline flow

- `asyncio.gather` (`scraper.py:57`) fans out **6 concurrent coroutines**, one per scraper.
- Each scraper is wrapped in `asyncio.wait_for` with site-specific timeouts:
  - YouthOP → **70s** (`scraper.py:58`)
  - GreatYop → **60s** (`scraper.py:59`)
  - Scholars4Dev → **60s** (`scraper.py:60`)
  - ScholarshipsCorner → **60s** (`scraper.py:61`)
  - OpportunitiesCorners → **60s** (`scraper.py:62`)
  - OpportunitiesForYouth → **60s** (`scraper.py:63`)
- `return_exceptions=True` (`scraper.py:64`) ensures that one failing scraper does not abort the entire pipeline.
- After gathering, each result is **individually inspected** for `Exception` (`scraper.py:67-89`); exceptions are logged via `icecream.ic` and replaced with `[]` so downstream concatenation is safe.

---

## 3. Key Classes / Functions

### `class CombinedScraper`

**Location:** `scraper.py:12`

#### `__init__(self, days_back=settings.scraper.days_back, threshold=settings.scraper.score_threshold)`

| Parameter    | Type    | Default                                  | Description                                                |
|--------------|---------|------------------------------------------|------------------------------------------------------------|
| `days_back`  | `int`   | `settings.scraper.days_back`             | Recency window used by every site scraper (e.g. 30 days).  |
| `threshold`  | `float` | `settings.scraper.score_threshold`       | Jaccard similarity threshold for in-site deduplication.     |

Attributes:

- `self.days_back` — propagated to each scraper.
- `self.threshold` — propagated to each scraper.
- `self.enrichment_task` — optional handle to a background LLM enrichment coroutine (`scraper.py:16`, set at `scraper.py:127`).

#### `async run_all_scrapers(self) -> list[dict]`

**Location:** `scraper.py:18`

End-to-end pipeline orchestrator. Does not take arguments; configuration is read from the constructor and `config.settings`. Returns the **combined list of opportunity dicts** (also written to `scraped_data.txt`).

Steps:

1. **Construct scrapers** with `index_url` from `settings.scraper.urls[...]` (`scraper.py:24-53`):
   - `youthop` → `YouthOP`
   - `greatyop` → `GreatYopScraper`
   - `scholars4dev` → `Scholars4Dev`
   - `scholarshipscorner` → `ScholarshipsCorner`
   - `opportunitiescorner` → `OpportunitiesCorners`
   - `opportunitiesforyouth` → `OpportunitiesForYouth`
2. **Concurrent fetch** (`scraper.py:57-65`) with per-scraper timeouts.
3. **Exception shielding** (`scraper.py:67-89`) — failed scrapers are logged and replaced with `[]`.
4. **Concatenate** results into `combined_results` (`scraper.py:92`).
5. **Persist raw JSON** to `scraped_data.txt` (`scraper.py:96-97`).
6. **Inline metadata extraction** — only if `settings.scraper.extract_metadata` is truthy (`scraper.py:100-106`):
   ```python
   from backend.metadata_extractor import extract_metadata_rules
   for item in combined_results:
       item["metadata"] = extract_metadata_rules(
           item.get("name") or item.get("title") or "",
           item.get("content") or "",
       )
   ```
7. **SQLite upsert** (`scraper.py:108-115`) — calls `backend.database.upsert_opportunities(combined_results)` inside a `try/except`. On failure logs the error but does not raise. The returned list of row IDs is kept in `row_ids`.
8. **Background LLM enrichment** — only if `settings.scraper.llm_enrichment` is truthy **and** `row_ids` is non-empty (`scraper.py:118-127`):
   - Filters for items with missing metadata fields via `find_missing_fields`.
   - Schedules `asyncio.create_task(enrich_missing_metadata(incomplete))`.
   - The task handle is stored in `self.enrichment_task` so it can be awaited later by `await_enrichment()`.
9. **Summary report** printed to stdout (`scraper.py:129-142`).
10. Returns `combined_results`.

#### `async await_enrichment(self) -> None`

**Location:** `scraper.py:146`

Awaits `self.enrichment_task` if it was scheduled. Catches and logs any exception so the caller never sees a raised error from the enrichment stage. Resets `self.enrichment_task = None` in the `finally` block.

### `async def main()`

**Location:** `scraper.py:156`

Builds a `CombinedScraper(days_back=30, threshold=0.7)` and calls:

```python
results = await scraper.run_all_scrapers()
await scraper.await_enrichment()
return results
```

The hard-coded values (30, 0.7) match the `settings.scraper.*` defaults but are passed explicitly here.

### Module-level entry point

```python
if __name__ == "__main__":
    asyncio.run(main())
```
**Location:** `scraper.py:165-166`. Standard Python script entry; runs `main()` to completion and exits.

---

## 4. Flow / Lifecycle (CLI → Indexed Data)

A single `python scraper.py` invocation walks through this sequence:

1. **Process startup** — `asyncio.run(main())` creates an event loop (`scraper.py:166`).
2. **CombinedScraper construction** — `days_back=30`, `threshold=0.7` (`scraper.py:157-160`).
3. **Scraper instantiation** — six concrete scraper objects are constructed with URLs from `settings.scraper.urls` (`scraper.py:24-53`).
4. **Concurrent fetch (Stage 1: site aggregation)** — `asyncio.gather` runs all six `getting_data()` coroutines concurrently, each wrapped in `asyncio.wait_for` (`scraper.py:57-65`).
   - Each site scraper independently:
     - Discovers recent URLs from its sitemap.
     - Jaccard-dedups within the site.
     - Fetches each page body.
     - Extracts main text via `trafilatura`.
     - Returns `[{name, url, content}, ...]`.
5. **Exception shielding** — `scraper.py:67-89` replaces any raised exception with `[]` after logging.
6. **Combine + dump** — All lists are concatenated and the raw JSON written to `scraped_data.txt` (`scraper.py:92-97`).
7. **Inline rules-based metadata extraction** — If enabled, each item gets a `metadata` field from `extract_metadata_rules` (`scraper.py:100-106`).
8. **SQLite upsert** — `backend.database.upsert_opportunities(combined_results)` is called inside a guarded `try/except`; on success returns row IDs (`scraper.py:108-115`).
9. **Optional background LLM enrichment** — If enabled **and** there are rows with missing metadata, a background `asyncio.create_task(enrich_missing_metadata(...))` is launched (`scraper.py:118-127`).
10. **Summary printout** — Per-source item counts + total printed to stdout (`scraper.py:129-142`).
11. **Enrichment await** — Back in `main()`, `await scraper.await_enrichment()` blocks until the LLM enrichment task finishes or fails (`scraper.py:162`).
12. **Return** — `main()` returns `combined_results`; the event loop closes.

End state: `scraped_data.txt` contains the raw JSON dump and the SQLite database contains the upserted rows (with metadata filled in where the LLM enrichment succeeded).

---

## 5. Concurrency Model

- **6-site fan-out** via `asyncio.gather` (`scraper.py:57-65`).
- **Per-site concurrency** is delegated to each scraper (varies — see `docs/scrapers_individual.md`):
  - YouthOP: `TCPConnector(limit=15, limit_per_host=10)` + 0.1s pre-fetch sleep + 60s task deadline.
  - ScholarshipsCorner: `limit=10, limit_per_host=10` + 1.0s sleep per URL.
  - Scholars4Dev: `limit=1, limit_per_host=1` (effectively sequential) + 1.0s sleep.
  - OpportunitiesCorners: `limit=20, limit_per_host=7`, no per-URL sleep.
  - OpportunitiesForYouth: `limit=1, limit_per_host=1` + staggered `index * 2.0` second sleep.
  - GreatYop: `limit=10, limit_per_host=10` + 1.0s sleep.
- **Hard ceiling** on total orchestrator wait: the slowest site-bound is YouthOP at **70s**; all other sites are capped at **60s**.

---

## 6. Deduplication

**In-site** deduplication is handled by each scraper individually using Jaccard similarity over URL slugs — see `docs/scrapers_base.md` §6 and `docs/scrapers_individual.md` for the per-site implementation.

**Cross-site deduplication** is **not performed** in `scraper.py`; it is left to the SQLite layer (unique URL constraint or downstream consolidation).

---

## 7. Metadata Extraction Handoff

After the scrape-and-persist step, `scraper.py` performs **two-phase metadata extraction** (gated by config flags in `config.settings`):

### Phase 1 — Inline rules-based extraction (`scraper.py:100-106`)

```python
from backend.metadata_extractor import extract_metadata_rules
for item in combined_results:
    item["metadata"] = extract_metadata_rules(
        item.get("name") or item.get("title") or "",
        item.get("content") or "",
    )
```

- Synchronous, fast, deterministic.
- Operates on `(name, content)` from each scraped item.
- Writes the result back into the item's `metadata` field before SQLite upsert.

### Phase 2 — Background LLM enrichment (`scraper.py:118-127`)

Only runs if `settings.scraper.llm_enrichment` is `True` and `row_ids` is non-empty.

```python
incomplete = [
    (row_id, item)
    for row_id, item in zip(row_ids, combined_results)
    if row_id != -1 and find_missing_fields(item.get("metadata"))
]
if incomplete:
    self.enrichment_task = asyncio.create_task(enrich_missing_metadata(incomplete))
```

- `find_missing_fields` returns truthy when required metadata slots are absent.
- `enrich_missing_metadata` is an LLM-driven async coroutine that fills in the gaps.
- The task is **fire-and-forget** from the orchestrator's perspective, but `await_enrichment()` is called from `main()` (`scraper.py:162`) to ensure completion before process exit.

---

## 8. Error Handling & Retries

### Per-site timeouts
Each `asyncio.wait_for` in `scraper.py:57-63` raises `asyncio.TimeoutError` if the scraper exceeds its budget; this is caught implicitly by `return_exceptions=True` and then replaced with `[]` (`scraper.py:67-89`).

### Cross-site isolation
A failing scraper (timeout, HTTP error, parse error, etc.) only loses its own data; the other five scrapers continue. Each failure is logged with `icecream.ic`.

### Database write protection
```python
try:
    from backend.database import upsert_opportunities
    row_ids = upsert_opportunities(combined_results)
except Exception as err:
    ic.ic(f"Error saving to SQLite database: {err}")
```
**Location:** `scraper.py:110-115`. The `try/except` ensures a database failure does not abort the script; `row_ids` is left empty (its initial value) so the optional enrichment step is skipped.

### Enrichment error handling
`await_enrichment()` wraps the await in `try/except` (`scraper.py:148-153`), logging via `icecream.ic` and never raising to the caller.

### Logging
- All logging goes through `icecream.ic` (debug-style printer). No `logging` calls in this file.
- Inheriting `BaseScraper` users get Python `logging` via `scrapers/base.py:18` (`logger = logging.getLogger(__name__)`).

---

## 9. Performance Characteristics

| Metric                                      | Value                                                              |
|---------------------------------------------|--------------------------------------------------------------------|
| Total concurrent scrapers                   | 6                                                                  |
| YouthOP outer timeout                       | **70s** (`scraper.py:58`)                                          |
| All other sites outer timeout               | **60s** (`scraper.py:59-63`)                                       |
| Expected full-pipeline wall-clock           | **~70 seconds** in the worst case (limited by YouthOP's ceiling)   |
| Concurrency inside each scraper             | Varies: 1 → 20 (`TCPConnector(limit=...)` per scraper)              |
| File I/O                                    | One JSON dump to `scraped_data.txt` (`scraper.py:96-97`)           |
| Database write                              | One bulk `upsert_opportunities` call                               |
| Background enrichment                       | Async task; main awaits it via `await_enrichment()` before exit    |
| Bottleneck                                  | Network I/O + remote site response latency                          |

### Tunables (via `config.settings.scraper.*`)

- `days_back` — recency window.
- `score_threshold` — Jaccard threshold for in-site dedup.
- `urls` — per-site URL map.
- `extract_metadata` — toggles Phase 1 metadata extraction.
- `llm_enrichment` — toggles Phase 2 LLM enrichment.

### Failure-mode performance
If any scraper fails, the pipeline still completes within ~70s. The orphan scraper's data is lost; the other five still produce output.