# Project Overview — Opportunities-Details

## 1. Purpose & Overview

**Opportunities-Details** is a **high-performance Retrieval-Augmented Generation (RAG) platform and web scraper** for global opportunity, scholarship, fellowship, and internship data. The system continuously scrapes six well-known opportunity portals, deduplicates and enriches the data with structured metadata (deadline, organization, location, funding type), indexes the content into a hybrid FAISS + BM25 vector store with cross-encoder reranking, and exposes a **chat assistant** that can answer natural-language questions about the indexed opportunities with streaming LLM responses and inline source attribution.

The platform targets the use case of a student or advisor who needs to ask questions like *"Are there any fully funded masters in Germany for fall 2026?"* and receive a grounded, cited answer drawn from the latest scraped content rather than the LLM's parametric memory.

The project ships three runtime layers:

- **Backend** — a FastAPI server (Python 3.12) that exposes the REST + SSE API, runs the multi-portal scraper, manages the SQLite database of opportunities and chat history, embeds documents with `sentence-transformers` / `e5-small-v2`, and orchestrates the LLM via `langchain` + `langgraph`. Supports both **Ollama** and **LLamaCPP (GGUF)** as the inference provider.
- **Frontend** — a single-file Streamlit UI (`streamlit_app.py`) with two tabs (Chat Assistant and Explore Opportunities), a sidebar for session and configuration management, a fixed-bottom voice recorder (Moonshine STT) that auto-transcribes speech to chat prompts, and a debug inspector that visualizes the prompt template, retrieved documents, and filled variables.
- **Storage** — a local SQLite database (`opportunities_chat.db`) for opportunity metadata and chat history, plus a FAISS index (`faiss_store/`) for vector search.

The repository also ships a **multi-stage Dockerfile**, a **three-service docker-compose.yml** (backend + frontend + Ollama), a **GitHub Actions CI pipeline** (ruff + pytest), a **`pyproject.toml` + `uv.lock` + `requirements.txt`** dependency trio, and a **`.env.example`** template for runtime configuration.

