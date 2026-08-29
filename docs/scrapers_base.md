# Base Scraper (`scrapers/base.py`)

## 1. Purpose & Overview

`scrapers/base.py` defines the **abstract scaffolding** that future, refactored site scrapers should inherit from. It provides:

- A standardized **`aiohttp` session lifecycle** (lazy creation + auto-close).
- A `_retry` helper with **exponential backoff + jitter** for transient HTTP errors.
- A `BaseScraper` class with the standard **index → URL discovery → fetch → parse → normalize** pipeline.
- A `getting_data()` orchestrator that runs the per-page fetch/parse loop under an `asyncio.Semaphore` for bounded concurrency.
- A small helper, `extract_links_from_sitemap`, for sitemap XML parsing.

> **Important note:** As of the current codebase, the **five active site scrapers** (`youthop.py`, `scholars4dev.py`, `scholarshipscorner.py`, `opportunitiesforyouth.py`, `opportunitiescorner.py`, `greatyop.py`) **do not inherit** from `BaseScraper`. They implement the same pattern by hand. `BaseScraper` is the canonical reference and the intended direction for future refactors; it is re-exported via `scrapers/__init__.py:2`.

---

## 2. Architecture

```
scraper.py  ──►  Individual site scraper (e.g. YouthOP)
                    │
                    ├── fetches sitemap ──► discover URLs ──► Jaccard dedup (in-site)
                    │                                                 │
                    │                                                 ▼
                    └─────────────────────── for each unique URL ──► _get()
                                                                         │
                                                          retry/backoff via _retry()
                                                                         │
                                                                         ▼
                                                                parse_page() → dict
                                                                         │
                                                                         ▼
                                                                normalize() → final dict
```

The pipeline implemented by `BaseScraper.getting_data()` (`scrapers/base.py:131`) follows this canonical order:

1. **Ensure session** (lazy create if needed).
2. **Fetch index** via `fetch_index()`.
3. **Parse index** via `parse_index(index_content)` to obtain an iterable of URLs.
4. **Fetch + parse each URL** under a concurrency `Semaphore`.
5. **Filter & normalize** results.
6. **Close session** (in `finally`).

---

## 3. Key Classes / Functions

### `async def _retry(coro_func, *args, max_retries=3, backoff_factor=0.5, **kwargs)`

**Location:** `scrapers/base.py:21`

A coroutine-safe retry helper with **exponential backoff + jitter**.

| Parameter      | Type            | Default | Description                                                                  |
|----------------|-----------------|---------|------------------------------------------------------------------------------|
| `coro_func`    | `Callable`      | —       | Async function to call on each attempt.                                      |
| `*args`        | positional      | —       | Forwarded to `coro_func`.                                                    |
| `max_retries`  | `int`           | `3`     | Total attempts (including the first).                                        |
| `backoff_factor`| `float`        | `0.5`   | Base wait in seconds; the actual wait is `backoff_factor * (2 ** (n-1)) + jitter`. |
| `**kwargs`     | keyword         | —       | Forwarded to `coro_func`.                                                    |

Returns whatever `coro_func(...)` returns. Re-raises the final exception after `max_retries` is exhausted.

**Backoff formula** (`scrapers/base.py:39-40`):

```
wait = backoff_factor * (2 ** (attempt - 1))
wait = wait + random.uniform(0, wait * 0.1)
```

### `class BaseScraper`

**Location:** `scrapers/base.py:45`

A reusable base class. Subclasses **must** override `parse_index` and `parse_page` (both raise `NotImplementedError`).

#### Class attribute
- `name: str = "base"` (`scrapers/base.py:53`) — identifier used in log messages.

#### `__init__(self, index_url, days_back=30, threshold=0.7, session=None, timeout=30, max_retries=3, concurrency=8)`

| Parameter    | Type                       | Default | Description                                                                                          |
|--------------|----------------------------|---------|------------------------------------------------------------------------------------------------------|
| `index_url`  | `str`                      | —       | URL of the index/sitemap that lists post URLs.                                                       |
| `days_back`  | `int`                      | `30`    | Recency window for filtering URLs.                                                                   |
| `threshold`  | `float`                    | `0.7`   | Jaccard threshold used by subclasses for in-site dedup.                                              |
| `session`    | `aiohttp.ClientSession?`   | `None`  | Optional shared session. If `None`, the scraper creates and closes its own (`self._own_session=True`). |
| `timeout`    | `int`                      | `30`    | Per-request `aiohttp.ClientTimeout(total=...)`.                                                      |
| `max_retries`| `int`                      | `3`     | Retry budget passed through to `_retry`.                                                             |
| `concurrency`| `int`                      | `8`     | Max parallel page fetches via `asyncio.Semaphore`.                                                   |

