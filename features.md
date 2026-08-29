# Features & Roadmap

This document tracks the implemented features and planned enhancements for the **Opportunities Details** RAG & Scraping platform.

---

## ✅ Currently Implemented Features

- **Async Multi-Portal Scraping**: Concurrent sitemap scraping using `aiohttp` & `BeautifulSoup` across 6 portals ([youthop](file:///x:/Opportunities-Details/scrapers/youthop.py), [greatyop](file:///x:/Opportunities-Details/scrapers/greatyop.py), [scholars4dev](file:///x:/Opportunities-Details/scrapers/scholars4dev.py), [scholarshipscorner](file:///x:/Opportunities-Details/scrapers/scholarshipscorner.py), [opportunitiescorner](file:///x:/Opportunities-Details/scrapers/opportunitiescorner.py), [opportunitiesforyouth](file:///x:/Opportunities-Details/scrapers/opportunitiesforyouth.py)) managed by [scraper.py](file:///x:/Opportunities-Details/scraper.py).
- **Hybrid Metadata Extraction**: Two-stage pipeline extracting `deadline` (ISO-normalized via `dateparser`, multilingual month support EN/FR/ES/DE), `organization`, `location` (200+ country alias table), and `type` (category + funding coverage) at ~3ms/item via deterministic rules, with async local-LLM (Ollama JSON-mode) fallback enriching only incomplete records in the background ([metadata_extractor.py](file:///x:/Opportunities-Details/backend/metadata_extractor.py)).
- **Hybrid RAG & Reranking**: Dense FAISS vector search (`e5-small-v2`) combined with BM25 keyword search via `EnsembleRetriever` and `CrossEncoder` reranking ([rag.py](file:///x:/Opportunities-Details/backend/rag.py)).
- **Dual Inference Engine**: Provider toggle supporting both Ollama (`ChatOllama`) and LlamaCPP (`ChatLlamaCpp`, default GGUF: `models/Qwen3.5-4B-IQ4_NL.gguf`).
- **Streaming Chat API**: Server-Sent Events (SSE) token streaming via `/api/v1/chat/stream` alongside standard JSON `/api/v1/chat`.
- **FastAPI Backend REST API**: Exposed clean versioned REST endpoints (`/api/v1/chat`, `/api/v1/chat/stream`, `/api/v1/transcribe`, `/api/v1/opportunities`, `/api/v1/opportunities/{opp_id}`, `/api/v1/scrape`, `/api/v1/scrape/status`, `/api/v1/sessions` CRUD + messages, `/api/v1/health`) and root aliases ([main.py](file:///x:/Opportunities-Details/backend/main.py)) to decouple RAG backend logic from Streamlit frontend UI.
- **SQLite Persistent Database**: SQLite table storage for opportunity metadata with SHA-256 deduplication hash and structured search, plus persistent chat session/message history tables ([database.py](file:///x:/Opportunities-Details/backend/database.py)).
- **Tool-Calling Agent Framework**: Level 1 Fast Python Router (0.01ms) combined with a 2-tool native tool-calling agent (`search_local_opportunities` RAG pipeline + `search_live_web` DuckDuckGo fallback with HTML scraping fallback) replacing static regex prompt routing ([agent.py](file:///x:/Opportunities-Details/backend/agent.py), [rag.py](file:///x:/Opportunities-Details/backend/rag.py)).
- **Moonshine Speech-to-Text (STT)**: Offline local hands-free voice transcription using `UsefulSensors/moonshine-tiny` (~50MB) executing in ~125ms on CPU (leaving 100% VRAM for the LLM) with `/api/v1/transcribe` and browser microphone audio input ([stt.py](file:///x:/Opportunities-Details/backend/stt.py), [streamlit_app.py](file:///x:/Opportunities-Details/streamlit_app.py)).
- **Streamlit Multi-Tab Web App**: Searchable opportunities browser with styled metadata pills (deadline / location / funding badges), streaming chat UI with prompt & retrieved-document debug inspector, persistent session history sidebar, microphone voice input, and one-click background scraper trigger ([streamlit_app.py](file:///x:/Opportunities-Details/streamlit_app.py)).
- **Rate Limiting Middleware**: In-memory sliding-window rate limiter (zero extra dependencies) with cost-tiered per-client-IP limits — chat/stream 10/min, transcribe 15/min, scrape trigger 5/min, other endpoints 120/min — returning standard `X-RateLimit-*` headers, `429` + `Retry-After` on breach, optional `X-Forwarded-For` trust behind reverse proxies, and health/docs/CORS-preflight exemption. Client IP is taken from the socket by default; `X-Forwarded-For` headers are ignored unless explicitly trusted via `RATE_LIMIT_TRUST_PROXY=true`, preventing quota-bucket spoofing ([backend/rate_limit.py](file:///x:/Opportunities-Details/backend/rate_limit.py)).
- **Semantic Answer Cache**: Embedding-similarity cache serving near-identical single-turn questions instantly instead of re-running retrieval + generation — strict 0.93 cosine threshold on e5 query embeddings (configurable), scoped by model/provider config hash, invalidated by epoch bump on every corpus re-index plus TTL backstop and LRU entry cap; multi-turn and debug requests bypass it, hits replay the original `[[METADATA]]` payload flagged `cache_hit`, and stats surface on `/health` ([backend/answer_cache.py](file:///x:/Opportunities-Details/backend/answer_cache.py)).
- **CI Pipeline**: GitHub Actions workflow running pinned-version `ruff` lint and `pytest` (Python 3.12) on every push to `main`, version tags, and all pull requests ([.github/workflows/ci.yml](file:///x:/Opportunities-Details/.github/workflows/ci.yml)).
- **Incremental Scraper Run Support**: Track previously scraped content using cryptographic content hashes (SHA-256) to avoid duplicate scraping and re-embedding ([scraper.py](file:///x:/Opportunities-Details/scraper.py)).
- **Vector Database Persistence**: FAISS index saved and reloaded from disk (`faiss_store/`) so embeddings survive restarts ([rag.py](file:///x:/Opportunities-Details/backend/rag.py)).
- **Dynamic Document Chunking Strategy**: Smart parent-child document chunking (500-char child chunks with 80-char overlap mapped to a parent docstore) for long opportunity posts ([rag.py](file:///x:/Opportunities-Details/backend/rag.py)).
- **Environment Variable File (`.env`) Support**: Load configuration dynamically via `pydantic-settings` from `.env` instead of hardcoded Windows model paths ([config.py](file:///x:/Opportunities-Details/config.py), [.env.example](file:///x:/Opportunities-Details/.env.example)).
- **Production Dockerfile**: Multi-stage `Dockerfile` (builder + slim runtime) with non-root `appuser` execution, `EXPOSE 8000 8501`, and `HEALTHCHECK` on `/api/v1/health` ([Dockerfile](file:///x:/Opportunities-Details/Dockerfile)).
- **Docker Compose Orchestration**: `docker-compose.yml` launching the Streamlit frontend, FastAPI backend, and Ollama server together with named volumes for SQLite and Ollama storage ([docker-compose.yml](file:///x:/Opportunities-Details/docker-compose.yml)).



---

## 🚀 To Be Implemented

### 1. Architecture & Data Pipeline Quality
- [ ] **Decoupled Scheduled Ingestion**: Move `scraper.py` execution out of app initialization into a dedicated background worker (APScheduler / Celery beat / GitHub Actions cron) with persisted run history, retry/backoff on portal failures, and automatic RAG re-indexing when new data lands.
- [ ] **Near-Duplicate Detection & Source Health Monitoring**: Cross-portal fuzzy deduplication (`rapidfuzz` token similarity with embedding cosine fallback) to collapse the same opportunity republished on multiple portals, plus dead-URL pruning and a per-portal health report (success rate, item yield, last-success timestamp).

### 2. Search & Retrieval Intelligence
- [ ] **Structured Metadata Filtering End-to-End**: Expose the indexed `deadline`, `location`, `organization`, and `type` columns as query parameters on `GET /api/v1/opportunities` (e.g. `?country=Germany&deadline_after=2026-09-01&funding=Fully Funded`) with matching facet controls in the Streamlit browser, and apply the same filters as post-retrieval doc filtering inside the RAG pipeline so chat answers honor explicit constraints.
- [ ] **Query Understanding Layer**: LLM-driven query rewriting, HyDE hypothetical-document expansion, and constraint extraction (degree level, country, funding, timeframe) feeding both BM25 and dense retrievers before the ensemble merge.
- [ ] **Citation & Source Link Formatting**: Render clickable application URLs inline in chat responses (doc metadata already carries `url`; links currently render only in the Opportunities browser tab).
- [ ] **RAG Evaluation Harness**: Curated golden Q&A dataset scored with Ragas (faithfulness, context precision/recall, answer relevancy) runnable as a single script, so retrieval quality regressions are caught whenever models, prompts, or chunking change.

### 3. REST API Hardening & AI Safety
- [ ] **API Authentication**: API-key or OAuth2 JWT security on all `/api/v1` endpoints (per-client rate limiting already shipped).
- [ ] **Prompt-Injection Defense & Output Guardrails**: Treat scraped page content as untrusted input — strict delimiting of web text vs system instructions, instruction-stripping/heuristic detection of override attempts ("ignore previous instructions"), and response-side validation before SSE streaming. The pipeline currently feeds raw portal HTML straight into LLM context, making this a realistic threat model worth defending against.

### 4. Infrastructure, Observability & Testing
- [ ] **Expand Automated Test Suite**: Grow the existing 20-test metadata-extractor suite into FastAPI endpoint tests (`httpx`/`TestClient`), scraper contract tests with mocked HTTP responses (`respx`), and RAG retriever smoke/regression tests.
- [ ] **Metrics & Tracing**: Prometheus `/metrics` endpoint + Grafana dashboard (request latency, tokens/sec, scrape success rate per portal) with OpenTelemetry traces spanning scraper → indexer → retriever → LLM.
- [ ] **Structured Logging**: Replace `icecream` debug prints with `loguru`/stdlib JSON logging including request IDs for end-to-end correlation.

### 5. Frontend & Product Features
- [ ] **React/Next.js Frontend Migration**: Rebuild the Streamlit UI as a typed TypeScript app consuming the REST + SSE APIs — demonstrates true backend decoupling and reads as production-grade engineering.
- [ ] **Deadline Reminder Service**: Notification digest (email / Telegram bot) of saved-search matches whose deadlines fall within N days, powered directly by the indexed `deadline` column.
- [ ] **User Profile & Personalized Ranking**: Store nationality, GPA, and target degree once; re-rank retrieved opportunities against the profile and let the agent answer eligibility questions without repeating constraints every session.
- [ ] **Developer Mode UI Switch**: Hide prompt inspection expanders and retrieved document title expanders behind a developer debug toggle in Streamlit.

### 6. Agentic AI & Autonomous Workflows
- [ ] **On-Device Micro-LLM Tool Router (`cactus-compute/needle`)**: Integrate the 26M parameter ultra-lightweight `cactus-compute/needle` model (~14MB footprint) for local function calling, multi-tool selection, and natural language query parameter extraction (e.g., parsing country, degree level, and deadline into structured JSON filters) with ~10ms execution latency.
- [ ] **Corrective RAG (CRAG) & Self-Reflection**: Implement a grade-and-correct loop where an evaluator node verifies retrieved document quality and falls back to live web search if local context is insufficient, followed by a hallucination-checking node.
- [ ] **Multi-Agent Orchestration**: Divide complex requests into specialized sub-agents:
  - *Scholarship Matcher Agent*: Discovers opportunities across local and web sources.
  - *Eligibility Evaluator Agent*: Compares user GPA, nationality, and degree target against scholarship criteria.
  - *Application Roadmap Agent*: Drafts custom document checklists, SOP outlines, and submission timelines.
- [ ] **Autonomous Web Scraping Agent**: Deploy goal-driven browser agents (e.g. via Crawl4AI / Browser Use) capable of navigating complex portal pagination, solving dynamic rendering, and extracting structured schemas directly into the vector database.

### 7. Advanced Agentic Tooling & Autonomous AI Features
- [ ] **Autonomous Document & Motivation Letter Generator**: A tool-equipped agent (`draft_motivation_letter`) that takes user bio/resume + opportunity details, performs a requirement-gap analysis, and drafts tailored Statements of Purpose (SOP) or Letters of Motivation (LOM).
- [ ] **Self-Healing & Adaptive Scraper Agent**: When HTML layout changes cause scraper failures, a diagnostic agent fetches the page DOM via Playwright, uses an LLM to generate updated CSS/XPath selectors, tests them in a sandbox, and auto-patches the scraper code.
- [ ] **Model Context Protocol (MCP) Server**: Expose `search_local_opportunities`, `search_live_web`, scrape triggering, and metadata-filtered queries as an MCP server, so any external MCP client (Claude Desktop, VS Code, Cursor) can drive the platform directly — and conversely, allow the in-app agent to consume third-party MCP servers as additional tools.
- [ ] **Full-Duplex Voice Assistant (STT -> LLM -> TTS)**: Close the hands-free loop by adding streaming local text-to-speech (Kokoro or Piper, ~50MB CPU models) to the existing Moonshine STT path — spoken conversation mode in Streamlit with interruptible playback and sub-second first-audio latency, fully offline.
- [ ] **Deep Research Report Generator**: An agentic research mode that plans sub-queries from a single complex request (e.g., *"fully-funded AI masters in Europe, no GRE, fall intake"*), fans out parallel local-RAG + live-web searches per sub-query, dedupes and cross-verifies findings, then streams a cited Markdown comparison report downloadable from the chat UI.
- [ ] **Extended Attribute Extraction (Stipend, Duration, Language)**: Extend the hybrid metadata extractor with LLM-assisted extraction of monthly stipend amounts, program duration, language requirements, and degree level into dedicated SQLite columns — unlocking precise sorting, filtering, and eligibility matching across the whole corpus.
- [ ] **Feedback-Driven Retrieval Auto-Tuning**: Capture thumbs-up/down ratings on chat answers as relevance pairs in SQLite, then periodically re-optimize `EnsembleRetriever` weights (BM25 vs dense alpha), reranker score thresholds, and chunk sizes against accumulated signals — turning real usage into automatic retrieval-quality gains without manual tuning.



