# `backend/database.py` — SQLite Persistence Layer

## 1. Purpose & Overview

`backend/database.py` is a thin, dependency-free wrapper around `sqlite3` that owns all of the project's persistent state:

- **Chat sessions** (`sessions` table) — UUID-keyed conversation containers.
- **Chat messages** (`messages` table) — turns within a session, with optional JSON metadata.
- **Opportunities** (`opportunities` table) — scraped scholarships/fellowships, deduped by a SHA-256 content hash, enriched with extracted metadata and deadline.

The module exposes a flat set of functions (`init_db`, `create_session`, `add_message`, …) consumed directly by route handlers. It deliberately avoids an ORM so the data layer is transparent and easy to audit.

Key features:

- **Single SQLite file** at `opportunities_chat.db` in the current working directory (`backend/database.py:8`).
- **Connection-per-call** with a small context manager — no global connection, no pool, no threads to coordinate beyond `check_same_thread=False`.
- **Idempotent schema** — `init_db()` can be run any number of times; it issues `CREATE TABLE IF NOT EXISTS`.
- **In-place migrations** — `_ensure_opportunities_schema` adds `metadata_json` and `deadline` columns to existing databases via `ALTER TABLE` (`backend/database.py:23`).
- **Upsert by content hash** — re-scraping the same item updates the row instead of duplicating it.
- **Auto-session-creation** — `add_message` will materialize a session row the first time a message is added to an unknown `session_id`, deriving a title from the first user prompt (`backend/database.py:151`).

---

## 2. Architecture

```
         ┌───────────────────────────────────────────────────┐
         │             backend/main.py  (HTTP routes)        │
         │                                                   │
         │   lifespan()       ──►  init_db()                  │
         │                                                   │
         │   chat_completion  ──►  create_session, add_message│
         │   chat_stream      ──►  add_message                │
         │   get_messages     ──►  get_session_messages       │
         │   list_sessions    ──►  list_sessions              │
         │   delete_session   ──►  delete_session             │
         │   opportunities    ──►  list_opportunities,        │
         │                       get_opportunity_by_id        │
         │   scrape (bg)      ──►  upsert_opportunities*      │
         └───────────────────────────────────────────────────┘
                            │
                            ▼
                ┌──────────────────────┐
                │  backend/database.py │
                └──────────┬───────────┘
                           │ sqlite3
                           ▼
                ┌──────────────────────┐
                │ opportunities_chat.db│
                │ ────────────────────│
                │  sessions            │
                │  messages            │
                │  opportunities       │
                └──────────────────────┘
```

\* `upsert_opportunities` and `update_opportunity_metadata` are called from the scraper module, not from `main.py` directly. They live in `database.py` for organizational consistency.

---

## 3. Key Classes / Functions

### 3.1 `DB_PATH` — `backend/database.py:8`

```python
DB_PATH = Path("opportunities_chat.db")
```

Relative path; the DB file is created in whatever directory the server is launched from. Use `DB_PATH.resolve()` to inspect the absolute location (`backend/database.py:74`).

### 3.2 `get_db_connection()` — `backend/database.py:13`

```python
@contextmanager
def get_db_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()
```

- Opens a new SQLite connection per call.
- `check_same_thread=False` is mandatory because FastAPI may service requests on multiple threads.
- `row_factory = sqlite3.Row` enables `cursor.fetchone()["column"]` and `dict(row)` ergonomic access.
- Closes the connection in `finally`; the caller is responsible for `commit()`.

### 3.3 `_ensure_opportunities_schema(cursor)` — `backend/database.py:23`

```python
def _ensure_opportunities_schema(cursor: sqlite3.Cursor) -> None:
    """Create the opportunities table if missing and migrate metadata columns."""
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            source TEXT,
            url TEXT,
            content_hash TEXT UNIQUE,
            created_at TEXT NOT NULL
        )
        """
    )
    columns = {row[1] for row in cursor.execute("PRAGMA table_info(opportunities)").fetchall()}
    if "metadata_json" not in columns:
        cursor.execute("ALTER TABLE opportunities ADD COLUMN metadata_json TEXT")
    if "deadline" not in columns:
        cursor.execute("ALTER TABLE opportunities ADD COLUMN deadline TEXT")
```

