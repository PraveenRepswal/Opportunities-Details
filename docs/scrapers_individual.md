# Individual Site Scrapers (`scrapers/*.py`)

This document covers every site-specific scraper in the `scrapers/` directory. All six currently-active scrapers share a common shape — they are **hand-rolled, do not inherit from `BaseScraper`** (despite `scrapers/base.py` existing), and each follows the same in-house pipeline:

```
sitemap_index.xml
    └─► pick the latest post-sitemap{N}.xml
            └─► filter URLs by <lastmod> within `days_back`
                    └─► normalize + slugify + tokenize
                            └─► Jaccard dedup against `seen_tokens` at `threshold`
                                    └─► trafilatura.extract() on each page body
                                            └─► dict {name, url, content}
```

Every scraper accepts `(index_url, days_back, threshold)` and exposes a single async entry point: `getting_data()`.

A summary table is at the bottom; per-scraper details follow.

---

## Table of Contents

1. [YouthOP](#1-youthop--scrapersyouthoppy)
2. [Scholars4Dev](#2-scholars4dev--scrapersscholars4devpy)
3. [ScholarshipsCorner](#3-scholarshipscorner--scrapersscholarshipscornerpy)
4. [GreatYop](#4-greatyop--scrapersgreatyoppy)
5. [OpportunitiesCorners](#5-opportunitiescorners--scrapersopportunitiescornerpy)
6. [OpportunitiesForYouth](#6-opportunitiesforyouth--scrapersopportunitiesforyouthpy)

---

## 1. YouthOP — `scrapers/youthop.py`

### Class: `YouthOP` (`scrapers/youthop.py:13`)

**Site:** https://www.youthop.com  
**Index URL pattern:** `https://www.youthop.com/sitemap_index.xml`  
**Sitemap shape:** Multi-file `post-sitemap{N}.xml` archives.

### Selectors & Parsing

| Stage            | Selector / Pattern                                                        | Code Location                          |
|------------------|---------------------------------------------------------------------------|----------------------------------------|
| Index parse      | `<loc>` elements matching regex `post-sitemap(\d*)\.xml$`                | `scrapers/youthop.py:52`               |
| Sitemap filter   | `<url>/<lastmod>` ISO-8601 within `datetime.now(UTC) - days_back`         | `scrapers/youthop.py:85-101`           |
| URL normalize    | `link.lower().rstrip('/')`                                                | `scrapers/youthop.py:108-110`          |
| Slug             | `slugify(URL last path segment)` then strip `-{YYYY}` or `-{YYYY-MM-DD}`  | `scrapers/youthop.py:112-116`          |
| Dedup            | Jaccard on slug token sets, threshold = `self.threshold` (default 0.7)   | `scrapers/youthop.py:118-145`          |
| Body extract     | `trafilatura.extract(page_data, include_comments=False)`                 | `scrapers/youthop.py:162`              |

### Key methods

#### `async get_latest_post_sitemap(self, session) -> str` (`scrapers/youthop.py:36`)

- Fetches `self.index_url` with `ssl=False`.
- Parses `Content-Type` to choose between `lxml-xml` and `html.parser`.
- Iterates `<loc>` entries, picks the highest `N` in `post-sitemapN.xml`.
- **Fallback probe** (`scrapers/youthop.py:62-74`): if no post-sitemap found in the index, probes `post-sitemap{7,6,5,4,3,2,1,}.xml` via `HEAD` requests.
- Raises `RuntimeError` if nothing resolves.

#### `async dump_recent_links(self, session) -> list[str]` (`scrapers/youthop.py:80`)

- Lazily resolves the latest sitemap via `get_latest_post_sitemap`.
- Computes `cutoff = datetime.now(timezone.utc) - timedelta(days=self.days_back)`.
- Iterates `<url>` elements, parses `<lastmod>` as ISO-8601, defaults tz to UTC if naive.
- Appends `<loc>` to `self.links` if `lastmod >= cutoff`.

#### `async process(self, session) -> list[str]` (`scrapers/youthop.py:122`)

Full pipeline: `dump_recent_links → strip → normalize → slugify → tokenize → Jaccard dedup`.

#### `async fetch_url(self, index, session, url) -> Optional[dict]` (`scrapers/youthop.py:150`)

- Sleeps **0.1s** before each request (`scrapers/youthop.py:158`).
- Browser-like headers (Chrome 113) + `Accept`, `Accept-Language` (`scrapers/youthop.py:151-155`).
- `ssl=False`, `response.raise_for_status()`, `trafilatura.extract`.
- `name = self.slugify_links(url).replace("-", " ")` — derived from the URL itself, not from any index offset (`scrapers/youthop.py:164`).
- Returns `{"name": ..., "url": ..., "content": <text with \n replaced by space>}` or `None` on failure.

#### `async getting_data(self) -> list[dict]` (`scrapers/youthop.py:180`)

- `aiohttp.ClientTimeout(total=100)` (overall session budget).
- `TCPConnector(limit=15, limit_per_host=10, ssl=False)`.
- **Retry loop** on the sitemap phase (`scrapers/youthop.py:187-206`): up to 3 attempts, 2-second pause between failures.
- **Fetch phase uses `asyncio.wait(tasks, timeout=60)`** (`scrapers/youthop.py:220`) — a hard 60-second cap inside the scraper. Any pending tasks are cancelled and their exceptions discarded (`scrapers/youthop.py:222-228`).
- Outer 70-second timeout (`scraper.py:58`) gives a 10s buffer for the sitemap and shutdown.

### Quirk
The `getting_data()` method uses **`asyncio.wait` + manual cancellation** rather than `asyncio.gather`, because the orchestrator caps YouthOP at 70s total and the scraper enforces an internal 60s budget to leave headroom. This is the only scraper with this pattern.

### Output sample
```json
{
  "name": "global youth peace ambassador program 2024",
  "url": "https://www.youthop.com/global-youth-peace-ambassador-program-2024/",
  "content": "Application Deadline: October 15, 2024 ..."
}
```

---

## 2. Scholars4Dev — `scrapers/scholars4dev.py`

### Class: `Scholars4Dev` (`scrapers/scholars4dev.py:12`)

**Site:** https://www.scholars4dev.com  
**Index URL pattern:** `https://www.scholars4dev.com/sitemap.xml` (note: **flat**, not `_index.xml`).

### Selectors & Parsing

Same overall structure as YouthOP. Differences:

| Stage            | Difference vs YouthOP                                                       | Code Location                       |
|------------------|------------------------------------------------------------------------------|-------------------------------------|
| Index URL        | `sitemap.xml` instead of `sitemap_index.xml`                                | `scrapers/scholars4dev.py:177`      |
| Sitemap filter   | Defensive: checks both `lm` and `loc` exist before appending                 | `scrapers/scholars4dev.py:67-81`    |
| Connector        | **`limit=1, limit_per_host=1`** — effectively sequential                    | `scrapers/scholars4dev.py:163`      |
| Per-URL sleep    | **1.0s** before each GET                                                     | `scrapers/scholars4dev.py:134`      |

### Key methods

#### `async get_latest_post_sitemap(self, session) -> str` (`scrapers/scholars4dev.py:35`)

Same regex/sort logic as YouthOP. Notably **does not** pass `ssl=False` (relies on default cert verification).

#### `async dump_recent_links(self, session) -> list[str]` (`scrapers/scholars4dev.py:57`)

Identical filtering logic but with the defensive `if loc:` check.

#### `async getting_data(self) -> list[dict]` (`scrapers/scholars4dev.py:160`)

- `aiohttp.ClientTimeout(total=30)` — the most aggressive (shortest) session timeout of the six.
- `TCPConnector(limit=1, limit_per_host=1, ssl=False)`.
- `asyncio.gather(*tasks)` without `return_exceptions=True` — exceptions bubble up. (The orchestrator's `return_exceptions=True` in `scraper.py:64` still catches them.)
- 1.0s pre-fetch sleep per URL inside `fetch_url`.

### Quirk
**Strictly sequential** per-host. The comment at `scrapers/scholars4dev.py:162` reads: *"Use only 1 connection per host to avoid rate limiting (sequential)."* This is the slowest scraper but the most respectful to the host.

---

## 3. ScholarshipsCorner — `scrapers/scholarshipscorner.py`

### Class: `ScholarshipsCorner` (`scrapers/scholarshipscorner.py:10`)

**Site:** https://scholarshipscorner.website  
**Index URL pattern:** `https://scholarshipscorner.website/sitemap_index.xml`.

### Selectors & Parsing

| Stage            | Code Location                                 |
|------------------|-----------------------------------------------|
| Index parse      | `scrapers/scholarshipscorner.py:40-47`        |
| Sitemap filter   | `scrapers/scholarshipscorner.py:54-77`        |
| Connector        | `limit=10, limit_per_host=10` (`scrapers/scholarshipscorner.py:160`) |
| Per-URL sleep    | **1.0s** (`scrapers/scholarshipscorner.py:131`) |

### Key methods

#### `async get_latest_post_sitemap(self, session) -> str` (`scrapers/scholarshipscorner.py:32`)

Same as YouthOP, but **without the fallback probe**. If the index lacks any `post-sitemap{N}.xml`, raises `RuntimeError("No post-sitemap found in index!")` at `scrapers/scholarshipscorner.py:51`.

#### `async dump_recent_links(self, session) -> list[str]` (`scrapers/scholarshipscorner.py:54`)

Mirrors YouthOP exactly.

#### `async fetch_url(self, index, session, url) -> Optional[dict]` (`scrapers/scholarshipscorner.py:124`)

- Sleeps **1.0s** before each request.
- Browser-like Chrome UA; **no `Accept`/`Accept-Language` headers** (more minimal than YouthOP).
- `ssl=False`.
- Returns `{"name", "url", "content"}` with `content` having `\n` replaced by space.

#### `async getting_data(self) -> list[dict]` (`scrapers/scholarshipscorner.py:157`)

- `aiohttp.ClientTimeout(total=50)`.
- `TCPConnector(limit=10, limit_per_host=10, ssl=False)`.
- `asyncio.gather(*tasks, return_exceptions=True)` with post-filter `[item for item in responses if item and not isinstance(item, Exception)]`.

### Quirk
Closest to the "canonical" YouthOP/Scholars4Dev template. Notable for its **lack of fallback probe** (unlike YouthOP) and **no retry loop** on the sitemap phase.

---

## 4. GreatYop — `scrapers/greatyop.py`

### Class: `GreatYopScraper` (`scrapers/greatyop.py:11`)

**Site:** https://greatyop.com  
**Index URL pattern:** `https://greatyop.com/sitemap_index.xml`.

### Selectors & Parsing

Structurally the same as ScholarshipsCorner. The pipeline (`get_latest_post_sitemap → dump_recent_links → process → fetch_url → getting_data`) is duplicated almost verbatim from YouthOP/ScholarshipsCorner.

| Stage            | Difference                                                       | Code Location                          |
|------------------|------------------------------------------------------------------|----------------------------------------|
| Header set       | Chrome UA + `Accept`, `Accept-Language`                          | `scrapers/greatyop.py:129-133`         |
| Connector        | `limit=10, limit_per_host=10`                                    | `scrapers/greatyop.py:162`             |
| Per-URL sleep    | **1.0s** (`scrapers/greatyop.py:136`)                            |                                        |
| Error reporting  | Uses `traceback.print_exc()` in addition to type-name logging    | `scrapers/greatyop.py:152-154`         |

### Key methods

#### `async get_latest_post_sitemap(self, session) -> str` (`scrapers/greatyop.py:33`)

Same as YouthOP/ScholarshipsCorner — picks highest `post-sitemap{N}.xml`. **No fallback probe.**

#### `async dump_recent_links(self, session) -> list[str]` (`scrapers/greatyop.py:55`)

Same logic; verbose comment on timezone handling at `scrapers/greatyop.py:72-75`.

#### `async fetch_url(self, index, session, url) -> Optional[dict]` (`scrapers/greatyop.py:127`)

- Sleeps **1.0s**.
- Chrome UA + `Accept`, `Accept-Language`.
- **Catches a single broad `Exception`** (instead of `asyncio.TimeoutError`, `aiohttp.ClientError`, `Exception` separately like other scrapers). Prints `traceback.print_exc()` on failure.

#### `async getting_data(self) -> list[dict]` (`scrapers/greatyop.py:159`)

- `aiohttp.ClientTimeout(total=50)`.
- `TCPConnector(limit=10, limit_per_host=10, ssl=False)`.
- `asyncio.gather(*tasks, return_exceptions=True)`.

### Quirk
Uses **`traceback.print_exc()`** for diagnostics — slightly noisier than the others but useful when debugging fetch issues.

---

## 5. OpportunitiesCorners — `scrapers/opportunitiescorner.py`

### Class: `OpportunitiesCorners` (`scrapers/opportunitiescorner.py:10`)

**Site:** https://opportunitiescorners.com  
**Index URL pattern:** `https://opportunitiescorners.com/sitemap_index.xml`.

### Selectors & Parsing

| Stage            | Difference                                                          | Code Location                          |
|------------------|---------------------------------------------------------------------|----------------------------------------|
| Sitemap parsing  | **Content-sniffing fallback** to `html.parser` if not XML           | `scrapers/opportunitiescorner.py:60-71`|
| Headers          | Only the default `self.headers` (Chrome UA) — **no** Accept/Language | `scrapers/opportunitiescorner.py:144`  |
| Per-URL sleep    | **None** (relies on connection limits + 60s outer timeout)         | —                                      |
| Connector        | `limit=20, limit_per_host=7` (most aggressive of the six)          | `scrapers/opportunitiescorner.py:172`  |

### Key methods

#### `async get_latest_post_sitemap(self, session) -> str` (`scrapers/opportunitiescorner.py:31`)

Same standard logic. **No fallback probe.**

#### `async dump_links(self, session) -> list[str]` (`scrapers/opportunitiescorner.py:53`)

Renamed from `dump_recent_links`. **Sniffs the response body** (`content.strip().startswith(b'<?xml')`) and chooses between `lxml-xml` and `html.parser` (`scrapers/opportunitiescorner.py:66-70`). The defensive path is required because some Yoast-generated sitemaps are served as `text/html`.

Wraps the entire fetch in a `try/except` that returns `[]` on failure (`scrapers/opportunitiescorner.py:72-74`).

#### `async fetch_url(self, index, session, url) -> Optional[dict]` (`scrapers/opportunitiescorner.py:141`)

- **No pre-fetch sleep**.
- Uses `self.headers` only (no `Accept`/`Accept-Language`).
- Same `trafilatura.extract + slugify-name-from-URL` pattern as the others.

#### `async getting_data(self) -> list[dict]` (`scrapers/opportunitiescorner.py:169`)

- `aiohttp.ClientTimeout(total=20)` — the **shortest** per-request total.
- `TCPConnector(limit=20, limit_per_host=7, ssl=False)` — most permissive concurrency.
- `asyncio.gather(*tasks)` **without** `return_exceptions=True`. Exceptions bubble up to the orchestrator.

### Quirk
**Largest concurrency (20/7)** and **shortest request timeout (20s)**. Trusts that aggressive concurrency will be absorbed by a tolerant CDN. There is also no per-URL delay, so this is the fastest scraper in wall-clock terms.

---

## 6. OpportunitiesForYouth — `scrapers/opportunitiesforyouth.py`

### Class: `OpportunitiesForYouth` (`scrapers/opportunitiesforyouth.py:9`)

**Site:** https://opportunitiesforyouth.org  
**Index URL pattern:** `https://opportunitiesforyouth.org/sitemap-1.xml` (note: **`sitemap-1.xml`** rather than `sitemap_index.xml` — a **flat single-file** sitemap).

### Selectors & Parsing

| Stage            | Difference                                                          | Code Location                          |
|------------------|---------------------------------------------------------------------|----------------------------------------|
| Index URL        | Single `sitemap-1.xml` (no nested sitemap index)                    | `scrapers/opportunitiesforyouth.py:142`|
| Sitemap parse    | Direct `<url>/<lastmod>` filtering, no `get_latest_post_sitemap`    | `scrapers/opportunitiesforyouth.py:22` |
| Per-URL sleep    | **Staggered `index * 2.0` seconds** — sequential with growing gap   | `scrapers/opportunitiesforyouth.py:98` |
| Connector        | `limit=1, limit_per_host=1` (sequential)                            | `scrapers/opportunitiesforyouth.py:128`|

### Key methods

#### `async dump_links(self, session) -> list[str]` (`scrapers/opportunitiesforyouth.py:22`)

There is **no separate sitemap-index step** — the configured `index_url` is itself the post-sitemap. Iterates `<url>/<lastmod>` directly with the same tz-aware cutoff logic.

#### `async fetch_url(self, index, session, url) -> Optional[dict]` (`scrapers/opportunitiesforyouth.py:89`)

- **Staggered delay**: `await asyncio.sleep(index * 2.0)` ensures a 2-second gap between consecutive requests even when the connector is effectively serial. For URL #0 the delay is 0s, #1 is 2s, #2 is 4s, etc.
- Same Chrome UA + `Accept` + `Accept-Language` as YouthOP.
- Returns the standard `{name, url, content}` shape.

#### `async getting_data(self) -> list[dict]` (`scrapers/opportunitiesforyouth.py:124`)

- `aiohttp.ClientTimeout(total=90)` — the longest of the six, to accommodate the staggered sleeps.
- `TCPConnector(limit=1, limit_per_host=1, ssl=False)`.
- `asyncio.gather(*tasks)` without `return_exceptions=True`.

### Quirk
Uses **cumulative staggered delays** rather than a fixed per-URL sleep. The intent (per the comment at `scrapers/opportunitiesforyouth.py:97`) is "a clean 2-second gap between requests" — i.e. index 0 starts at t=0, index 1 starts at t=2s, etc. This makes total runtime roughly `2 * N` seconds plus page-fetch time.

---

## Cross-cutting Concerns

### Shared helpers (duplicated across scrapers)

The following functions are duplicated verbatim in **every** site scraper file. They are *not* factored into `scrapers/base.py`:

| Helper                  | Where it lives                                |
|-------------------------|-----------------------------------------------|
| `normalize_url(link)`   | `static` method in each scraper file          |
| `slugify_links(u)`      | `static` method in each scraper file          |
| `jaccard(a, b)`         | `static` method in each scraper file          |
| `dump_recent_links / dump_links` | per-scraper                     |
| `process(session)`      | per-scraper                                   |

This duplication is the principal motivator for the existence of `BaseScraper` (see `docs/scrapers_base.md`).

### Output schema (every scraper)

All six scrapers emit identical dicts:

```python
{
    "name": str,      # derived from URL slug (e.g. "fully funded scholarship in canada 2024")
    "url":  str,      # original URL
    "content": str,   # trafilatura-extracted plain text (\n → " ")
}
```

`name` is derived from the URL slug — **not** from the page title — to avoid index-mismatch bugs after the dedup filter (comment at `scrapers/youthop.py:163`).

### Combined error-handling matrix

| Scraper               | Connector              | Per-URL sleep | Outer timeout | Special                                                            |
|-----------------------|------------------------|---------------|---------------|--------------------------------------------------------------------|
| YouthOP               | `limit=15, host=10`    | 0.1s          | 100s (70s outer) | `asyncio.wait` + manual cancel after 60s; sitemap retry loop      |
| Scholars4Dev          | `limit=1, host=1`      | 1.0s          | 30s           | Sequential, no SSL override                                        |
| ScholarshipsCorner    | `limit=10, host=10`    | 1.0s          | 50s           | No fallback probe                                                  |
| GreatYop              | `limit=10, host=10`    | 1.0s          | 50s           | `traceback.print_exc()` on failure                                 |
| OpportunitiesCorners  | `limit=20, host=7`     | **none**      | 20s           | HTML/XML content sniffing; no `return_exceptions=True`             |
| OpportunitiesForYouth | `limit=1, host=1`      | `index * 2s`  | 90s           | Single `sitemap-1.xml` (no nested index)                           |

### `BaseScraper` integration status

None of the six active scrapers currently `class X(BaseScraper)`. They are all stand-alone classes that duplicate the in-house pattern. See `docs/scrapers_base.md` for the canonical pattern intended for future migration.

---

## Quick Reference Summary

| # | Scraper               | Class              | Site                              | URL pattern                                              | Concurrency              |
|---|-----------------------|--------------------|-----------------------------------|----------------------------------------------------------|--------------------------|
| 1 | YouthOP               | `YouthOP`          | youthop.com                       | `sitemap_index.xml` → `post-sitemapN.xml`                | `limit=15, host=10`      |
| 2 | Scholars4Dev          | `Scholars4Dev`     | scholars4dev.com                  | `sitemap.xml`                                            | `limit=1, host=1`        |
| 3 | ScholarshipsCorner    | `ScholarshipsCorner` | scholarshipscorner.website      | `sitemap_index.xml` → `post-sitemapN.xml`                | `limit=10, host=10`      |
| 4 | GreatYop              | `GreatYopScraper`  | greatyop.com                      | `sitemap_index.xml` → `post-sitemapN.xml`                | `limit=10, host=10`      |
| 5 | OpportunitiesCorners  | `OpportunitiesCorners` | opportunitiescorners.com      | `sitemap_index.xml` → `post-sitemapN.xml`                | `limit=20, host=7`       |
| 6 | OpportunitiesForYouth | `OpportunitiesForYouth` | opportunitiesforyouth.org     | `sitemap-1.xml` (flat)                                   | `limit=1, host=1`        |