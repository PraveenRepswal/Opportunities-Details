# `backend/main.py` — FastAPI Application & Routes

## 1. Purpose & Overview

`backend/main.py` is the **single-file FastAPI entrypoint** for the Opportunities-Details backend. It wires together every subsystem (RAG pipeline, SQLite persistence, scraper, rate limiter, STT transcriber, CORS) and exposes them as a versioned REST API (`/api/v1/*`) plus a set of legacy unversioned root-level aliases (`/chat`, `/opportunities`, …) for backward compatibility.

The file's responsibilities are:

- **Lifespan management** — initialize the SQLite schema and warm up the RAG pipeline (embeddings + LLM) on startup; print a shutdown notice on teardown.
- **Middleware ordering** — install the per-IP sliding-window rate limiter *before* CORS so 429 responses still carry CORS headers (the last-added middleware is the outermost in Starlette).
- **HTTP routes** — health, chat (sync + streaming), opportunities listing/detail, scrape trigger/status, chat-session CRUD, and audio transcription.
- **Streaming protocol** — Server-Sent-Events (SSE) style `text/event-stream` where assistant text is interleaved with a sidecar `[[METADATA]] {json}` block used by the UI to render "opportunity cards" alongside prose.
- **Background scraping** — schedule long-running scrape jobs onto FastAPI's `BackgroundTasks` so the HTTP response returns immediately with a status handle.

The module is **directly runnable** as `python -m backend.main` (or `python backend/main.py`); the `__main__` block at `backend/main.py:419` boots Uvicorn.

---

## 2. Architecture

```
                     ┌────────────────────────────────────────────┐
                     │              FastAPI app (main.py)          │
                     │                                              │
  HTTP request ───▶  │  RateLimitMiddleware ─▶ CORSMiddleware ─▶   │
                     │     │                                       │
                     │     ▼                                       │
                     │  /api/v1/*  routes + legacy aliases          │
                     │     │                                       │
                     │     ├── chat  ──▶  RAGPipeline.stream_resp   │
                     │     │              ├─ answer cache (in-mem)  │
                     │     │              ├─ FAISS / vector store   │
                     │     │              └─ Ollama / llama.cpp HTTP │
                     │     │                                        │
                     │     ├── opportunities ─▶ backend/database.py │
                     │     ├── scrape (BackgroundTasks)             │
                     │     │           └─ scraper.CombinedScraper   │
                     │     │                └─ database upsert      │
                     │     │                └─ rag.reload_documents  │
                     │     ├── sessions/messages ─▶ backend/database.py
                     │     └── transcribe ─▶ backend/stt.py (Moonshine)
                     │                                              │
                     │  Globals: rag_pipeline, scrape_job_state      │
                     └────────────────────────────────────────────┘
```

Two **module-level singletons** carry process-wide state:

| Symbol | Type | Defined at | Purpose |
| --- | --- | --- | --- |
| `rag_pipeline` | `RAGPipeline` | `backend/main.py:45` | Long-lived RAG pipeline (embeddings index + answer cache). Initialized once at startup, reused per request. |
| `scrape_job_state` | `dict` | `backend/main.py:46` | Tracks current/latest scrape job (`status`, `message`, `items_scraped`, `scraped_at`, `error`). |

A third global — `scrape_job_state` — is mutated **inside** `_execute_scrape_job` via `global scrape_job_state` (`backend/main.py:106`, `backend/main.py:121`).

---

## 3. Key Functions

### 3.1 `lifespan(app: FastAPI)` — `backend/main.py:55`

`@asynccontextmanager` FastAPI lifespan hook.

```python
async def lifespan(app: FastAPI):
    """Warm up RAG pipeline and initialize database on app startup."""
    print("[API] Starting up FastAPI server...")
    try:
        init_db()
        rag_pipeline.initialize()
    except Exception as exc:
        print(f"[API] Error during startup initialization: {exc}")
    yield
    print("[API] Shutting down FastAPI server...")
```