#### Default headers (`scrapers/base.py:74-78`)

```python
{
    "User-Agent": (
        "Mozilla/5.0 (compatible; Opportunities-Details/1.0; +https://example.com)"
    )
}
```

A polite, identifying UA string. Site-specific scrapers typically override with a more browser-like UA.

#### Subclass hooks

| Method                                | Location                  | Required? | Purpose                                                                |
|---------------------------------------|---------------------------|-----------|------------------------------------------------------------------------|
| `async fetch_index()`                 | `scrapers/base.py:81`     | Optional  | Default implementation calls `self._get(self.index_url)`. Override for custom index retrieval. |
| `async parse_index(index_content)`    | `scrapers/base.py:88`     | **Yes**   | Yield site page URLs from the fetched index content.                  |
| `async parse_page(page_content, url)` | `scrapers/base.py:95`     | **Yes**   | Return a normalized dict for the page, or `None` to skip.             |
| `async normalize(item)`               | `scrapers/base.py:102`    | Optional  | Post-processing hook; default returns the item unchanged.              |

#### Internal helpers

##### `_ensure_session(self) -> aiohttp.ClientSession`

**Location:** `scrapers/base.py:107`. Lazily creates a session if `self._session is None`. Sets `self._own_session = True` so `_close_session` will dispose of it.

##### `_close_session(self) -> None`

**Location:** `scrapers/base.py:114`. Closes the session if the scraper created it.

##### `_get(self, url) -> str`

**Location:** `scrapers/base.py:119`. Fetches a URL via the session, raising on non-2xx, then runs the result through `_retry(...)` with `max_retries=self.max_retries, backoff_factor=0.5`.

#### `async getting_data(self) -> list[dict]`

**Location:** `scrapers/base.py:131`

The high-level pipeline. The body of the coroutine is wrapped in `try/finally` so the session is always closed.

```
ensure session
try:
    index_content = await fetch_index()
    urls = list(await parse_index(index_content))
    sem = asyncio.Semaphore(self.concurrency)
    tasks = [
        asyncio.create_task(_fetch_and_parse(u))
        for u in urls
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    items = [r for r in results if isinstance(r, dict)]
    return items
finally:
    await _close_session()
```

The inner `_fetch_and_parse(u)` (defined as a closure at `scrapers/base.py:145-154`):

- Acquires the semaphore slot.
- Calls `_retry(self._get, u, max_retries=self.max_retries, backoff_factor=0.5)` to fetch with retries.
- Awaits `self.parse_page(text, u)`.
- If non-`None`, awaits `self.normalize(parsed)`.
- On any exception, logs via `logger.exception` and returns `None`.

### `def extract_links_from_sitemap(xml_text: str) -> list[str]`

**Location:** `scrapers/base.py:166`

Parses a sitemap XML/text payload with BeautifulSoup's `lxml-xml` parser and returns the text contents of every `<loc>` element.

```python
def extract_links_from_sitemap(xml_text: str) -> List[str]:
    soup = BeautifulSoup(xml_text, "lxml-xml")
    return [loc.text.strip() for loc in soup.find_all("loc") if loc.text]
```

This is currently unused by the in-tree site scrapers (they hand-roll the sitemap walk) but is provided as a convenience for future subclasses.

---

## 4. Flow / Lifecycle

When a hypothetical subclass `X` is plugged into `BaseScraper`:

1. Operator runs `python scraper.py`.
2. `CombinedScraper.run_all_scrapers()` constructs `X(index_url=..., days_back=..., threshold=...)`.
3. `X.getting_data()` (inherited) runs:
   - `_ensure_session()` opens an `aiohttp.ClientSession(timeout=30, headers={UA})`.
   - `fetch_index()` retrieves the sitemap XML/HTML.
   - `parse_index()` is called with the index body. It must return an iterable of post URLs.
   - An `asyncio.Semaphore(self.concurrency)` caps in-flight page fetches.
   - For each URL: `_get(url)` (with retries) → `parse_page(text, url)` → optional `normalize(item)`.
   - Failures are caught per-task, logged, and result in `None` (filtered out).
   - Final list of dicts is returned.
   - `_close_session()` disposes of the session in `finally`.

