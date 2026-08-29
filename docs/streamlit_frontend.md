# Streamlit Frontend — `streamlit_app.py`

## 1. Purpose & Overview

`streamlit_app.py` is the **primary user-facing web UI** for the Opportunities-Details RAG platform. It is a single-file Streamlit application that:

- Provides a chat assistant UI that streams token-by-token responses from a FastAPI backend (`/api/v1/chat/stream`).
- Renders inline **stylized "pill" badges** (deadline, organization, location, type) parsed from assistant messages.
- Provides a second tab, **Explore Opportunities**, that lists and searches the SQLite-indexed corpus via `/api/v1/opportunities`.
- Provides a **voice input** mode that captures browser audio, sends it to the backend Moonshine STT endpoint, and auto-fills the chat input with the transcribed text.
- Provides a sidebar for **session management** (list / select / create / delete chat sessions), **inference options** (Think Mode, Rerank, Provider: Ollama vs LlamaCPP), **background scraper trigger**, and **backend URL configuration**.
- Renders a **debug inspector** (when the debug flag is active) showing the final system prompt, final human prompt, retrieved document titles, and the filled template variables.

The frontend is intentionally thin: it never imports backend or scraper modules; all data flows through HTTP requests to the FastAPI service (`api/v1/...`).

## 2. Architecture

The Streamlit app is a **decoupled presentation layer** sitting on top of the FastAPI backend.

```
┌──────────────────────────────────────────────────────────┐
│  Browser                                                 │
│  ┌────────────────────────────────────────────────────┐  │
│  │  Streamlit (streamlit_app.py)                      │  │
│  │  - Tab 1: Chat (text + Moonshine voice)            │  │
│  │  - Tab 2: Explore Opportunities (search/list)      │  │
│  │  - Sidebar: Sessions, Model, Scraper, Config       │  │
│  └──────────────────┬─────────────────────────────────┘  │
└─────────────────────┼────────────────────────────────────┘
                      │  HTTP (requests)
                      ▼
┌──────────────────────────────────────────────────────────┐
│  FastAPI Backend  (http://<host>:<port>)                 │
│      /api/v1/health                                      │
│      /api/v1/sessions           (GET, POST, DELETE)      │
│      /api/v1/sessions/{id}/messages                      │
│      /api/v1/chat/stream          (SSE)                  │
│      /api/v1/transcribe          (multipart)             │
│      /api/v1/scrape              (POST, GET status)      │
│      /api/v1/opportunities       (GET, paginated)        │
└──────────────────────────────────────────────────────────┘
```

**Key architectural properties:**

- The base URL is **configurable** via the sidebar (`backend_api_url` widget) and stored in `st.session_state`. It defaults to `f"http://{settings.api.host}:{settings.api.port}"` (i.e. `http://127.0.0.1:8000` by default), constructed at `streamlit_app.py:18` and consumed at `streamlit_app.py:318`.
- On every page render, a 3-second health probe (`/api/v1/health`) is performed (`streamlit_app.py:327`); if it succeeds, sessions and document counts are loaded.
- The **chat input is fixed to the viewport bottom** via a custom CSS block (`streamlit_app.py:23-312`) that pins the `stChatInput` and `stAudioInput` Streamlit widgets to a unified bottom bar with matching dimensions, border, and shadow.
- **Auto-scroll** is implemented by injecting a `<div id="chat-bottom-anchor">` (`streamlit_app.py:583`) and a small `streamlit.components.v1.html` script (`streamlit_app.py:584-612`) that scrolls the parent window to that anchor immediately and on a schedule (50/150/350/700 ms) to defeat race conditions with Streamlit's incremental DOM updates.

## 3. Key Classes / Functions

The app is procedural; below are the named functions and structural blocks.

### 3.1 `clean_pill_text(val: str) -> str`  (`streamlit_app.py:449`)
- **Purpose:** Strips markdown emphasis (`**`, `*`, backticks) and trims whitespace; returns empty string for placeholders such as `"N/A"`, `"None"`, `"null"`, `"Unknown"`, `"Not specified"`.
- **Parameters:** `val` — any string (typically a metadata field from the LLM response).
- **Returns:** cleaned string or `""`.
- **Used by:** `render_pill_badges`, `format_message_with_inline_pills`.