## 2. Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                       Opportunities-Details                        │
│                                                                    │
│  ┌─────────────┐    ┌──────────────────────────────────────────┐   │
│  │  Scrapers   │    │             Backend (FastAPI)             │   │
│  │  ─────────  │    │  ─────────────────────────────────────   │   │
│  │  youthop    │    │  /api/v1/health                          │   │
│  │  greatyop   │──► │  /api/v1/sessions (CRUD + messages)      │   │
│  │  scholars4d │    │  /api/v1/chat, /api/v1/chat/stream (SSE) │   │
│  │  scholars.. │    │  /api/v1/transcribe (Moonshine STT)      │   │
│  │  opportun.. │    │  /api/v1/scrape, /api/v1/scrape/status   │   │
│  │  opportun.. │    │  /api/v1/opportunities (search/list)     │   │
│  └─────────────┘    │                                          │   │
│                     │  Components:                              │   │
│                     │  - metadata_extractor.py (hybrid)         │   │
│                     │  - rag.py (FAISS + BM25 + rerank)         │   │
│                     │  - agent.py (tool-calling router)         │   │
│                     │  - stt.py (Moonshine ONNX)                │   │
│                     │  - database.py (SQLite)                   │   │
│                     │  - rate_limit.py (sliding window)        │   │
│                     │  - answer_cache.py (semantic LRU)         │   │
│                     └─────┬───────────────────┬─────────────────┘   │
│                           │                   │                     │
│                           ▼                   ▼                     │
│                ┌─────────────────┐  ┌────────────────────┐         │
│                │ SQLite DB       │  │  FAISS index       │         │
│                │ opportunities + │  │  + BM25 store      │         │
│                │ chat history    │  │  + parent docstore │         │
│                └─────────────────┘  └────────────────────┘         │
│                                                                    │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │           Frontend (Streamlit)                              │   │
│  │  ─────────────────────────────────                          │   │
│  │  streamlit_app.py                                           │   │
│  │  - Tab 1: Chat Assistant (text + Moonshine voice)           │   │
│  │  - Tab 2: Explore Opportunities (search/list)               │   │
│  │  - Sidebar: Sessions, Model, Scraper, Config                │   │
│  │  - Debug inspector (system prompt, retrieved docs, vars)   │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                    │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │           LLM Inference                                     │   │
│  │  ─────────────────────                                      │   │
│  │  Provider toggle (UI sidebar):                              │   │
│  │    • Ollama (http://ollama:11434 by default)                │   │
│  │    • LLamaCPP (local GGUF, Qwen3.5-4B-IQ4_NL.gguf)         │   │
│  └─────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────┘
```

**Deployment modes:**
- **Local dev** — `uvicorn backend.main:app` on port 8000, `streamlit run streamlit_app.py` on port 8501. Read `.env` from cwd.
- **Docker Compose** — `docker compose up -d` brings up `opportunities_backend` (8000), `opportunities_frontend` (8501), and `opportunities_ollama` (11434). Named volumes persist SQLite and Ollama model cache.

**End-to-end request flow (chat):**
1. User types a question or records voice in the Streamlit UI.
2. (Voice only) Audio is POSTed to `/api/v1/transcribe`; the Moonshine STT endpoint returns text.
3. The UI POSTs the prompt + last 6 messages to `/api/v1/chat/stream`.
4. The backend's **agent router** (`agent.py`) decides whether to use the local RAG pipeline (`search_local_opportunities`) or fall back to live web search (`search_live_web` via DuckDuckGo).
5. RAG retrieves top-k candidates from FAISS + BM25, reranks with a cross-encoder, and assembles context.
6. The LLM generates a response, streamed as Server-Sent Events. Tokens arrive at the UI; `<think>...</think>` blocks are rendered in a collapsible status; `Deadline: | Organization: | Location:` lines are formatted as colored pill badges.
7. A `[[METADATA]]` sentinel line is appended with `{is_opportunity, initial_docs, debug_info}` so the UI can render retrieved-document lists and the debug inspector.

## 3. Key Components (high level)

| Component | File | Role |
|---|---|---|
| **FastAPI app** | `backend/main.py` | Wires routes, middleware (CORS, rate limit), startup hooks. |
| **Settings** | `config.py` | `pydantic-settings` based, reads `.env` with `__` nested delimiter. |
| **Metadata extractor** | `backend/metadata_extractor.py` | Deterministic + LLM-fallback extraction of `deadline`, `organization`, `location`, `type`. |
| **RAG pipeline** | `backend/rag.py` | FAISS + BM25 ensemble retriever, cross-encoder reranking, parent-child chunking. |
| **Agent** | `backend/agent.py` | Level-1 fast router + 2-tool tool-calling agent (local RAG + live web). |
| **STT** | `backend/stt.py` | Moonshine ONNX, runs on CPU in ~125ms. |
| **Database** | `backend/database.py` | SQLite: opportunity table with SHA-256 dedup hash, chat session/message tables. |
| **Rate limiter** | `backend/rate_limit.py` | Per-IP sliding window, tiered limits, `429` + `Retry-After`. |
| **Semantic cache** | `backend/answer_cache.py` | Embedding-similarity LRU cache, 0.93 cosine threshold, TTL + epoch-based invalidation. |
| **Scrapers** | `scrapers/youthop.py`, `greatyop.py`, `scholars4dev.py`, `scholarshipscorner.py`, `opportunitiescorner.py`, `opportunitiesforyouth.py` | Per-portal async sitemap crawlers. |
| **Scraper orchestrator** | `scraper.py` | Concurrent fan-out across all 6 portals with content-hash dedup. |
| **Streamlit UI** | `streamlit_app.py` | Single-file UI: chat, opportunities browser, voice, debug inspector. |

The full feature list is in `features.md` (`features.md:1-79`); the most prominent currently-shipped capabilities include:

- **Async multi-portal scraping** with `aiohttp` + `BeautifulSoup` across 6 portals.
- **Hybrid metadata extraction** (~3 ms/item deterministic, with async LLM fallback for incomplete records).
- **Hybrid RAG with reranking** (FAISS dense + BM25 sparse + cross-encoder).
- **Dual inference engine** (Ollama + LLamaCPP GGUF).
- **Streaming chat API** (SSE).
- **SQLite persistent database** with SHA-256 deduplication.
- **Tool-calling agent** (Level-1 fast router + 2-tool native agent).
- **Moonshine STT** for offline voice input.
- **Streamlit multi-tab UI** with debug inspector.
- **Per-IP rate limiting** (zero extra deps, tiered).
- **Semantic answer cache** (strict 0.93 cosine threshold, LRU + epoch invalidation).
- **CI pipeline** (ruff + pytest on Python 3.12).
- **Incremental scraper runs** via cryptographic content hashes.
- **Vector DB persistence** (FAISS on disk).
- **Dynamic parent-child chunking** (500-char children, 80-char overlap).
- **`.env` configuration** via `pydantic-settings`.
- **Production Dockerfile** (multi-stage, non-root `appuser`, healthcheck).
- **Docker Compose orchestration** (backend + frontend + Ollama).

## 4. Lifecycle

### 4.1 First-time setup
```bash
# 1. Install dependencies (uv or pip)
uv sync
# or: pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# edit .env to point at your model file / Ollama URL

# 3. Pre-download embedding model (optional; happens on first request otherwise)
#    intfloat/e5-small-v2 via Hugging Face Hub

# 4. Pre-download LLM
#    - For Ollama: ollama pull qwen2.5:4b
#    - For LLamaCPP: place Qwen3.5-4B-IQ4_NL.gguf in ./models/

# 5. Pre-download STT model (optional)
#    UsefulSensors/moonshine-tiny via Hugging Face Hub
```

### 4.2 Local run
```bash
# Terminal 1 — backend
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

# Terminal 2 — frontend
streamlit run streamlit_app.py
# open http://localhost:8501
```

### 4.3 Docker Compose run
```bash
docker compose up -d
# wait for backend healthcheck
docker compose logs -f backend frontend ollama
# open http://localhost:8501
```

### 4.4 First-time data ingestion
From the Streamlit sidebar, click **Run Scraper** (`streamlit_app.py:416`). The backend runs all 6 scrapers concurrently, deduplicates by SHA-256, extracts metadata, and indexes into FAISS + SQLite. The semantic answer cache is invalidated on the new index epoch.

### 4.5 CI
GitHub Actions runs ruff (pinned `0.16.4`) and pytest on every push to `main`, every `v*` tag, and every PR. See `docs/ci_pipeline.md`.

## 5. Configuration / environment variables

The project reads all configuration from `.env` via `pydantic-settings`. The full reference is in `docs/environment_config.md`; the high-level groups are:

- **Scraper** — `SCRAPER__DAYS_BACK`, `SCRAPER__SCORE_THRESHOLD`, `SCRAPER__EXTRACT_METADATA`, `SCRAPER__LLM_ENRICHMENT`, etc.
- **Model** — `MODEL__EMBEDDING_MODEL`, `MODEL__MAIN_MODEL`, `MODEL__OLLAMA_BASE_URL`, `MODEL__LLAMACPP_SERVER_URL`, plus `STT_MODEL_NAME` and `STT_DEVICE`.
- **Semantic cache** — `SEMANTIC_CACHE_ENABLED`, `SEMANTIC_CACHE_SIMILARITY_THRESHOLD`, `SEMANTIC_CACHE_TTL_HOURS`, `SEMANTIC_CACHE_MAX_ENTRIES`.
- **API server** — `API__HOST`, `API__PORT`.
- **Rate limiting** — `API__RATE_LIMIT_ENABLED`, `API__RATE_LIMIT_TRUST_PROXY`, `API__RATE_LIMIT_CHAT_PER_MINUTE`, `API__RATE_LIMIT_TRANSCRIBE_PER_MINUTE`, `API__RATE_LIMIT_SCRAPE_PER_MINUTE`, `API__RATE_LIMIT_DEFAULT_PER_MINUTE`.
- **Debug** — `DEBUG` (or `--debug` / `-d` CLI flag).

The current `version` is **0.5.0** (`pyproject.toml:3`).

## 6. Network / API calls

The backend exposes the following surface (all under `/api/v1`):

- `GET /health` — liveness + `docs_count`, `device`, cache stats.
- `GET /sessions`, `POST /sessions`, `DELETE /sessions/{id}` — chat session CRUD.
- `GET /sessions/{id}/messages` — message history.
- `POST /chat`, `POST /chat/stream` — chat (SSE on the streaming variant).
- `POST /transcribe` — multipart audio → text (Moonshine STT).
- `POST /scrape`, `GET /scrape/status` — background scraper control.
- `GET /opportunities`, `GET /opportunities/{id}` — corpus browser.
- `GET /docs`, `GET /openapi.json` — Swagger UI and OpenAPI schema (exempt from rate limit).

The frontend (`streamlit_app.py`) calls these endpoints over HTTP. The backend calls Ollama at `OLLAMA_BASE_URL` and an optional remote LLamaCPP server at `LLAMACPP_SERVER_URL` for inference, and `sentence-transformers` fetches the embedding model from Hugging Face on first use. The full inventory is in `docs/streamlit_frontend.md` §6.

## 7. Error handling / fallbacks

- **Backend offline** — the Streamlit UI shows `Cannot connect to FastAPI backend. Is it running?` (`streamlit_app.py:447`) and disables interactive features while still rendering the configuration UI.
- **Ollama unreachable** — chat endpoint returns 5xx with the underlying error. The user can switch to the LLamaCPP provider from the sidebar.
- **GGUF missing** — `config.py:45-60` (`resolved_main_model_path`) checks four candidate paths. If none exist, the provider fails at load time with a clear error.
- **Rate limit breach** — `HTTP 429` with `Retry-After` and `X-RateLimit-*` headers. The UI surfaces the error inline.
- **Empty voice transcription** — the UI shows `st.warning("No speech detected in audio. Please speak clearly and try again.")` (`streamlit_app.py:643`).
- **Semantic cache wrong hit** — mitigated by the strict `0.93` cosine threshold, multi-turn bypass, and debug-request bypass.
- **Vector index missing on startup** — the first scraper run will create the index. Pre-existing FAISS files in `faiss_store/` are loaded on startup.
- **Cross-portal duplicates** — SHA-256 content hash dedup in `scraper.py` prevents the same post from being indexed twice.
- **Scraper portal down** — per-portal failures are caught; the other 5 portals continue. The plan (per `features.md:37`) is to add a per-portal health report.

## 8. Notable design decisions

1. **RAG-first, not LLM-first.** The agent router (`agent.py`) is biased toward the local indexed corpus. Only when the local context is insufficient does it call the live web search tool (`search_live_web`). This keeps answers grounded, current-with-the-scrape, and free from hallucination as much as possible.

2. **Dual inference providers.** The sidebar lets the user toggle between **Ollama** and **LLamaCPP (GGUF)**. Ollama is friendlier for swapping models; LLamaCPP is friendlier for fully offline, GPU-customized inference. The default is Ollama for ease of setup.

3. **Strict semantic cache (0.93 cosine).** A wrong cached answer is worse than a slow fresh one, so the threshold is set very high. Multi-turn and debug requests bypass the cache entirely. The cache is invalidated by an epoch bump on every corpus re-index.

4. **Per-IP rate limiting with explicit proxy distrust.** The default `RATE_LIMIT_TRUST_PROXY=false` prevents attackers from spoofing `X-Forwarded-For` to fragment their quota bucket. Operators behind a reverse proxy must explicitly opt in.

5. **Single-file Streamlit UI.** All UI logic is in `streamlit_app.py` (794 lines). This is a deliberate trade-off: a single file is easy to navigate and modify, but a future React/Next.js migration (`features.md:55`) is a planned improvement for production-grade engineering.

6. **Two-tab layout with progressive disclosure.** All chat/scraping/voice functionality is in Tab 1; the corpus browser is in Tab 2. Keeps the chat surface calm while exposing the indexed corpus to curious users.

7. **Single Docker image, two services.** The backend and frontend build from the same `Dockerfile` and differ only in their `command`. This halves the build cache footprint and ensures parity between dev and prod.

8. **Multi-stage Docker build, non-root runtime.** The `builder` stage has `build-essential` and `gcc` for compiling Python wheels; the `runtime` stage drops them and runs as `appuser` (uid 1000). Image is slim and security-scanner-friendly.

9. **Healthcheck-driven Compose dependency.** The frontend's `depends_on: backend: { condition: service_healthy }` (`docker-compose.yml:38-40`) prevents the user from opening a UI that returns 503s because Uvicorn hasn't bound the socket yet.

10. **`.env` for runtime config, `pyproject.toml` for build-time.** Clean separation. `.env.example` is the canonical reference; the real `.env` is gitignored and operator-controlled.

11. **CI on Python 3.12 only, with a minimal `pip install` set.** The CI runner does not install `torch` or `transformers` to keep build times fast. Tests that need those libraries must either mock them or live in a separate integration job.

12. **Incremental scraping by content hash.** `scraper.py` tracks SHA-256 of previously-scraped posts; only new/changed content is re-embedded. This makes the daily scrape cost a function of how many new posts landed, not the total corpus size.

13. **No authentication yet.** Per `features.md:46`, API-key or OAuth2 JWT is on the roadmap. For now, the platform is designed for self-hosted single-user (or small-team) use.

14. **Pinned ruff version in CI.** `version: "0.16.4"` (`.github/workflows/ci.yml:28`) makes CI reproducible — a new ruff release cannot turn a green build red overnight.

15. **MIT License.** Open and permissive. See `LICENSE` (21 lines) — `Copyright (c) 2026`. Free for use, modification, distribution, sublicensing, and sale, with the standard "as-is, no warranty" disclaimer.

## 9. Roadmap (selected from `features.md`)

The `features.md` file (79 lines) tracks a large backlog. Highlights, grouped by area:

- **Architecture & pipeline** — Decoupled scheduled ingestion (APScheduler / Celery / GitHub Actions cron); near-duplicate detection across portals (rapidfuzz + embedding cosine); per-portal health monitoring.
- **Search & retrieval** — Structured metadata filtering end-to-end (`?country=…&deadline_after=…`); query understanding layer (rewriting, HyDE, constraint extraction); clickable citation links; RAG evaluation harness (Ragas).
- **API & AI safety** — API authentication (API-key / OAuth2); prompt-injection defense; output guardrails.
- **Infrastructure & observability** — Expanded test suite (FastAPI endpoint tests via `httpx` / `TestClient`); Prometheus `/metrics` + Grafana; OpenTelemetry traces; structured JSON logging.
- **Frontend & product** — React/Next.js migration; deadline reminder service (email / Telegram); user profile & personalized ranking; developer-mode UI switch.
- **Agentic AI** — On-device micro-LLM tool router (`cactus-compute/needle`); Corrective RAG (CRAG) with self-reflection; multi-agent orchestration (Matcher / Eligibility / Application Roadmap); autonomous web scraping agent (Crawl4AI / Browser Use).
- **Advanced tooling** — Autonomous document & motivation letter generator; self-healing scraper agent; Model Context Protocol (MCP) server; full-duplex voice (STT → LLM → TTS); deep research report generator; extended attribute extraction (stipend, duration, language); feedback-driven retrieval auto-tuning.

The platform's current state (`0.5.0`) is a fully working end-to-end RAG + scraper + UI stack, with the architecture-decoupling, observability, and agentic features queued for the next milestones.

## 10. License

```
MIT License
Copyright (c) 2026

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- **License:** MIT (`LICENSE:1-21`).
- **Year stated:** 2026.
- **Permissions:** use, copy, modify, merge, publish, distribute, sublicense, sell.
- **Condition:** copyright + permission notice must accompany substantial copies.
- **Warranty:** none — software is provided "as is".