| Phase | Action |
| --- | --- |
| **Startup** (before `yield`) | Calls `init_db()` (creates `sessions`, `messages`, `opportunities` tables if missing) then `rag_pipeline.initialize()` (loads embeddings model + vector store). Exceptions are caught and logged so the server can still come up — degraded mode is better than no server. |
| **Runtime** (`yield`) | ASGI serves requests. |
| **Shutdown** (after `yield`) | Prints shutdown banner. (No explicit resource release; relies on process exit for the SQLite connection and the in-memory cache.) |

### 3.2 Middleware setup — `backend/main.py:79`

```python
if settings.api.rate_limit_enabled:
    app.add_middleware(
        RateLimitMiddleware,
        limits={
            "chat":       settings.api.rate_limit_chat_per_minute,
            "transcribe": settings.api.rate_limit_transcribe_per_minute,
            "scrape":     settings.api.rate_limit_scrape_per_minute,
            "default":    settings.api.rate_limit_default_per_minute,
        },
        trust_forwarded_for=settings.api.rate_limit_trust_proxy,
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.api.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

- `RateLimitMiddleware` is added **first** so it is the **innermost** layer; CORS wraps it. This guarantees 429 responses include `Access-Control-Allow-*` headers for browsers.
- CORS is wide-open by default (`allow_origins=["*"]`), appropriate for a local-first tool but should be locked down for any non-loopback exposure.

### 3.3 `_execute_scrape_job(days_back, threshold, reindex)` — `backend/main.py:105`

```python
async def _execute_scrape_job(days_back: int, threshold: float, reindex: bool):
```

| Param | Type | Meaning |
| --- | --- | --- |
| `days_back` | `int` | Only consider items published within the last N days. |
| `threshold` | `float` | Relevance/score threshold for accepting a scraped item. |
| `reindex` | `bool` | If `True`, call `rag_pipeline.reload_documents()` after the scrape so the new items are immediately queryable. |

Returns: `None` — side effects only.

**Flow:**
1. Flip `scrape_job_state` to `"running"` and stamp the message.
2. Construct `CombinedScraper(days_back, threshold)` and `await scraper.run_all_scrapers()`.
3. `await scraper.await_enrichment()` — blocks until the LLM enrichment queue drains.
4. If `reindex`, call `rag_pipeline.reload_documents()`.
5. On success: set status `"completed"`, `items_scraped`, `scraped_at = now`.
6. On any exception: set status `"failed"`, capture the error string. The traceback is `print`-ed to stdout but **not** returned to the caller (the background task is fire-and-forget).

### 3.4 `health_check()` — `backend/main.py:131`

`GET /health` (and `GET /api/v1/health`).

- Returns `HealthResponse` with RAG metrics.
- Defensively tries to read `rag_pipeline.answer_cache.stats()` if the attribute exists; otherwise returns zero counters.
- `status` is `"ok"` when `rag_pipeline.is_initialized` is true, else `"initializing"`.

### 3.5 `chat_completion(request: ChatRequest)` — `backend/main.py:158`

`POST /chat` (and `POST /api/v1/chat`) — **non-streaming** chat.

```python
async def chat_completion(request: ChatRequest):
```

1. `session_id = request.session_id or create_session()` — if no session was supplied, allocate a new UUID-backed session row.
2. `add_message(session_id, "user", request.prompt)` — persist the user turn immediately.
3. Convert `request.conversation_history` to plain dicts (`m.model_dump()`) for the RAG pipeline.
4. `async for chunk in raw_generator`: scan for a sentinel line starting with `[[METADATA]]`; everything else is appended to `full_response`. The sentinel is JSON after the tag — parsed via `json.loads(line.replace("[[METADATA]]", "").strip())`. Parse failures are silently swallowed.
5. After streaming completes: `clean_response = strip_thinking_tags(full_response)` to remove any `<|think|>…<|/think|>` blocks the LLM emitted.
6. Persist the assistant turn with `add_message(...)`.
7. Return `ChatResponse(session_id, response=clean_response, metadata=metadata_dict)`.
8. Any exception is wrapped in `HTTPException(500, str(e))`.

### 3.6 `extract_cards_from_response(text)` — `backend/main.py:207`

Helper used only by the streaming endpoint. It post-processes the raw, *un-stripped* assistant text and pulls out pipe-separated metadata lines formatted as `Key: Value | Key: Value …`.

```python
def extract_cards_from_response(text: str) -> List[dict]:
```

| Key recognized | Source field |
| --- | --- |
| `deadline` | `deadline` |
| `organization` | `organization` |
| `location` | `location` |
| `type` | `type` |
| `title` | `title` |

- Accepts lines containing any of the trigger keywords `Deadline:`, `Organization:`, `Location:` and containing a `|`.
- Strips bullet markers (`•`, `*`, `-`) from the start of each pipe-part.
- Strips `**` / `*` (Markdown bold) from values.
- Returns a list of dicts; each dict only contains the keys that were present in that line.

### 3.7 `chat_stream(request: ChatRequest)` — `backend/main.py:230`

`POST /chat/stream` (also `/api/v1/chat/stream` and legacy `/api/chat/stream`) — **streaming** chat.

- Persists the user turn up-front **only if** `session_id` was provided (unlike the sync path, no implicit session creation).
- Sets `effective_debug = request.debug or settings.debug`.
- Wraps `rag_pipeline.stream_response(...)` in an inner async generator `database_persisting_generator()` (`backend/main.py:250`) which:
  - Yields every chunk immediately so the client sees tokens in real time.
  - Accumulates `full_response` and parses any `[[METADATA]]` lines into `metadata_dict`.
  - After the upstream generator exhausts, if `session_id` and `full_response` are non-empty:
    - Strips thinking tags → `clean_text`.
    - Runs `extract_cards_from_response(full_response)` against the **raw** (unstripped) text and injects the resulting list under `metadata_dict["opportunity_cards"]`.
    - Persists the assistant message via `add_message`.
- Returns `StreamingResponse(database_persisting_generator(), media_type="text/event-stream")`.

> **Why "raw" for card extraction but "clean" for the persisted message?** Thinking-block text can legitimately contain pipe-delimited metadata that the model is *reasoning about*, and that reasoning is exactly what we want surfaced as cards. The stored message, however, should never leak `<think>` blocks to clients that replay history later.

### 3.8 `get_opportunities_list(...)` — `backend/main.py:290`

`GET /opportunities?query=&source=&limit=&offset=`.

| Query param | Type | Constraints | Default |
| --- | --- | --- | --- |
| `query` | `Optional[str]` | — | `None` (matches title or content via `LIKE %…%`) |
| `source` | `Optional[str]` | — | `None` (exact match on `source` column) |
| `limit` | `int` | `1 ≤ limit ≤ 100` | `20` |
| `offset` | `int` | `≥ 0` | `0` |

Delegates to `database.list_opportunities(...)` and wraps each row into an `OpportunityItem`.

### 3.9 `get_opportunity_detail(opp_id: int)` — `backend/main.py:308`

`GET /opportunities/{opp_id}`. Returns 404 with a descriptive `detail` if not found.

### 3.10 `trigger_scrape_job(payload, background_tasks)` — `backend/main.py:319`

`POST /scrape`.

- **Concurrency guard:** if `scrape_job_state["status"] == "running"`, short-circuits with a `ScrapeResponse(status="running", …)` describing the in-flight job (does not queue a second one).
- Otherwise schedules `_execute_scrape_job(days_back=payload.days_back, threshold=payload.score_threshold, reindex=payload.reindex)` on `background_tasks` and returns `ScrapeResponse(status="started", …)` immediately.

### 3.11 `get_scrape_job_status()` — `backend/main.py:347`

`GET /scrape/status` — returns the live `scrape_job_state` dict, mapped to `ScrapeResponse`.

### 3.12 `get_all_sessions()` — `backend/main.py:361`

`GET /sessions` — returns `list_sessions()` directly.

### 3.13 `create_new_session(payload: SessionCreate)` — `backend/main.py:369`

`POST /sessions` — creates a session with `payload.title or "New Chat"`. Note: `created_at` and `updated_at` are returned as empty strings; clients should refetch via `GET /sessions`.

### 3.14 `get_messages(session_id: str)` — `backend/main.py:383`

`GET /sessions/{session_id}/messages` — chronological list of messages.

### 3.15 `delete_chat_session(session_id: str)` — `backend/main.py:391`

`DELETE /sessions/{session_id}` — `database.delete_session` cascades to messages via `ON DELETE CASCADE` (`backend/database.py:68`). Returns `{"status": "deleted", "session_id": session_id}`.

### 3.16 `transcribe_audio(file: UploadFile)` — `backend/main.py:401`

`POST /transcribe` — multipart upload of an audio file.

1. Read full bytes (`await file.read()`).
2. `transcriber = get_transcriber()` — lazy-singleton accessor from `backend.stt`.
3. `result = transcriber.transcribe(audio_bytes)` — Moonshine inference.
4. Return `TranscribeResponse(**result)`.
5. On error: `HTTPException(500, "Speech transcription failed: …")`.

### 3.17 `__main__` CLI — `backend/main.py:419`

```python
parser.add_argument("--host",    default=settings.api.host)
parser.add_argument("--port",    type=int, default=settings.api.port)
parser.add_argument("--debug",   action="store_true", default=settings.debug)
parser.add_argument("--no-reload", action="store_false", dest="reload", default=True)
```

- `--debug` sets `os.environ["DEBUG"]="true"` and mutates `settings.debug = True` so downstream code can read either source.
- Boots uvicorn with `"backend.main:app"` (string form — required for `--reload` to pick up the app via the import string).

---

## 4. Flow / Lifecycle

### 4.1 Process startup

```
uvicorn worker starts
   │
   ▼