### 3.2 `render_pill_badges(card: dict) -> str`  (`streamlit_app.py:459`)
- **Purpose:** Builds the inline HTML for colored "pill" badges for a single opportunity card.
- **Parameters:** `card` — dict with optional keys `deadline`, `organization`, `location`, `type`.
- **Returns:** `<div>…</div>` HTML string (with 4px top margin, 10px bottom margin) containing `<span>` pills with hard-coded palette (purple, blue, green, amber), or empty string if no fields.
- **Color mapping:**
  - `deadline` → purple `#f0e6ff` / `#6b21a8`
  - `organization` → blue `#e0f2fe` / `#0369a1`
  - `location` → green `#dcfce7` / `#15803d`
  - `type` → amber `#fef3c7` / `#b45309`

### 3.3 `format_message_with_inline_pills(text: str) -> str`  (`streamlit_app.py:480`)
- **Purpose:** Scans a chat response line by line. For any line containing the substrings `Deadline:`, `Organization:`, or `Location:`, it splits the line on `|` and converts each key/value pair into pill badges via `render_pill_badges`. Other lines are passed through unchanged.
- **Parameters:** `text` — the full assistant message.
- **Returns:** HTML-safe string with embedded pill HTML.
- **Used by:** `st.markdown(..., unsafe_allow_html=True)` for both the historical message render (`streamlit_app.py:567`) and the live token stream (`streamlit_app.py:736`).

### 3.4 `render_debug_inspector(meta: dict) -> None`  (`streamlit_app.py:516`)
- **Purpose:** Renders a Streamlit `st.expander` with four nested tabs that visualize internal LLM pipeline state.
- **Parameters:** `meta` — the `metadata` block returned by the backend.
- **Tabs (`streamlit_app.py:525`):**
  1. **System Prompt** — `dbg.get("formatted_system_prompt")` rendered in a `st.code` markdown block.
  2. **Human Prompt** — `dbg.get("formatted_human_prompt")` in a `st.code` block.
  3. **Retrieved Docs** — numbered list of `meta["initial_docs"]` titles.
  4. **Variables & Context** — JSON dump of `dbg["filled_variables"]` plus a `st.code` block of `dbg.get("context_text_snippet")`.
- **Header caption** (`streamlit_app.py:523`) prints `Route Target`, `Opportunity Prompt` flag, and `Used Tools` list.

### 3.5 Inline UI blocks (sidebar)

| Block | Lines | Description |
|---|---|---|
| **Session selector** | `streamlit_app.py:342-398` | `st.selectbox` + ➕/🗑️ buttons; talks to `/api/v1/sessions`. |
| **Model & RAG options** | `streamlit_app.py:401-408` | `st.toggle` for Think / Rerank, `st.selectbox` for provider, debug-mode caption. |
| **Background scraper** | `streamlit_app.py:411-434` | Two buttons: `Run Scraper` (POST `/api/v1/scrape`) and `Check Status` (GET `/api/v1/scrape/status`). |
| **Configuration** | `streamlit_app.py:437-447` | `st.text_input` for `backend_api_url`; status pill showing `docs_count` and `device`. |

### 3.6 Chat-input blocks

- **Voice input** (`streamlit_app.py:614-648`): `st.audio_input` with key `f"moonshine_voice_recorder_{rec_key}"`. On new audio, computes a stable hash, POSTs to `/api/v1/transcribe` as `multipart/form-data` with field name `file` and MIME type `audio/wav`, and on success stores `transcribed_query_text` in `st.session_state`. The widget key is incremented (`stt_rec_key += 1`) to reset the recorder's UI to 00:00 for the next cycle.
- **Text input** (`streamlit_app.py:651`): `st.chat_input("Ask about scholarships, internships, or opportunities...")`.
- **Combined prompt** (`streamlit_app.py:652`): `prompt = voice_prompt or text_prompt`. Voice input has priority — if a transcribed text exists, the text-input box is ignored for that cycle.

## 4. Flow / Lifecycle

### 4.1 App startup (every rerun)
1. **CLI / env debug flag** is resolved (`streamlit_app.py:11-16`). It returns `True` if `--debug` or `-d` is in `sys.argv`, or `DEBUG` env var is truthy, or `settings.debug` is True.
2. `DEFAULT_API_URL` is computed from `settings.api.host`/`settings.api.port` (`streamlit_app.py:18`).
3. `st.set_page_config(...)` is called (`streamlit_app.py:20`).
4. The custom CSS block (`streamlit_app.py:23-312`) is injected via `st.markdown(..., unsafe_allow_html=True)`.
5. The page title is set (`streamlit_app.py:315`).
6. `api_url` is loaded from `st.session_state.backend_api_url` (default: `DEFAULT_API_URL`) (`streamlit_app.py:318`).
7. A health probe is performed (`streamlit_app.py:326-339`): `GET /api/v1/health`. On 200, `docs_count` and `device` are stored, and `GET /api/v1/sessions` populates `sessions_list`. Any exception is silently swallowed.