- Idempotent table creation.
- Conditional `ALTER TABLE` migrations for `metadata_json` and `deadline` columns — safe to run on every startup.
- Called by `init_db()` (`backend/database.py:72`), `upsert_opportunities()` (`backend/database.py:207`), and `update_opportunity_metadata()` (`backend/database.py:254`).

### 3.4 `init_db()` — `backend/database.py:45`

```python
def init_db():
    """Initialize SQLite database tables for chat sessions and messages."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""CREATE TABLE IF NOT EXISTS sessions …""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS messages …""")
        _ensure_opportunities_schema(cursor)
        conn.commit()
    print(f"[DB] Initialized SQLite database at {DB_PATH.resolve()}")
```

Creates the three tables if they don't already exist:

**`sessions`**
| Column | Type | Notes |
| --- | --- | --- |
| `session_id` | TEXT PK | UUID string. |
| `title` | TEXT NOT NULL | User-supplied or auto-derived. |
| `created_at` | TEXT NOT NULL | ISO-8601 timestamp. |
| `updated_at` | TEXT NOT NULL | Bumped on every message. |

**`messages`**
| Column | Type | Notes |
| --- | --- | --- |
| `id` | INTEGER PK AUTOINCREMENT | |
| `session_id` | TEXT NOT NULL | FK → `sessions(session_id)` `ON DELETE CASCADE`. |
| `role` | TEXT NOT NULL | `"user"` or `"assistant"`. |
| `content` | TEXT NOT NULL | Message body. |
| `metadata_json` | TEXT | JSON-encoded metadata dict. |
| `created_at` | TEXT NOT NULL | |

**`opportunities`** — see `_ensure_opportunities_schema`; same columns plus `metadata_json` and `deadline` (added on first run after the migration).

### 3.5 `create_session(session_id=None, title="New Chat")` — `backend/database.py:77`

```python
def create_session(session_id: Optional[str] = None, title: str = "New Chat") -> str:
    sid = session_id or str(uuid4())
    now = datetime.now().isoformat()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO sessions (session_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (sid, title, now, now),
        )
        conn.commit()
    return sid
```

- `INSERT OR REPLACE` means calling with an existing `session_id` overwrites its title — useful for "create-or-reset" semantics, but watch out if you only want to insert-if-new.
- `now` is reused for both `created_at` and `updated_at`, so a freshly-created session has equal timestamps.
- Returns the resolved `session_id` (handy when called as `create_session()` with no args).

### 3.6 `list_sessions()` — `backend/database.py:94`

```python
def list_sessions() -> List[Dict[str, Any]]:
```

`SELECT session_id, title, created_at, updated_at FROM sessions ORDER BY updated_at DESC` and returns a list of dicts.

### 3.7 `get_session_messages(session_id)` — `backend/database.py:109`

```python
def get_session_messages(session_id: str) -> List[Dict[str, Any]]:
```

Returns chronological (`ORDER BY id ASC`) messages for a session. For each row, `metadata_json` is JSON-decoded into `metadata` (or `None` on failure / missing), and the raw `metadata_json` key is popped from the returned dict.

### 3.8 `add_message(session_id, role, content, metadata=None, auto_create_session=True)` — `backend/database.py:138`

