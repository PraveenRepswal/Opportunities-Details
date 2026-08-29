# `config.py` & `.env.example` — Typed Application Configuration

## 1. Purpose & Overview

`config.py` is the project's single source of typed configuration. It defines four Pydantic `BaseSettings` classes that read from a `.env` file (or process environment) and exposes a module-level singleton `settings = Settings()` that every other module imports.

Goals:

- **Type safety & validation** — `int`/`float`/`bool` coercion happens at load time, so a malformed `.env` fails fast.
- **Namespaced settings** — `scraper.*`, `model.*`, `api.*` are independent groups.
- **No code changes to add a setting** — drop a line into `.env`, declare the field, done.
- **Reasonable local-first defaults** — the app runs with zero environment overrides.
- **Cross-environment model resolution** — `resolved_main_model_path` (`config.py:45`) finds the GGUF model whether it lives in the Windows dev path, the Docker image path, or the current working directory.

`.env.example` is the human-friendly template checked into the repo. Real `.env` files are gitignored and let operators override anything without touching code.

---

## 2. Architecture

```
process env / .env file
            │
            ▼
   ┌────────────────────────┐
   │ ScraperSettings        │  ◀── SCRAPER__*  env vars
   │  days_back, threshold, │
   │  urls, enrichment …    │
   └──────────┬─────────────┘
              │ nested
   ┌──────────▼─────────────┐         ┌────────────────────────────┐
   │ ModelSettings          │  ◀──    │ MODEL__*  + top-level vars │
   │  embedding_model,      │         │  (OLLAMA_BASE_URL,         │
   │  main_model, URLs,     │         │   LLAMACPP_SERVER_URL,     │
   │  semantic cache …      │         │   STT_*, SEMANTIC_CACHE_*) │
   └──────────┬─────────────┘         └────────────────────────────┘
              │
   ┌──────────▼─────────────┐         ┌────────────────────────────┐
   │ APISettings            │  ◀──    │ API__*  + top-level vars   │
   │  host, port, CORS,     │         │  (API_HOST, API_PORT,      │
   │  rate limits …         │         │   RATE_LIMIT_*)            │
   └──────────┬─────────────┘         └────────────────────────────┘
              │
   ┌──────────▼─────────────┐
   │ Settings               │  ◀── top-level DEBUG env var
   │  debug + nested groups │
   └──────────┬─────────────┘
              │
              ▼
        settings  (module-level singleton, imported everywhere)
```

Pydantic's `env_nested_delimiter="__"` (`config.py:83`) is what allows `SCRAPER__DAYS_BACK=30` to populate `settings.scraper.days_back`.

---

## 3. Key Classes / Functions

### 3.1 `ScraperSettings(BaseSettings)` — `config.py:7`

```python
class ScraperSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    days_back: int = 30
    score_threshold: float = 0.7

    extract_metadata: bool = True
    llm_enrichment: bool = True
    llm_enrichment_concurrency: int = 2
    llm_enrichment_timeout: float = 45.0
    metadata_content_chars: int = 4000

    urls: Dict[str, str] = {
        "youthop":               "https://www.youthop.com/sitemap_index.xml",
        "greatyop":              "https://greatyop.com/sitemap_index.xml",
        "scholars4dev":          "https://www.scholars4dev.com/sitemap.xml",
        "scholarshipscorner":    "https://scholarshipscorner.website/sitemap_index.xml",
        "opportunitiescorner":   "https://opportunitiescorners.com/sitemap-1.xml",
        "opportunitiesforyouth": "https://opportunitiesforyouth.org/sitemap-1.xml",
    }
```

| Field | Type | Default | Purpose |
| --- | --- | --- | --- |
| `days_back` | `int` | `30` | Recency window for scraping (only items published within N days). |
| `score_threshold` | `float` | `0.7` | Minimum relevance score to accept a scraped item. |
| `extract_metadata` | `bool` | `True` | Whether to run the metadata extractor (deadline / org / location). |
| `llm_enrichment` | `bool` | `True` | Whether to enrich items with an LLM pass after extraction. |
| `llm_enrichment_concurrency` | `int` | `2` | Max in-flight LLM enrichment requests. |
| `llm_enrichment_timeout` | `float` | `45.0` | Per-item enrichment timeout in seconds. |
| `metadata_content_chars` | `int` | `4000` | How many characters of page content to send into the extractor. |
| `urls` | `Dict[str, str]` | six portals | Mapping of portal name → sitemap URL. |

### 3.2 `ModelSettings(BaseSettings)` — `config.py:29`