### 4.2 Sidebar build
- The active session id is initialized to the first session id or a fresh `uuid.uuid4()` if there are no sessions yet (`streamlit_app.py:344-348`).
- The session `selectbox` shows `"<title> (<first 6 hex of id>)"`, with a "Current Active Session" label for unknown ids.
- A `+` button creates a new session via `POST /api/v1/sessions` and reassigns `active_session_id` (`streamlit_app.py:378-387`).
- A `🗑️` button deletes the active session via `DELETE /api/v1/sessions/{id}` and reassigns a fresh `uuid` (`streamlit_app.py:389-398`).

### 4.3 Chat tab runtime
1. **History fetch** (`streamlit_app.py:551-558`): `GET /api/v1/sessions/{active_session_id}/messages`. Returns a list of `{role, content, metadata}` dicts.
2. **Render loop** (`streamlit_app.py:562-579`): For each message, an `st.chat_message` is opened. If role is `assistant`, the content is run through `format_message_with_inline_pills` and rendered with `unsafe_allow_html=True`. If metadata contains `debug` or `debug_info`, `render_debug_inspector` is invoked. If metadata is an opportunity, an expander with `Retrieved Document Titles` would render (currently commented out at `streamlit_app.py:576-579`).
3. **Auto-scroll** (`streamlit_app.py:582-612`): a 0×0 iframe is injected that scrolls to the `chat-bottom-anchor` div.
4. **Voice capture** (see §3.6). On a successful STT response, the transcribed text is stored and the widget key is incremented.
5. **Prompt submission** (`streamlit_app.py:654-755`): When a prompt is present (voice or text), the user message is rendered inline. A `history_payload` is built from the last 6 messages (`streamlit_app.py:662`). A streaming `POST /api/v1/chat/stream` is opened with `stream=True`, `timeout=120` (`streamlit_app.py:684`).

### 4.4 Token streaming lifecycle
- The backend streams SSE-style chunks. Each chunk is iterated with `resp.iter_content(chunk_size=None, decode_unicode=True)` (`streamlit_app.py:688`).
- **Metadata sentinel:** lines beginning with `[[METADATA]]` are intercepted (`streamlit_app.py:692-703`); everything after the sentinel on the same line is parsed as JSON and stored as `metadata_received`. The remaining lines of the chunk are appended to `full_raw_response`.
- **Plain text:** all other chunks are appended to `full_raw_response` (`streamlit_app.py:705`).
- **Think-tag parsing** (`streamlit_app.py:709-725`): The accumulated response is searched for `<think>` and `</think>`. If both are present, the content between them becomes `thinking_content` (rendered in an `st.status("Thinking...", expanded=True)` block, `streamlit_app.py:728-733`); everything after `</think>` is `response_content` (rendered in the `response_placeholder` with pill formatting). If only `<think>` has been seen, `response_content` is empty and only the thinking content renders.
- **Completion:** when the loop ends, the thinking status is collapsed to `Thinking finished` if it hadn't been collapsed already (`streamlit_app.py:739-740`). If metadata is present and the assistant produced an opportunity, a `Retrieved Document Titles` expander is rendered (`streamlit_app.py:742-751`). Finally, `st.rerun()` is called (`streamlit_app.py:752`) to refresh the page so the new message appears in the persisted history fetch.

### 4.5 Explore Opportunities tab
1. `st.text_input` for keyword search + `st.selectbox` for page size (10/20/50) (`streamlit_app.py:762-766`).
2. `GET /api/v1/opportunities?limit=<n>&query=<kw>` is called (`streamlit_app.py:774`).
3. `total` and `items` are unpacked; an `st.info` shows the count.
4. Each item is rendered as an `st.expander` with title `#<id> - <title>`, a markdown source link, a caption with `source` and `created_at`, and the first 1500 chars of `content` (`streamlit_app.py:782-788`).

### 4.6 Shutdown
There is no explicit shutdown logic. Streamlit tears down the page on browser close / rerun. The audio recorder and the SSE stream are dropped with the page.

## 5. Configuration / Environment Variables