```python
def add_message(
    session_id: str,
    role: str,
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
    auto_create_session: bool = True,
):
    now = datetime.now().isoformat()
    meta_json = json.dumps(metadata) if metadata else None

    with get_db_connection() as conn:
        cursor = conn.cursor()
        if auto_create_session:
            cursor.execute("SELECT session_id FROM sessions WHERE session_id = ?", (session_id,))
            if not cursor.fetchone():
                title_preview = content[:40].strip() + ("..." if len(content) > 40 else "")
                cursor.execute(
                    "INSERT INTO sessions (session_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (session_id, title_preview or "New Chat", now, now),
                )

        cursor.execute(
            """INSERT INTO messages (session_id, role, content, metadata_json, created_at) VALUES (?, ?, ?, ?, ?)""",
            (session_id, role, content, meta_json, now),
        )

        # Update session title if user prompt and session title is default
        if role == "user":
            cursor.execute("SELECT title FROM sessions WHERE session_id = ?", (session_id,))
            row = cursor.fetchone()
            if row and row["title"] == "New Chat":
                new_title = content[:35].strip() + ("..." if len(content) > 35 else "")
                cursor.execute(
                    "UPDATE sessions SET title = ?, updated_at = ? WHERE session_id = ?",
                    (new_title, now, session_id),
                )
            else:
                cursor.execute(
                    "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
                    (now, session_id),
                )
        else:
            cursor.execute(
                "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
                (now, session_id),
            )

        conn.commit()
```

| Param | Type | Notes |
| --- | --- | --- |
| `session_id` | `str` | If unknown and `auto_create_session=True`, a new session is created. |
| `role` | `str` | `"user"` or `"assistant"`. |
| `content` | `str` | Message text. |
| `metadata` | `Optional[Dict]` | Stored as a JSON-encoded `metadata_json` column. |
| `auto_create_session` | `bool` | When `True`, materializes a session row on first use. Set to `False` to require the caller to have called `create_session` first. |

**Side effects on the `sessions` row:**

- **User message + title is `"New Chat"`:** renames to first 35 chars of content + `...` if truncated.
- **User message + title already set:** only bumps `updated_at`.
- **Assistant message:** only bumps `updated_at`.

Returns: `None`. (No exception is raised if the session already exists; only the auto-create path branches on presence.)

### 3.9 `delete_session(session_id)` — `backend/database.py:187`

```python
def delete_session(session_id: str):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        cursor.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        conn.commit()
```

- Deletes messages explicitly (FK is `ON DELETE CASCADE`, but SQLite needs `PRAGMA foreign_keys=ON` for cascade to fire — the explicit delete is safer).
- Returns: `None`.

### 3.10 `upsert_opportunities(items)` — `backend/database.py:196`

```python
def upsert_opportunities(items: List[Dict[str, Any]]) -> List[int]:
    """Upsert opportunity items into SQLite database based on SHA-256 content hash."""
```

| Param | Notes |
| --- | --- |
| `items` | A list of dicts. Each dict is expected to carry some subset of: `name`/`title`, `content`, `source`/`portal`, `url`/`link`, `metadata` (dict), `deadline` (optional; pulled from `metadata["deadline"]` if absent). |

Returns: a `List[int]` of row IDs, **aligned with input order**. A returned value of `-1` means the row could not be located after upsert (a defensive fallback).

**Algorithm per item:**

1. Normalize field names:
   - `title = item.get("name") or item.get("title") or "Untitled Opportunity"`
   - `content = item.get("content") or ""`
   - `source = item.get("source") or item.get("portal") or ""`
   - `url = item.get("url") or item.get("link") or ""`
   - `metadata = item.get("metadata")`
   - `deadline = metadata["deadline"]` if `metadata` is a dict
2. `content_hash = sha256(f"{title}:{content[:200]}")` — only the first 200 chars of content contribute to the hash so small edits to long pages don't churn dedup.
3. `INSERT INTO opportunities (...) VALUES (...) ON CONFLICT(content_hash) DO UPDATE SET ...`:
   - On conflict, all non-PK fields are overwritten with the new values.
   - `metadata_json` and `deadline` use `COALESCE(excluded.X, opportunities.X)` so a None/null incoming value **preserves the existing one** (lets the scraper re-enrich without first nuking prior metadata).
4. `SELECT id FROM opportunities WHERE content_hash = ?` — used to surface the row ID back to the caller.