```python
class ModelSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    embedding_model: str = "intfloat/e5-small-v2"
    main_model: str = os.getenv("MAIN_MODEL_PATH", "models/Qwen3.5-4B-IQ4_NL.gguf")
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    llamacpp_server_url: str = os.getenv("LLAMACPP_SERVER_URL", "http://localhost:8080")
    stt_model: str = os.getenv("STT_MODEL_NAME", "UsefulSensors/moonshine-tiny")
    stt_device: str = os.getenv("STT_DEVICE", "cpu")           # "cpu" | "cuda" | "auto"

    # Semantic answer cache (single-turn requests only; invalidated on re-index)
    semantic_cache_enabled: bool = os.getenv("SEMANTIC_CACHE_ENABLED", "true").lower() in ("true", "1", "yes")
    semantic_cache_similarity_threshold: float = float(os.getenv("SEMANTIC_CACHE_SIMILARITY_THRESHOLD", "0.93"))
    semantic_cache_ttl_hours: float = float(os.getenv("SEMANTIC_CACHE_TTL_HOURS", "24"))
    semantic_cache_max_entries: int = int(os.getenv("SEMANTIC_CACHE_MAX_ENTRIES", "500"))
```

> Note: `ModelSettings` reads top-level env vars (`MAIN_MODEL_PATH`, `OLLAMA_BASE_URL`, …) **directly via `os.getenv`** rather than through Pydantic's `Field(..., env=…)`. Both styles work; the `os.getenv` form is used here for consistency with the project's existing style.

| Field | Type | Default | Env (top-level) |
| --- | --- | --- | --- |
| `embedding_model` | `str` | `intfloat/e5-small-v2` | `MODEL__EMBEDDING_MODEL` |
| `main_model` | `str` | `models/Qwen3.5-4B-IQ4_NL.gguf` | `MAIN_MODEL_PATH`, `MODEL__MAIN_MODEL` |
| `ollama_base_url` | `str` | `http://localhost:11434` | `OLLAMA_BASE_URL`, `MODEL__OLLAMA_BASE_URL` |
| `llamacpp_server_url` | `str` | `http://localhost:8080` | `LLAMACPP_SERVER_URL`, `MODEL__LLAMACPP_SERVER_URL` |
| `stt_model` | `str` | `UsefulSensors/moonshine-tiny` | `STT_MODEL_NAME`, `MODEL__STT_MODEL` |
| `stt_device` | `str` | `cpu` | `STT_DEVICE`, `MODEL__STT_DEVICE` |
| `semantic_cache_enabled` | `bool` | `True` | `SEMANTIC_CACHE_ENABLED`, `MODEL__SEMANTIC_CACHE_ENABLED` |
| `semantic_cache_similarity_threshold` | `float` | `0.93` | `SEMANTIC_CACHE_SIMILARITY_THRESHOLD`, … |
| `semantic_cache_ttl_hours` | `float` | `24` | `SEMANTIC_CACHE_TTL_HOURS`, … |
| `semantic_cache_max_entries` | `int` | `500` | `SEMANTIC_CACHE_MAX_ENTRIES`, … |

> **Bool parsing convention:** `os.getenv(..., "true").lower() in ("true", "1", "yes")` — case-insensitive, lenient on `"1"`/`"yes"`.

### 3.3 `resolved_main_model_path` (property) — `config.py:45`

```python
@property
def resolved_main_model_path(self) -> str:
    """Resolve model path, prioritizing configured path, with fallbacks
    for local Windows and Docker environments."""
    p = Path(self.main_model)
    if p.exists():                                return str(p)
    win_fallback     = Path(r"X:\HuggingFace\models\Qwen3.5-4B-IQ4_NL.gguf")
    if win_fallback.exists():                     return str(win_fallback)
    docker_fallback  = Path("/app/models/Qwen3.5-4B-IQ4_NL.gguf")
    if docker_fallback.exists():                  return str(docker_fallback)
    relative_fallback = Path("./models/Qwen3.5-4B-IQ4_NL.gguf")
    if relative_fallback.exists():                return str(relative_fallback)
    return self.main_model                        # last resort: original string
```

Resolution order:

1. Whatever `main_model` points to (relative or absolute).
2. Windows dev path `X:\HuggingFace\models\Qwen3.5-4B-IQ4_NL.gguf`.
3. Docker container path `/app/models/Qwen3.5-4B-IQ4_NL.gguf`.
4. `./models/Qwen3.5-4B-IQ4_NL.gguf` (relative to CWD).
5. As a last resort, return the original string and let downstream code fail loudly.