| Variable | Used at | Effect |
|---|---|---|
| `DEBUG` | `streamlit_app.py:11-16` | When `"true"/"1"/"yes"`, enables `debug_mode` (sends `debug=true` in chat payloads and surfaces the debug inspector). |
| `--debug` / `-d` (CLI) | `streamlit_app.py:12-13` | Equivalent to setting `DEBUG=true` for one run. Pass via `streamlit run streamlit_app.py -- --debug`. |

Configuration is also pulled from the imported `config.py` settings (used in `streamlit_app.py:8` and `:18`):

- `settings.api.host` / `settings.api.port` — compose `DEFAULT_API_URL`.
- `settings.debug` — extra source of truth for the debug flag.

The `backend_api_url` is **user-overridable** in the sidebar at runtime (`streamlit_app.py:439`).

## 6. API calls (full inventory)

All endpoints are prefixed with `{api_url}` (sidebar-configurable, default `http://127.0.0.1:8000`).

| # | Method | Path | Trigger | Body / Params | Response handling |
|---|---|---|---|---|---|
| 1 | `GET` | `/api/v1/health` | Every page load (`streamlit_app.py:327`) | none | Sets `backend_online=True`, reads `docs_count` and `device`. |
| 2 | `GET` | `/api/v1/sessions` | Every page load (`streamlit_app.py:335`) | none | Populates `sessions_list` (array of `{session_id, title}`). |
| 3 | `POST` | `/api/v1/sessions` | `+` button (`streamlit_app.py:382`) | `{"title": "New Chat"}` | Best-effort; failures swallowed. |
| 4 | `DELETE` | `/api/v1/sessions/{sid}` | `🗑️` button (`streamlit_app.py:392`) | none | Best-effort; failures swallowed. |
| 5 | `GET` | `/api/v1/sessions/{sid}/messages` | Chat tab render (`streamlit_app.py:554`) | none | Populates `messages` for history render. |
| 6 | `POST` | `/api/v1/transcribe` | New audio captured (`streamlit_app.py:630`) | `multipart/form-data` with `file=(voice_query.wav, audio/wav)` | On 200, `t_data["text"]` becomes the voice prompt; on non-200, an `st.error` is shown. |
| 7 | `POST` | `/api/v1/chat/stream` | User submits prompt (`streamlit_app.py:684`) | `json={"prompt", "session_id", "conversation_history"(last 6), "provider", "think", "rerank", "debug"}` | SSE stream; tokens accumulated; `[[METADATA]]` lines parsed; thinking tags parsed; pills rendered live. |
| 8 | `POST` | `/api/v1/scrape` | `Run Scraper` button (`streamlit_app.py:418`) | `{"days_back": 30, "score_threshold": 0.7}` | `st.sidebar.info` on 200; `st.sidebar.error` otherwise. |
| 9 | `GET` | `/api/v1/scrape/status` | `Check Status` button (`streamlit_app.py:429`) | none | Reads `status` and `items_scraped`; shows in `st.sidebar.info`. |
| 10 | `GET` | `/api/v1/opportunities` | Explore tab render (`streamlit_app.py:774`) | `params={limit, offset:0, query?}` | Renders items as expanders. |

**Example chat payload** (`streamlit_app.py:666-674`):
```json
{
  "prompt": "Are there any fully funded masters in Germany?",
  "session_id": "7d2c6c4b-1f3a-4e8c-9d31-7c2aabf9d5b1",
  "conversation_history": [
    {"role": "user", "content": "...", "metadata": null},
    {"role": "assistant", "content": "...", "metadata": null}
  ],
  "provider": "Ollama",
  "think": false,
  "rerank": true,
  "debug": false
}
```

**Example SSE event** parsed by the UI:
```
data: Hello there!
data: <think>The user is asking about Germany...</think>
data: <think>The user is asking about Germany...</think>Here are some options:
data: Deadline: 2026-12-31 | Organization: DAAD | Location: Germany
data: ...
data: [[METADATA]]{"is_opportunity": true, "initial_docs": ["DAAD 2026"], "debug_info": {...}}
```

## 7. Error handling / fallbacks