lifespan() startup phase
   ├─► init_db()         [creates sessions/messages/opportunities tables]
   └─► rag_pipeline.initialize()  [loads embeddings + vector store + LLM metadata]
   │
   ▼
ASGI server accepts connections
   │
   ▼
Request → RateLimitMiddleware → CORSMiddleware → route handler
```

### 4.2 Streaming chat lifecycle

```
client → POST /chat/stream
        │
        ▼
chat_stream(request) — synchronously:
        ├─ add_message(user) if session_id
        ├─ kick off rag_pipeline.stream_response(...) generator
        └─ return StreamingResponse
                                            │
                                            ▼
        client receives text/event-stream
        chunks are yielded one by one
                                            │
                                            ▼
        when generator exhausts:
        ├─ strip_thinking_tags(full_response) → clean_text
        ├─ extract_cards_from_response(raw)   → cards[]
        ├─ merge cards into metadata_dict
        └─ add_message(assistant, clean_text, metadata)
```

### 4.3 Scrape lifecycle

```
client → POST /scrape
        ├─ check scrape_job_state.status != "running"
        ├─ background_tasks.add_task(_execute_scrape_job, …)
        └─ respond ScrapeResponse(status="started", …)

(async) _execute_scrape_job
   ├─ state.status = "running"
   ├─ scraper = CombinedScraper(days_back, threshold)
   ├─ results = await scraper.run_all_scrapers()
   ├─ await scraper.await_enrichment()
   ├─ if reindex: rag_pipeline.reload_documents()
   └─ state.status ∈ {"completed", "failed"} + timestamp