### 3.11 `update_opportunity_metadata(opp_id, metadata)` — `backend/database.py:248`

```python
def update_opportunity_metadata(opp_id: int, metadata: Dict[str, Any]) -> bool:
```

- Validates `metadata` is a dict (returns `False` otherwise).
- `UPDATE opportunities SET metadata_json = ?, deadline = COALESCE(?, deadline) WHERE id = ?` — preserves existing deadline if the new metadata dict doesn't carry one.
- Returns `cursor.rowcount > 0` (i.e. whether a row was actually updated).

### 3.12 `_parse_opportunity_row(row)` — `backend/database.py:271`

```python
def _parse_opportunity_row(row: sqlite3.Row) -> Dict[str, Any]:
```

- Converts a row to a dict via `dict(row)`.
- Pops `metadata_json` and JSON-decodes it into `metadata` (or `None`).
- Returns the cleaned dict.

### 3.13 `list_opportunities(query=None, source=None, limit=20, offset=0)` — `backend/database.py:285`

```python
def list_opportunities(
    query: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> Dict[str, Any]:
```

Returns:
```python
{
    "total": int,        # unpaginated row count under the same filters
    "limit": int,        # echoes the input
    "offset": int,       # echoes the input
    "items": List[Dict], # rows, with metadata_json parsed into `metadata`
}
```

- `query`: `LIKE %…%` against `title` OR `content`.
- `source`: exact-match filter on the `source` column.
- Pagination: `ORDER BY id DESC LIMIT ? OFFSET ?`.
- The `total` count is computed against the *filtered* SQL but *before* applying `LIMIT/OFFSET`, which lets the client render "showing N–M of T" correctly.

### 3.14 `get_opportunity_by_id(opp_id)` — `backend/database.py:328`

```python
def get_opportunity_by_id(opp_id: int) -> Optional[Dict[str, Any]]:
```

Returns the parsed row or `None` if not found.

---

## 4. Flow / Lifecycle

### 4.1 Startup (called from `lifespan`)

```
lifespan startup
   └─► init_db()
         ├─► CREATE TABLE IF NOT EXISTS sessions
         ├─► CREATE TABLE IF NOT EXISTS messages
         └─► _ensure_opportunities_schema(cursor)
               ├─► CREATE TABLE IF NOT EXISTS opportunities
               ├─► PRAGMA table_info(opportunities)
               ├─► ALTER TABLE opportunities ADD COLUMN metadata_json TEXT    (if missing)
               └─► ALTER TABLE opportunities ADD COLUMN deadline TEXT          (if missing)
         └─► conn.commit()
         └─► print("[DB] Initialized SQLite database at <abs_path>")
```

### 4.2 First-message session lifecycle

```
chat_completion called with no session_id
   ├─► sid = create_session()                  ← creates "New Chat" row
   ├─► add_message(sid, "user", prompt)
   │      ├─► sees no row for sid (it was just created above so this branch is skipped)
   │      └─► role == "user" + title == "New Chat"
   │             └─► renames session.title to first 35 chars of prompt
   └─► add_message(sid, "assistant", response)
          └─► bumps updated_at
```

### 4.3 Scrape lifecycle (caller is `scraper.CombinedScraper`)

```
scraper.run_all_scrapers() → list[dict]
   └─► upsert_opportunities(items)
         for each item:
           ├─► normalize field names
           ├─► content_hash = sha256(title + first 200 chars of content)
           ├─► INSERT … ON CONFLICT(content_hash) DO UPDATE …
           └─► collect id
         commit
   └─► (later) update_opportunity_metadata(opp_id, enriched_metadata)
         └─► UPDATE opportunities SET metadata_json = ?, deadline = COALESCE(?, deadline)
```

### 4.4 Shutdown

No explicit teardown — connections are closed by the context manager in every function. The process exit closes the SQLite file.

---

## 5. Dependencies

