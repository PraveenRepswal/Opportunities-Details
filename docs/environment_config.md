# Environment Configuration — `.env.example`

## 1. Purpose & Overview

`.env.example` is the **template for the project's runtime configuration**. It documents every environment variable that the application reads and shows the **default** value for each. The actual `.env` file (gitignored) is what developers and operators copy from this template to control scraper behavior, model selection, semantic caching, and the API server's bind/port/rate-limit settings.

All variables are read by `config.py` (`config.py:1-93`) via **`pydantic-settings`** (`BaseSettings` + `SettingsConfigDict(env_file=".env", env_nested_delimiter="__")`). The double-underscore `__` is the **nested-delimiter** that maps `SCRAPER__DAYS_BACK=30` to `settings.scraper.days_back`.

## 2. Architecture

```
┌──────────────────────────┐
│  .env (gitignored)       │  operator-controlled
│  or environment vars     │
└────────────┬─────────────┘
             │  pydantic-settings reads
             ▼
┌──────────────────────────────────────────────────────────────┐
│  config.py  (BaseSettings)                                  │
│  ├── ScraperSettings (config.py:7-26)                       │
│  │     days_back, score_threshold, extract_metadata,        │
│  │     llm_enrichment, llm_enrichment_concurrency,          │
│  │     llm_enrichment_timeout, metadata_content_chars,      │
│  │     urls (6 portals)                                     │
│  ├── ModelSettings (config.py:29-60)                        │
│  │     embedding_model, main_model,                         │
│  │     ollama_base_url, llamacpp_server_url,                │
│  │     stt_model, stt_device,                               │
│  │     semantic_cache_*                                     │
│  └── APISettings (config.py:63-76)                          │
│        host, port, cors_origins,                            │
│        rate_limit_*                                         │
│                                                              │
│  Settings (config.py:79-92)                                 │
│        debug, scraper, model, api                            │
└────────────┬─────────────────────────────────────────────────┘
             │  consumed by
             ▼
┌──────────────────────────────────────────────────────────────┐
│  Backend (FastAPI), Scraper, Streamlit UI                   │
│  - settings.scraper.* used by scraper.py and metadata_extractor
│  - settings.model.*   used by rag.py, stt.py                 │
│  - settings.api.*     used by main.py, rate_limit.py         │
│  - settings.debug     used by streamlit_app.py:11-16         │
└──────────────────────────────────────────────────────────────┘
```

Docker Compose passes a **subset** of these vars explicitly (`docker-compose.yml:14-17, 37`); the rest must be provided by a mounted `.env` file, an additional `env_file:` reference, or a Compose override.

## 3. Variable Reference

The variables below are grouped exactly as `.env.example` groups them.

### 3.1 Scraper Settings  (`.env.example:5-12`)

| Variable | Type | Default | Source code | Effect |
|---|---|---|---|---|
| `SCRAPER__DAYS_BACK` | `int` | `30` | `config.py:10` | How many days back from today to consider a portal post "recent" when scraping. Older posts are filtered out. |
| `SCRAPER__SCORE_THRESHOLD` | `float` | `0.7` | `config.py:11` | Minimum relevance score (0..1) a post must clear to be indexed. Higher = fewer, higher-quality items. |
| `SCRAPER__EXTRACT_METADATA` | `bool` | `true` | `config.py:13` | Whether to run the deterministic + LLM metadata extraction pipeline (deadline, organization, location, type). |
| `SCRAPER__LLM_ENRICHMENT` | `bool` | `true` | `config.py:14` | Whether incomplete metadata records are enriched by an async local-LLM call. |
| `SCRAPER__LLM_ENRICHMENT_CONCURRENCY` | `int` | `2` | `config.py:15` | Max concurrent LLM enrichment requests. Keeps the GPU/CPU from being swamped. |
| `SCRAPER__LLM_ENRICHMENT_TIMEOUT` | `float` | `45.0` | `config.py:16` | Per-request timeout (seconds) for the LLM enrichment call. |
| `SCRAPER__METADATA_CONTENT_CHARS` | `int` | `4000` | `config.py:17` | How many chars of post body to send to the LLM enricher. Larger = more context, more tokens. |