client (later) → GET /scrape/status  → reads state dict
```

### 4.4 Shutdown

`lifespan` exits after `yield`, prints `[API] Shutting down FastAPI server…`. SQLite connections are closed by the `get_db_connection` context manager on each request; nothing else needs explicit teardown because the process is exiting.

---

## 5. Dependencies

| Import | Why |
| --- | --- |
| `json` | Parsing `[[METADATA]]` sidecar lines. |
| `sys`, `pathlib.Path`, `contextlib.asynccontextmanager` | Bootstrap when run as `python backend/main.py` (adds project root to `sys.path`). |
| `datetime.datetime` | ISO-8601 timestamps in `scrape_job_state` and `ScrapeResponse.scraped_at`. |
| `typing.List, Optional` | Type hints. |
| `fastapi.{FastAPI, HTTPException, APIRouter, BackgroundTasks, Query, UploadFile, File}` | Framework primitives. |
| `fastapi.middleware.cors.CORSMiddleware` | Browser CORS. |
| `fastapi.responses.StreamingResponse` | SSE-style streaming for `/chat/stream`. |
| `uvicorn` | ASGI server (used in `__main__`). |
| `backend.schemas.*` | Request/response DTOs. |
| `backend.stt.get_transcriber` | Moonshine STT singleton. |
| `backend.rate_limit.RateLimitMiddleware` | Sliding-window rate limiter. |
| `backend.rag.{RAGPipeline, DEVICE, DAYS_BACK, strip_thinking_tags}` | RAG engine + helpers. |
| `backend.database.{init_db, create_session, list_sessions, get_session_messages, add_message, delete_session, list_opportunities, get_opportunity_by_id}` | SQLite-backed persistence. |
| `scraper.CombinedScraper` | Multi-portal scrape + LLM enrichment. |
| `config.settings` | Typed Pydantic settings (host, port, rate limits, model URLs, …). |

---

## 6. Configuration / Environment Variables

Driven entirely by `config.py`; consumed via `settings.api.*`:

| Setting | Used in | Default |
| --- | --- | --- |
| `settings.api.host` | `__main__` `--host` default | `127.0.0.1` |
| `settings.api.port` | `__main__` `--port` default | `8000` |
| `settings.api.cors_origins` | `CORSMiddleware` | `["*"]` |
| `settings.api.rate_limit_enabled` | Whether to install middleware | `True` |
| `settings.api.rate_limit_trust_proxy` | `RateLimitMiddleware(trust_forwarded_for=…)` | `False` |
| `settings.api.rate_limit_chat_per_minute` | Tier `chat` | `10` |
| `settings.api.rate_limit_transcribe_per_minute` | Tier `transcribe` | `15` |
| `settings.api.rate_limit_scrape_per_minute` | Tier `scrape` | `5` |
| `settings.api.rate_limit_default_per_minute` | Tier `default` (everything else) | `120` |
| `settings.debug` | `--debug` default + per-request effective_debug override | `False` |
| `settings.model.ollama_base_url` | Echoed in `HealthResponse` | `http://localhost:11434` |
| `settings.model.llamacpp_server_url` | Echoed in `HealthResponse` | `http://localhost:8080` |