This means the same code runs untouched on a Windows dev box and inside a Linux container, as long as the model file lives in one of those locations.

### 3.4 `APISettings(BaseSettings)` — `config.py:63`

```python
class APISettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    host: str = os.getenv("API_HOST", "127.0.0.1")
    port: int = int(os.getenv("API_PORT", "8000"))
    cors_origins: List[str] = ["*"]

    # Rate limiting: per-client-IP sliding window, tiered by endpoint cost.
    rate_limit_enabled: bool = True
    rate_limit_trust_proxy: bool = os.getenv("RATE_LIMIT_TRUST_PROXY", "false").lower() in ("true", "1", "yes")
    rate_limit_chat_per_minute: int = int(os.getenv("RATE_LIMIT_CHAT_PER_MINUTE", "10"))
    rate_limit_transcribe_per_minute: int = int(os.getenv("RATE_LIMIT_TRANSCRIBE_PER_MINUTE", "15"))
    rate_limit_scrape_per_minute: int = int(os.getenv("RATE_LIMIT_SCRAPE_PER_MINUTE", "5"))
    rate_limit_default_per_minute: int = int(os.getenv("RATE_LIMIT_DEFAULT_PER_MINUTE", "120"))
```

| Field | Type | Default | Env (top-level) |
| --- | --- | --- | --- |
| `host` | `str` | `127.0.0.1` | `API_HOST`, `API__HOST` |
| `port` | `int` | `8000` | `API_PORT`, `API__PORT` |
| `cors_origins` | `List[str]` | `["*"]` | `API__CORS_ORIGINS` (comma-separated) |
| `rate_limit_enabled` | `bool` | `True` | `API__RATE_LIMIT_ENABLED` |
| `rate_limit_trust_proxy` | `bool` | `False` | `RATE_LIMIT_TRUST_PROXY`, `API__RATE_LIMIT_TRUST_PROXY` |
| `rate_limit_chat_per_minute` | `int` | `10` | `RATE_LIMIT_CHAT_PER_MINUTE`, `API__RATE_LIMIT_CHAT_PER_MINUTE` |
| `rate_limit_transcribe_per_minute` | `int` | `15` | `RATE_LIMIT_TRANSCRIBE_PER_MINUTE`, `API__RATE_LIMIT_TRANSCRIBE_PER_MINUTE` |
| `rate_limit_scrape_per_minute` | `int` | `5` | `RATE_LIMIT_SCRAPE_PER_MINUTE`, `API__RATE_LIMIT_SCRAPE_PER_MINUTE` |
| `rate_limit_default_per_minute` | `int` | `120` | `RATE_LIMIT_DEFAULT_PER_MINUTE`, `API__RATE_LIMIT_DEFAULT_PER_MINUTE` |

### 3.5 `Settings(BaseSettings)` — `config.py:79`

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    debug: bool = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")
    scraper: ScraperSettings = ScraperSettings()
    model: ModelSettings = ModelSettings()
    api: APISettings = APISettings()
```

| Field | Type | Default | Env |
| --- | --- | --- | --- |
| `debug` | `bool` | `False` | `DEBUG`, `SETTINGS__DEBUG` |
| `scraper` | `ScraperSettings` | nested instance | `SCRAPER__*` |
| `model` | `ModelSettings` | nested instance | `MODEL__*` |
| `api` | `APISettings` | nested instance | `API__*` |

The `__` delimiter is what allows the dotted access `settings.api.port` to be populated from `API__PORT=8000` in `.env`.

### 3.6 `settings` module-level singleton — `config.py:93`

```python
settings = Settings()
```

A single import-eagerly-constructed instance, used everywhere (`from config import settings`).

---

## 4. Flow / Lifecycle

1. **Process start.** Any module that does `from config import settings` triggers instantiation of `Settings()`, which constructs three nested `*Settings()` instances. Each instance reads `.env` *and* `os.environ` and merges them.
2. **Resolution.** Pydantic considers: (a) explicit constructor args (none used here), (b) process env vars, (c) `.env` file values, (d) field defaults.
3. **At use sites.** Code reads e.g. `settings.api.port`. The lazy `@property` `resolved_main_model_path` is evaluated on first access.
4. **At mutation.** Some CLI flags (e.g. `--debug` in `backend/main.py:430`) mutate `settings.debug` in place. This works because `Settings` is a regular Pydantic model — be aware this affects only the in-process copy.

There is no re-load mechanism; `.env` changes require a restart.

---

## 5. Dependencies

| Import | Why |
| --- | --- |
| `os` | `os.getenv` for top-level env reads. |
| `pathlib.Path` | Cross-platform path checks inside `resolved_main_model_path`. |
| `typing.{Dict, List}` | Type hints for the Pydantic fields. |
| `pydantic_settings.{BaseSettings, SettingsConfigDict}` | The `BaseSettings` base class + per-class config (env file, encoding, ignore-extra behavior). |

---

## 6. Configuration / Environment Variables

The complete contract between `.env` and runtime is documented below. Both **nested** (`SCRAPER__FOO`) and **flat** (`FOO`) names are accepted for `main_model`, `ollama_base_url`, `llamacpp_server_url`, `stt_*`, and the rate-limit fields, because those are read both ways. Other settings are read only through the nested prefix.

### `.env.example` (verbatim) — `.env.example:1`

```ini
# ==========================================
# Opportunities Details Environment Configuration
# ==========================================