---

## 5. Concurrency Model

- **Cross-scraper concurrency** is the orchestrator's responsibility (`asyncio.gather` in `scraper.py`).
- **Within one scraper**, `BaseScraper` provides bounded concurrency via `asyncio.Semaphore(self.concurrency)` (default 8).
- The semaphore is acquired inside the `_fetch_and_parse` closure at `scrapers/base.py:146` so that no more than `concurrency` page requests are in-flight at once.
- Per-request retries (`max_retries=3`) are sequential within a single page fetch; they do not consume additional semaphore slots.

---

## 6. Deduplication

`BaseScraper` **does not implement Jaccard deduplication** itself — that is delegated to subclasses (or, in practice, to the per-site scrapers that have not yet been migrated to this base class).

The pattern used by the in-tree scrapers is:

```python
@staticmethod
def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a or b) else 0.0

@staticmethod
def slugify_links(u: str) -> str:
    seg = u.split("/")[-1]
    s   = slugify(seg)
    return re.sub(r'-(\d{4}|\d{4}-\d{2}-\d{2})$', '', s)

# in process():
for link, tokens in zip(self.normalized, self.slug_tokens):
    if not any(self.jaccard(tokens, prev) >= self.threshold for prev in seen_tokens):
        self.unique_urls.append(link)
        seen_tokens.append(tokens)
    else:
        self.duplicates.append(link)
```

The Jaccard similarity compares **slug token sets** (the slug of the URL path, lowercased, stripped of trailing dates) — see `docs/scrapers_individual.md` for the per-site instantiation.

`BaseScraper` exposes the `threshold` constructor argument so subclasses can adopt the same convention. It does **not** enforce a particular dedup algorithm — `normalize()` is the suggested extension point.

---

## 7. Metadata Extraction Handoff

`BaseScraper` itself does **not** invoke `metadata_extractor`. The handoff happens in the **top-level orchestrator** (`scraper.py:100-127`) **after** every scraper has returned. See `docs/scraper_orchestrator.md` §7 for the full two-phase process.

---

## 8. Error Handling & Retries

### Per-request retries (`_retry`, `scrapers/base.py:21`)

- Wraps any exception from `coro_func`.
- Exponential backoff: `backoff_factor * 2 ** (attempt - 1)` seconds, plus up to 10% jitter.
- Re-raises after `max_retries` attempts.
- Logs retries at `DEBUG` level via `logger.debug` (`scrapers/base.py:36, 41`).

### Per-page error handling (`_fetch_and_parse`, `scrapers/base.py:145-154`)

- Wraps every `_get → parse_page → normalize` chain in `try/except`.
- Logs failures via `logger.exception("%s: error fetching/parsing %s: %s", ...)`.
- Returns `None` on failure; `asyncio.gather(..., return_exceptions=True)` keeps other tasks alive.

### Session lifecycle safety (`scrapers/base.py:162-163`)

- `getting_data` uses `try/finally` to guarantee `_close_session()` runs even if an unexpected exception escapes the inner pipeline.

### Logging

- `logger = logging.getLogger(__name__)` (`scrapers/base.py:18`).
- INFO messages on start, URL count, and finish (`scrapers/base.py:136, 141, 160`).

---

## 9. Performance Characteristics

| Metric                                | Value                                                                |
|---------------------------------------|----------------------------------------------------------------------|
| Default per-page timeout              | **30s** (`scrapers/base.py:61`)                                      |
| Default retry attempts                | **3** (`scrapers/base.py:62`)                                         |
| Default in-flight concurrency         | **8** (`scrapers/base.py:63`)                                         |
| Backoff base                          | **0.5s**, exponential, up to 10% jitter                              |
| Session reuse across requests         | Yes (single `aiohttp.ClientSession` per `getting_data` invocation)  |
| Connection pooling                    | Default `aiohttp` TCPConnector (no explicit limits here)             |

### Notes

- `BaseScraper` is the most conservative of the in-tree scrapers; the existing per-site scrapers tune these knobs to be more aggressive (Scholars4Dev → sequential with 1 connection) or more relaxed (OpportunitiesCorners → 20/7) depending on the host's tolerance.
- The base class's `concurrency=8` is a safe default that has not yet been benchmarked in production by any subclass.