---

## 7. API Endpoints

Two URL spaces coexist: the documented **`/api/v1/*`** space (registered via the `router_v1` `APIRouter` and exposed in the OpenAPI schema) and the **legacy root-level aliases** (registered with `include_in_schema=False`). Both call the same handler, so the table below lists each path once per alias family.

| Method | Path (canonical) | Legacy aliases | Request | Response | Handler line |
| --- | --- | --- | --- | --- | --- |
| GET | `/api/v1/health` | `/health` | — | `HealthResponse` | `backend/main.py:131` |
| POST | `/api/v1/chat` | `/chat` | `ChatRequest` JSON | `ChatResponse` JSON | `backend/main.py:156` |
| POST | `/api/v1/chat/stream` | `/chat/stream`, `/api/chat/stream` | `ChatRequest` JSON | `text/event-stream` (raw chunks, may include `[[METADATA]]` lines) | `backend/main.py:227` |
| GET | `/api/v1/opportunities` | `/opportunities` | query params: `query`, `source`, `limit`, `offset` | `OpportunitiesResponse` | `backend/main.py:288` |
| GET | `/api/v1/opportunities/{opp_id}` | `/opportunities/{opp_id}` | path: `opp_id: int` | `OpportunityItem` or 404 | `backend/main.py:306` |
| POST | `/api/v1/scrape` | `/scrape` | `ScrapeRequest` JSON | `ScrapeResponse` | `backend/main.py:317` |
| GET | `/api/v1/scrape/status` | `/scrape/status` | — | `ScrapeResponse` | `backend/main.py:345` |
| GET | `/api/v1/sessions` | `/sessions`, `/api/sessions` | — | `List[SessionResponse]` | `backend/main.py:358` |
| POST | `/api/v1/sessions` | `/sessions`, `/api/sessions` | `SessionCreate` JSON | `SessionResponse` | `backend/main.py:366` |
| GET | `/api/v1/sessions/{session_id}/messages` | `/sessions/{session_id}/messages`, `/api/sessions/{session_id}/messages` | path: `session_id: str` | `List[MessageItem]` | `backend/main.py:380` |
| DELETE | `/api/v1/sessions/{session_id}` | `/sessions/{session_id}`, `/api/sessions/{session_id}` | path: `session_id: str` | `{"status": "deleted", "session_id": "…"}` | `backend/main.py:388` |
| POST | `/api/v1/transcribe` | `/transcribe`, `/api/transcribe` | multipart `file` upload | `TranscribeResponse` | `backend/main.py:398` |