# --- Scraper Settings ---
SCRAPER__DAYS_BACK=30
SCRAPER__SCORE_THRESHOLD=0.7
SCRAPER__EXTRACT_METADATA=true
SCRAPER__LLM_ENRICHMENT=true
SCRAPER__LLM_ENRICHMENT_CONCURRENCY=2
SCRAPER__LLM_ENRICHMENT_TIMEOUT=45.0
SCRAPER__METADATA_CONTENT_CHARS=4000

# --- Model & RAG Settings ---
MODEL__EMBEDDING_MODEL=intfloat/e5-small-v2
MODEL__MAIN_MODEL=models/Qwen3.5-4B-IQ4_NL.gguf
MODEL__OLLAMA_BASE_URL=http://localhost:11434
MODEL__LLAMACPP_SERVER_URL=http://localhost:8080

# --- Semantic Answer Cache (single-turn questions; invalidated on re-index) ---
SEMANTIC_CACHE_ENABLED=true
# Min cosine similarity between question embeddings to serve a cached answer.
# Keep strict (>=0.90): a wrong hit is worse than a slow miss.
SEMANTIC_CACHE_SIMILARITY_THRESHOLD=0.93
SEMANTIC_CACHE_TTL_HOURS=24
SEMANTIC_CACHE_MAX_ENTRIES=500

# --- API Server Settings ---
API__HOST=127.0.0.1
API__PORT=8000