**Not exposed as env var (hard-coded in `config.py:19-26`):** the six scraper URLs (`youthop`, `greatyop`, `scholars4dev`, `scholarshipscorner`, `opportunitiescorner`, `opportunitiesforyouth`). These are the sitemap entry points for each portal. To change them, edit `config.py`.

### 3.2 Model & RAG Settings  (`.env.example:14-18`)

| Variable | Type | Default | Source code | Effect |
|---|---|---|---|---|
| `MODEL__EMBEDDING_MODEL` | `str` | `intfloat/e5-small-v2` | `config.py:32` | Hugging Face model id for the dense retriever. Loaded by `sentence-transformers`. |
| `MODEL__MAIN_MODEL` | `str` | `models/Qwen3.5-4B-IQ4_NL.gguf` | `config.py:33` | Path to the GGUF file used by the LLamaCPP provider. Docker Compose overrides this to `/app/models/Qwen3.5-4B-IQ4_NL.gguf` (`docker-compose.yml:17`). |
| `MODEL__OLLAMA_BASE_URL` | `str` | `http://localhost:11434` | `config.py:34` | URL of the Ollama server when the Ollama provider is selected. Docker Compose overrides this to `http://ollama:11434` (`docker-compose.yml:16`). |
| `MODEL__LLAMACPP_SERVER_URL` | `str` | `http://localhost:8080` | `config.py:35` | URL of an external LLamaCPP server, if the GGUF is hosted remotely rather than loaded in-process. |

**Not in `.env.example` but read in `config.py:36-37`:**
- `STT_MODEL_NAME` (env var, default `UsefulSensors/moonshine-tiny`).
- `STT_DEVICE` (env var, default `cpu`; can be `cpu`, `cuda`, or `auto`).

These should ideally be added to `.env.example` for completeness.

### 3.3 Semantic Answer Cache  (`.env.example:20-26`)

The semantic cache is a per-process embedding-similarity cache. On a single-turn request, the system embeds the prompt and looks for a near-identical previously-answered question. If the cosine similarity is above `SEMANTIC_CACHE_SIMILARITY_THRESHOLD`, the cached answer is replayed instead of re-running retrieval + LLM generation.

| Variable | Type | Default | Source code | Effect |
|---|---|---|---|---|
| `SEMANTIC_CACHE_ENABLED` | `bool` | `true` | `config.py:40` | Master switch. `false` disables the cache entirely. |
| `SEMANTIC_CACHE_SIMILARITY_THRESHOLD` | `float` | `0.93` | `config.py:41` | Cosine similarity cutoff. The comment in `.env.example:22-23` warns: *"Keep strict (>=0.90): a wrong hit is worse than a slow miss."* |
| `SEMANTIC_CACHE_TTL_HOURS` | `float` | `24` | `config.py:42` | Time-to-live for cache entries. Older entries are evicted. |
| `SEMANTIC_CACHE_MAX_ENTRIES` | `int` | `500` | `config.py:43` | LRU cap on the cache size. |