| Import | Why |
| --- | --- |
| `json` | Encode/decode `metadata_json` columns. |
| `sqlite3` | The whole persistence layer. |
| `datetime.datetime` | ISO-8601 timestamps. |
| `pathlib.Path` | Type for `DB_PATH`. |
| `typing.{Any, Dict, List, Optional}` | Type hints. |
| `uuid.uuid4` | Generate session IDs. |
| `contextlib.contextmanager` | `get_db_connection` context manager. |
| `hashlib` (lazy import inside `upsert_opportunities`) | SHA-256 for dedup hashing. |

Only the Python stdlib is used.

---

## 6. Configuration / Environment variables

None. `DB_PATH` is a module constant relative to the current working directory. To change it, edit `backend/database.py:8` or wrap it in a settings lookup.

---

## 7. API Endpoints

This module defines **no routes**, but every function is reachable via an HTTP route in `backend/main.py`:

| Function | Route |
| --- | --- |
| `init_db` | called from `lifespan` startup |
| `create_session` | `POST /chat` (implicit), `POST /sessions` |
| `list_sessions` | `GET /sessions` |
| `get_session_messages` | `GET /sessions/{session_id}/messages` |
| `add_message` | `POST /chat`, `POST /chat/stream` |
| `delete_session` | `DELETE /sessions/{session_id}` |
| `list_opportunities` | `GET /opportunities` |
| `get_opportunity_by_id` | `GET /opportunities/{opp_id}` |
| `upsert_opportunities` | called by the scraper (background task from `POST /scrape`) |
| `update_opportunity_metadata` | called by the scraper after LLM enrichment |

---

## 8. Error handling

- **No explicit `try/except` around SQL.** SQLite errors (constraint violations, I/O errors) propagate to the caller. Route handlers wrap calls in their own `try/except Exception` and re-raise as `HTTPException(500)`.
- **`upsert_opportunities`** swallows no errors. If a single item raises, the entire transaction is rolled back at the connection close.
- **`get_session_messages`** swallows JSON-decoding errors (`except Exception: d["metadata"] = None`) — `backend/database.py:129`. A corrupt `metadata_json` row returns `metadata=None` rather than 500.
- **`upsert_opportunities`** returns `-1` for an item if the post-upsert `SELECT id` fails to locate the row (defensive fallback).

---

## 9. Notable patterns / design decisions

- **No ORM.** Explicit SQL keeps the layer auditable and easy to reason about for a single-file project. The cost is boilerplate, mitigated by short helper functions.
- **One connection per call.** Avoids cross-thread issues (with `check_same_thread=False`) at the cost of an open/close on every request. SQLite opens are fast enough on local filesystems; no connection pool needed.
- **Explicit `DELETE FROM messages` before `DELETE FROM sessions`.** Foreign-key cascade in SQLite requires `PRAGMA foreign_keys=ON`, which the driver does not enable by default. The explicit delete is safe regardless of PRAGMA state.
- **Idempotent migrations via `PRAGMA table_info`.** Allows deploying schema changes (`metadata_json`, `deadline`) without a separate migration tool — perfect for a local-first tool.
- **Content-hash dedup.** Re-scraping the same item updates in place rather than duplicating rows. The hash only covers `title` + first 200 chars of `content`, so meaningful content updates are picked up.
- **`COALESCE(excluded.X, opportunities.X)`** on update preserves prior metadata/deadline if the new value is `NULL`. Critical for incremental LLM enrichment where a "no-deadline-found" pass should not wipe a previously-extracted deadline.
- **Auto-session creation + title auto-derivation** are baked into `add_message` so the API surface stays simple: callers don't have to call `create_session` before `add_message`.
- **ISO-8601 string timestamps** (not SQLite `DATETIME`/Unix epoch). Easy to read in `sqlite3` CLI, easy to compare lexically, easy to surface in JSON without converters.
- **`check_same_thread=False` + `row_factory=Row`.** The two ergonomic essentials for serving SQLite from a multi-threaded FastAPI app.