# --- API Rate Limiting (per client IP, sliding window) ---
API__RATE_LIMIT_ENABLED=true
API__RATE_LIMIT_TRUST_PROXY=false
# Requests per minute per tier: chat/streaming, STT transcription,
# scrape trigger, everything else (reads). /health and /docs are exempt.
API__RATE_LIMIT_CHAT_PER_MINUTE=10
API__RATE_LIMIT_TRANSCRIBE_PER_MINUTE=15
API__RATE_LIMIT_SCRAPE_PER_MINUTE=5
API__RATE_LIMIT_DEFAULT_PER_MINUTE=120
```

### Full env-var index

| Env var | Type | Default | Populates |
| --- | --- | --- | --- |
| `DEBUG` | bool | `false` | `settings.debug` |
| `SCRAPER__DAYS_BACK` | int | `30` | `settings.scraper.days_back` |
| `SCRAPER__SCORE_THRESHOLD` | float | `0.7` | `settings.scraper.score_threshold` |
| `SCRAPER__EXTRACT_METADATA` | bool | `true` | `settings.scraper.extract_metadata` |
| `SCRAPER__LLM_ENRICHMENT` | bool | `true` | `settings.scraper.llm_enrichment` |
| `SCRAPER__LLM_ENRICHMENT_CONCURRENCY` | int | `2` | `settings.scraper.llm_enrichment_concurrency` |
| `SCRAPER__LLM_ENRICHMENT_TIMEOUT` | float | `45.0` | `settings.scraper.llm_enrichment_timeout` |
| `SCRAPER__METADATA_CONTENT_CHARS` | int | `4000` | `settings.scraper.metadata_content_chars` |
| `MODEL__EMBEDDING_MODEL` | str | `intfloat/e5-small-v2` | `settings.model.embedding_model` |
| `MODEL__MAIN_MODEL` | str | `models/Qwen3.5-4B-IQ4_NL.gguf` | `settings.model.main_model` |
| `MAIN_MODEL_PATH` | str | (same) | `settings.model.main_model` (top-level override) |
| `MODEL__OLLAMA_BASE_URL` | str | `http://localhost:11434` | `settings.model.ollama_base_url` |
| `OLLAMA_BASE_URL` | str | (same) | `settings.model.ollama_base_url` (top-level override) |
| `MODEL__LLAMACPP_SERVER_URL` | str | `http://localhost:8080` | `settings.model.llamacpp_server_url` |
| `LLAMACPP_SERVER_URL` | str | (same) | `settings.model.llamacpp_server_url` (top-level override) |
| `MODEL__STT_MODEL` | str | `UsefulSensors/moonshine-tiny` | `settings.model.stt_model` |
| `STT_MODEL_NAME` | str | (same) | `settings.model.stt_model` (top-level override) |
| `MODEL__STT_DEVICE` | str | `cpu` | `settings.model.stt_device` |
| `STT_DEVICE` | str | (same) | `settings.model.stt_device` (top-level override) |
| `SEMANTIC_CACHE_ENABLED` | bool | `true` | `settings.model.semantic_cache_enabled` |
| `SEMANTIC_CACHE_SIMILARITY_THRESHOLD` | float | `0.93` | `settings.model.semantic_cache_similarity_threshold` |
| `SEMANTIC_CACHE_TTL_HOURS` | float | `24` | `settings.model.semantic_cache_ttl_hours` |
| `SEMANTIC_CACHE_MAX_ENTRIES` | int | `500` | `settings.model.semantic_cache_max_entries` |
| `API__HOST` | str | `127.0.0.1` | `settings.api.host` |
| `API_HOST` | str | (same) | `settings.api.host` (top-level override) |
| `API__PORT` | int | `8000` | `settings.api.port` |
| `API_PORT` | int | (same) | `settings.api.port` (top-level override) |
| `API__CORS_ORIGINS` | csv | `*` | `settings.api.cors_origins` |
| `API__RATE_LIMIT_ENABLED` | bool | `true` | `settings.api.rate_limit_enabled` |
| `RATE_LIMIT_TRUST_PROXY` | bool | `false` | `settings.api.rate_limit_trust_proxy` |
| `RATE_LIMIT_CHAT_PER_MINUTE` | int | `10` | `settings.api.rate_limit_chat_per_minute` |
| `RATE_LIMIT_TRANSCRIBE_PER_MINUTE` | int | `15` | `settings.api.rate_limit_transcribe_per_minute` |
| `RATE_LIMIT_SCRAPE_PER_MINUTE` | int | `5` | `settings.api.rate_limit_scrape_per_minute` |
| `RATE_LIMIT_DEFAULT_PER_MINUTE` | int | `120` | `settings.api.rate_limit_default_per_minute` |

---

## 7. API Endpoints

This module defines **no routes** — it is configuration only.

---

## 8. Error handling

- **Type coercion failures** (e.g. `SCRAPER__PORT=not_an_int`) raise `pydantic.ValidationError` at import time. This is intentional fail-fast.
- **`resolved_main_model_path`** never raises — it returns the original string even if nothing resolves, leaving the loader to surface a clear downstream error.
- `extra="ignore"` (`config.py:8`, `config.py:30`, `config.py:64`, `config.py:84`) — unknown keys in `.env` are silently dropped, keeping the file forward-compatible.

---

## 9. Notable patterns / design decisions

- **Two reading styles coexist.** The nested `SCRAPER__FOO` style uses Pydantic's `BaseSettings` machinery; the flat `OLLAMA_BASE_URL` style uses `os.getenv` directly inside the field default. Both work, but only the flat form is overridable when `Settings()` is instantiated *without* the env (e.g. in unit tests that pass `_env_file=None`).
- **`env_nested_delimiter="__"`** is set only on the top-level `Settings` class, *not* on the nested classes. That's why `MODEL__FOO` works at the top level but nested classes only read their own `.env` file directly.
- **`resolved_main_model_path`** is a `@property`, not a field. It is recomputed every call — fine, since `Path.exists()` is a single `stat()`. Caching it could surprise if the file appears after startup.
- **No re-load hook.** Changing `.env` requires a restart. This is appropriate for a single-process local app.
- **Singleton via module-level import.** `settings` is constructed once at process start. Every importer shares the same object; mutations (`settings.debug = True` in `backend/main.py:432`) are visible everywhere.
- **CORS default is `["*"]`.** Local-first tool, but operators exposing the API to other origins should override `API__CORS_ORIGINS`.
- **`rate_limit_trust_proxy` default is `false`.** Reading `X-Forwarded-For` blindly is unsafe behind an untrusted proxy; operators must opt in explicitly.