Cache invalidation:
- Entries are **invalidated by an epoch bump** on every corpus re-index (the LLM generator's `on_reindex()` resets the cache).
- TTL is a backstop.
- Multi-turn and debug requests bypass the cache entirely.

### 3.4 API Server Settings  (`.env.example:28-30`)

| Variable | Type | Default | Source code | Effect |
|---|---|---|---|---|
| `API__HOST` | `str` | `127.0.0.1` | `config.py:66` | Bind address. Set to `0.0.0.0` to accept non-loopback connections (required inside Docker, `docker-compose.yml:14`). |
| `API__PORT` | `int` | `8000` | `config.py:67` | TCP port. The Dockerfile exposes both 8000 and 8501 (`Dockerfile:49`). |

**Not in `.env.example` but read in `config.py:68`:**
- `cors_origins: List[str] = ["*"]` — currently always permissive; not env-driven.

### 3.5 Rate Limiting  (`.env.example:32-40`)

A per-client-IP sliding-window rate limiter implemented in `backend/rate_limit.py` and configured by `APISettings` (`config.py:70-76`). The comment in `.env.example:36` explains the tiered structure: *"chat/streaming, STT transcription, scrape trigger, everything else (reads). /health and /docs are exempt."*

| Variable | Type | Default | Source code | Effect |
|---|---|---|---|---|
| `API__RATE_LIMIT_ENABLED` | `bool` | `true` | `config.py:71` | Master switch. When `false`, the middleware is bypassed. |
| `API__RATE_LIMIT_TRUST_PROXY` | `bool` | `false` | `config.py:72` | When `true`, the client IP is taken from the `X-Forwarded-For` header (set by reverse proxies). When `false` (default), the socket IP is used, preventing header-spoofing attacks that would otherwise split a single user into many quota buckets. |
| `API__RATE_LIMIT_CHAT_PER_MINUTE` | `int` | `10` | `config.py:73` | Max chat/stream requests per minute per IP. |
| `API__RATE_LIMIT_TRANSCRIBE_PER_MINUTE` | `int` | `15` | `config.py:74` | Max STT requests per minute per IP. |
| `API__RATE_LIMIT_SCRAPE_PER_MINUTE` | `int` | `5` | `config.py:75` | Max scrape-trigger requests per minute per IP. Scraping is the most expensive operation. |
| `API__RATE_LIMIT_DEFAULT_PER_MINUTE` | `int` | `120` | `config.py:76` | Catch-all limit for any other endpoint (read endpoints, list ops, etc.). |

On breach: the middleware returns `HTTP 429 Too Many Requests` with a `Retry-After` header, plus standard `X-RateLimit-*` headers on every response.

### 3.6 Debug / Behavior  (`.env.example:1-3` and others)

| Variable | Where | Effect |
|---|---|---|
| `DEBUG` | read in `config.py:87` and `streamlit_app.py:11-16` | When `"true"/"1"/"yes"`, enables debug mode in the backend and surfaces the debug inspector in the Streamlit UI. |
| `--debug` / `-d` (CLI) | `streamlit_app.py:12-13` | Equivalent to `DEBUG=true` for one run, via `streamlit run streamlit_app.py -- --debug`. |

`DEBUG` is not in the current `.env.example` template; add it as `DEBUG=false` if you want to ship a complete reference.

## 4. Lifecycle

### 4.1 Local development
1. Copy the template: `cp .env.example .env`.
2. Edit `.env` as needed.
3. Run `uvicorn backend.main:app` — `pydantic-settings` reads `.env` from the current working directory on import.

### 4.2 Docker
Two paths:
- **Bind-mount `.env`:** add `- .env:/app/.env:ro` to the backend service's `volumes` in `docker-compose.yml`. The container will read `/app/.env` at startup.
- **Inline env vars:** pass them under the `environment:` key. The current `docker-compose.yml` does this for `API_HOST`, `API_PORT`, `OLLAMA_BASE_URL`, `MODEL__MAIN_MODEL`, and `BACKEND_API_BASE_URL` (`docker-compose.yml:14-17, 37`). All other settings take their `pydantic-settings` defaults.

### 4.3 Production / secrets
- `.env` is **gitignored** (see `.gitignore`). Secrets (API keys for OpenAI, future OAuth credentials) should be injected via the deployment platform's secret manager, not committed to the repo.
- `OLLAMA_BASE_URL` and `LLAMACPP_SERVER_URL` may include credentials in their URLs (e.g. `http://user:pass@host:port`); treat them as secrets when they do.

## 5. Configuration / environment variables

This section is the reference itself. The only meta-rule is:

> **Use double underscores `__` for nested keys.** The delimiter is set at `config.py:83` (`env_nested_delimiter="__"`). Single underscores are part of the leaf name. Example: `SCRAPER__DAYS_BACK` → `settings.scraper.days_back`.

> **Boolean parsing is permissive.** `pydantic-settings` treats `"true"/"1"/"yes"` (case-insensitive) as `True`. The `config.py` file also accepts these explicitly at `config.py:14, 40, 72, 87` (in case `pydantic-settings` semantics drift).

## 6. Network / API calls

The configuration file itself makes no API calls. The variables it defines drive outbound HTTP calls at runtime:

- `MODEL__OLLAMA_BASE_URL` — backend calls Ollama at `${OLLAMA_BASE_URL}/api/chat` when the Ollama provider is selected.
- `MODEL__LLAMACPP_SERVER_URL` — backend calls a remote LLamaCPP HTTP server (or loads the GGUF in-process via `ChatLlamaCpp`).
- `MODEL__EMBEDDING_MODEL` — on first use, `sentence-transformers` downloads the embedding model from `https://huggingface.co/intfloat/e5-small-v2`.
- `STT_MODEL_NAME` — `UsefulSensors/moonshine-tiny` is loaded from Hugging Face Hub on first STT request (or from a local cache if pre-downloaded).

## 7. Error handling / fallbacks

- **Unknown variables:** `extra="ignore"` is set in every `SettingsConfigDict` (`config.py:8, 30, 64, 84`). Variables in `.env` that do not correspond to a field are silently dropped, so old `.env` files do not crash on startup.
- **Bad type:** `pydantic-settings` raises a `ValidationError` at import time. The stack trace makes the offending variable obvious. Common cases: a non-numeric `API__PORT`, a non-float `SEMANTIC_CACHE_SIMILARITY_THRESHOLD`.
- **GGUF model not present:** `ModelSettings.resolved_main_model_path` (`config.py:45-60`) checks four candidate locations (configured path, Windows `X:\HuggingFace\...`, Docker `/app/models/...`, relative `./models/...`). If none exist, the configured string is returned and the LLamaCPP provider will fail at load time with a clear "file not found" error.
- **Ollama unreachable:** the chat endpoint surfaces a 502/500 with the underlying exception text. The user can switch to the LLamaCPP provider from the sidebar (`streamlit_app.py:405`).
- **Rate-limit misconfiguration:** if `*_per_minute` is set to `0`, the corresponding tier becomes completely blocked. If set to a very high number (e.g. `1000000`), the limiter is effectively a no-op for that tier.
- **Empty `.env`:** no error. The defaults from `pydantic-settings` are used.

## 8. Notable design decisions

1. **`.env.example` is committed; `.env` is gitignored.** This is the standard 12-factor pattern. The example shows every supported variable with a safe default; the real `.env` contains the operator's overrides and secrets.

2. **Double-underscore `__` delimiter.** Avoids ambiguity with single-underscore separators in variable names. The choice is made at `config.py:83`.

3. **Defaults are safe for local development.** `API__HOST=127.0.0.1`, `OLLAMA_BASE_URL=http://localhost:11434`, `RATE_LIMIT_ENABLED=true`. To deploy, the operator must override `API__HOST=0.0.0.0` (as `docker-compose.yml:14` does) and `OLLAMA_BASE_URL=http://ollama:11434` (as `docker-compose.yml:16` does).

4. **Rate limit defaults are tiered, not flat.** Scraping is the most expensive operation (5/min), chat is mid (10/min), STT is higher (15/min) because voice users may iterate quickly, and reads are generous (120/min). The cost reflects the resource profile of each endpoint.

5. **`RATE_LIMIT_TRUST_PROXY` defaults to `false`.** The comment at `features.md:19` is explicit: *"preventing quota-bucket spoofing"*. Operators behind a reverse proxy must explicitly opt in.

6. **Semantic cache is strict-by-default (`0.93` threshold).** The comment at `.env.example:22-23` is blunt: *"a wrong hit is worse than a slow miss."* The cache is also disabled for multi-turn and debug requests, so interactive and developer usage never gets stale answers.

7. **`MODEL__MAIN_MODEL` is a path, not a Hugging Face id.** This is intentional: the LLamaCPP provider loads a local GGUF file directly, not a remote HF model. The model file is shipped by the operator (or baked into the Docker image with an additional `COPY` line in the Dockerfile).

8. **`SCRAPER__URLS` is not env-driven.** The six portal URLs are hard-coded in `config.py:19-26`. This is a deliberate trade-off: env-driven URLs would invite configuration drift, and the portals are project-defining constants.

9. **`CORS_ORIGINS` is permissive by default (`["*"]`).** The app is a self-hosted single-user (or small-team) tool. Locking down CORS would require knowing the operator's deployment topology in advance.

10. **The example file is the documentation.** Every variable is grouped and commented; reading `.env.example` end-to-end is a one-minute tour of the platform's knobs.