**Rate-limit response shape (429):**

```json
{
  "detail": "Rate limit exceeded for 'chat' tier (10 requests per 60s). Retry later."
}
```

with headers `Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`.

**Successful responses on rate-limited routes also include** `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `X-RateLimit-Reset`.

---

## 8. Error handling

| Layer | Behavior |
| --- | --- |
| **Route handlers** | Each handler wraps its body in `try/except Exception` and re-raises as `HTTPException(status_code=500, detail=str(e))`. The error message is the raw exception `str()`. |
| **`chat_stream`** | The `try` only wraps *synchronous* setup; if the **inner generator** raises mid-stream, the SSE connection terminates without a final event (Starlette will surface a 500 to the underlying ASGI server). Improvements would wrap the generator too. |
| **Background scrape** | Exceptions inside `_execute_scrape_job` are caught and stored in `scrape_job_state["error"]`; the HTTP caller is *not* notified. |
| **`[[METADATA]]` parsing** | `json.loads` failures are silently ignored (kept best-effort). |
| **Health endpoint** | Defensive `hasattr` + `try/except` around `answer_cache.stats()` — never raises. |
| **Startup errors** | `lifespan` swallows all exceptions with a `print`, allowing the server to come up in a degraded state. |

---

## 9. Notable patterns / design decisions

- **Dual URL spaces (`/api/v1/*` + legacy root aliases).** Lets the OpenAPI doc stay clean while existing clients (Streamlit, mobile) keep their old URLs working. Each handler is decorated twice.
- **Middleware ordering.** `RateLimitMiddleware` is added **before** `CORSMiddleware` so 429 responses still ship CORS headers — important because Starlette applies middlewares in reverse order of insertion (the *last* added is the *outermost*).
- **`[[METADATA]]` sidecar inside the SSE stream.** A pragmatic out-of-band channel for cards, retrievers' debug info, etc., without changing the text stream's content type. Any line beginning with `[[METADATA]]` is stripped from `full_response` but kept in the yielded stream so the client UI can also read it.
- **`effective_debug = request.debug or settings.debug`.** Per-request override takes precedence; the CLI `--debug` flag sets the global default.
- **Per-tier rate limits.** Scraping (5/min) is the most expensive operation; chat (10/min) is moderate; reads default to a generous 120/min. Health and docs are exempt.
- **No global asyncio lock around `scrape_job_state`.** The current scrape concurrency is enforced by the `/scrape` route checking `state.status == "running"` rather than a lock. With a single Uvicorn worker this is safe; with multi-worker deployments the guard would be per-process.
- **Session auto-creation on first user message.** `add_message(... auto_create_session=True)` (`backend/database.py:138`) inspects the session table; if missing, inserts with a title derived from the first 35 characters of the user prompt. Title is then only auto-updated if it is still the literal `"New Chat"` (`backend/database.py:173`), preventing clobbering of user-set titles.
- **Cards extracted from raw (unstripped) text but stored clean.** Preserves any opportunity metadata buried inside `<|think|>…<|/think|>` reasoning while never persisting reasoning text into the chat history.
- **`check_same_thread=False`** on the SQLite connection (`backend/database.py:15`) is required because FastAPI's threadpool may service requests on different threads.