- **Health probe failure** (`streamlit_app.py:326-339`): a `try/except` wraps the call; on any failure `backend_online` stays `False`, and the sidebar shows `Cannot connect to FastAPI backend. Is it running?` (`streamlit_app.py:447`). Chat and Explore tabs both branch on `backend_online`.
- **Session create/delete failures** (`streamlit_app.py:382, 392`): exceptions are swallowed; the UI still creates a local UUID so the user is not blocked.
- **Message-history fetch failure** (`streamlit_app.py:552-558`): `messages` becomes `[]`; the chat renders empty.
- **Voice/STT failure** (`streamlit_app.py:628-648`): HTTP errors show `st.error` with the status code + body; network errors show `st.error("Failed to reach STT endpoint: ...")`. Empty transcriptions show `st.warning("No speech detected ...")`. The recorder key is incremented on every outcome so the UI resets.
- **Streaming chat error** (`streamlit_app.py:684-755`): non-200 responses print `st.error("API returned error status N: ...")`. Network exceptions print `st.error("Failed to communicate with API backend: ...")`. The thinking status is finalized even on error path (the `if thinking_status is not None and not thinking_completed` block at `streamlit_app.py:739` runs only inside the `else` branch — meaning a stream that errors out before the first token will leave an open `st.status`, which Streamlit auto-closes at rerun).
- **Opportunities fetch error** (`streamlit_app.py:790-792`): non-200 prints an error with the status code; network exceptions print `st.error("Error connecting to /api/v1/opportunities: ...")`.
- **Scraper status check failure** (`streamlit_app.py:433-434`): a `st.sidebar.warning("Status check failed.")` is shown.

## 8. Notable design decisions

1. **Stateless client over a coupled import.** The app does not import `backend.*` modules. It treats the FastAPI service as a black box. This means the same frontend can be pointed at any environment (local, Docker, remote) via the sidebar's `Backend API Base URL` field (`streamlit_app.py:439`).

2. **Bottom-bar voice + chat unification via raw CSS.** Because Streamlit's `stChatInput` and `stAudioInput` are independently positioned, the app injects ~290 lines of CSS (`streamlit_app.py:23-312`) to pin both to the same vertical level with matching 48px height, 12px border-radius, and a 10px gap. Empty sub-wrappers are explicitly `display:none` to prevent layout shifts. The submit button is recolored to the Streamlit accent `#ff4b4b` on hover.

3. **Auto-scroll via a zero-pixel iframe.** The `streamlit.components.v1.html` block at `streamlit_app.py:584-612` returns a 0×0 element. The CSS at `streamlit_app.py:302-311` hides the resulting iframe (height/width 0, opacity 0, pointer-events none) without preventing JavaScript from running — so the parent's scrollIntoView fires despite the iframe being visually invisible. The script retries at 50/150/350/700 ms to outlast Streamlit's component reconciliation.

4. **Pill-rendering heuristic over a strict schema.** The pill renderer is a tolerant string scanner, not a JSON validator (`streamlit_app.py:480-506`). This means the LLM does not need to emit perfect JSON; any line containing `Deadline:`, `Organization:`, or `Location:` separated by `|` will be styled. Placeholder tokens (`N/A`, `None`, `null`, `Unknown`, `Not specified`) are silently dropped by `clean_pill_text`.

5. **Two-tier thinking-token rendering.** During streaming, the UI treats `<think>...</think>` as ephemeral and renders them inside a collapsible `st.status` (default `expanded=True`). The status is auto-collapsed to `expanded=False` once the closing tag is seen (`streamlit_app.py:716-719`).

6. **Voice has priority over text.** `prompt = voice_prompt or text_prompt` (`streamlit_app.py:652`) means that if both a new transcription and a typed message exist in the same rerun, the transcribed voice query wins. The user can still type after the voice cycle ends (the next rerun starts with no `voice_prompt`).

7. **Stable audio hashing for idempotency.** Each audio file is fingerprinted by `f"{len(audio_bytes)}_{hash(audio_bytes[:64])}"` (`streamlit_app.py:625`) and compared against `last_processed_audio_hash` (`streamlit_app.py:626`) so the same recording is not transcribed twice during Streamlit's repeated reruns.

8. **Last-6-message history window.** Only the most recent 6 messages are sent as `conversation_history` (`streamlit_app.py:662`), keeping the prompt small and reducing token cost. The backend remains the source of truth for full history; the UI re-fetches on rerun.

9. **Two-tab layout as progressive disclosure.** All chat/scraping/voice functionality is in Tab 1; the database browser is in Tab 2. This keeps the chat surface calm while exposing the full indexed corpus to curious users.

10. **Comments are minimal in the file itself** (per project convention); this documentation is the canonical place to look up the per-block behavior.
