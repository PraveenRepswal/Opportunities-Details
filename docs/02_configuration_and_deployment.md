# 02 — Configuration & Deployment

## 1. Unified Configuration Architecture

The project employs a single, centralized configuration system defined in `config.py`, built with `pydantic-settings` (Pydantic v2). All runtime values can be set via `.env` files, process environment variables, or fallback code defaults.

### 1.1 Hierarchical Settings Schema

Settings are categorized into four logical domains:
- **`ModelSettings`**: LLM, SLM, embedding, reranker, and STT parameters.
- **`APISettings`**: Host, port, CORS, and rate limiting thresholds.
- **`DatabaseSettings`**: File paths for SQLite database, FAISS index, and BM25 store.
- **`ScraperSettings`**: Concurrency, timeouts, retries, and User-Agent headers.

```python
# config.py excerpt showing idiomatic AliasChoices support
class ModelSettings(BaseModel):
    main_model: str = Field(
        default="qwen2.5:3b",
        validation_alias=AliasChoices("MODEL__MAIN_MODEL", "MAIN_MODEL"),
    )
    model_type: str = Field(
        default="ollama",
        validation_alias=AliasChoices("MODEL__MODEL_TYPE", "MODEL_TYPE"),
    )
    main_model_path: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("MODEL__MAIN_MODEL_PATH", "MAIN_MODEL_PATH", "MODEL_PATH"),
    )
    slm_model_path: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("MODEL__SLM_MODEL_PATH", "SLM_MODEL_PATH"),
    )
    ollama_base_url: str = Field(
        default="http://localhost:11434",
        validation_alias=AliasChoices("MODEL__OLLAMA_BASE_URL", "OLLAMA_BASE_URL"),
    )
    # ...
```

By leveraging `AliasChoices`, the application transparently supports both **flat environment variables** (e.g., `MAIN_MODEL_PATH`, `OLLAMA_BASE_URL`) and **prefixed nested variables** (e.g., `MODEL__MAIN_MODEL`, `API__PORT`).

---

## 2. Complete Environment Variable Reference (`.env.example`)

Below is the exhaustive matrix of all supported environment configuration parameters:

| Variable | Type | Default | Description |
|---|---|---|---|
| `MODEL__MODEL_TYPE` | `str` | `ollama` | Provider: `ollama` or `llamacpp`. |
| `MODEL__MAIN_MODEL` | `str` | `qwen2.5:3b` | Model tag for Ollama or identifier string. |
| `MODEL__MAIN_MODEL_PATH` | `str` | `models/Qwen3.5-4B-IQ4_NL.gguf` | Path to primary LLM GGUF file when using `llamacpp`. |
| `MODEL__SLM_MODEL_PATH` | `str` | `models/qwen2.5-0.5b-instruct-q4_k_m.gguf` | Path to lightweight SLM GGUF file for metadata extraction. |
| `MODEL__OLLAMA_BASE_URL` | `str` | `http://localhost:11434` | Base URL of running Ollama instance. |
| `MODEL__EMBEDDING_MODEL` | `str` | `intfloat/e5-small-v2` | Hugging Face embedding model identifier. |
| `MODEL__RERANKER_MODEL` | `str` | `cross-encoder/ms-marco-MiniLM-L12-v2` | Hugging Face cross-encoder model identifier. |
| `MODEL__STT_MODEL` | `str` | `UsefulSensors/moonshine-tiny` | Hugging Face / Moonshine speech recognition model. |
| `MODEL__STT_DEVICE` | `str` | `cpu` | Device preference for STT: `cpu`, `cuda`, or `auto`. |
| `MODEL__N_GPU_LAYERS` | `int` | `0` | GPU offload layers for llama.cpp (`-1` for all layers). |
| `MODEL__N_CTX` | `int` | `4096` | Context window size for llama.cpp inference. |
| `MODEL__TEMPERATURE` | `float` | `0.7` | LLM generation sampling temperature. |
| `MODEL__MAX_TOKENS` | `int` | `2048` | Maximum output generation token limit. |
| `API__HOST` | `str` | `0.0.0.0` | Binding host for FastAPI server. |
| `API__PORT` | `int` | `8000` | Binding port for FastAPI server. |
| `API__RATE_LIMIT_PER_MINUTE`| `int` | `60` | General endpoint rate limit threshold per client IP. |
| `API__RATE_LIMIT_BURST` | `int` | `10` | Maximum burst limit on cost-intensive endpoints. |
| `API__RATE_LIMIT_TRUST_PROXY`| `bool` | `false` | When true, reads client IP from `X-Forwarded-For`. |
| `DATABASE__SQLITE_PATH` | `str` | `opportunities_chat.db` | Local SQLite database file path. |
| `DATABASE__FAISS_INDEX_PATH`| `str` | `faiss_store` | Directory for persisted FAISS index files. |
| `SCRAPER__REQUEST_TIMEOUT` | `int` | `15` | Async HTTP request timeout in seconds. |
| `SCRAPER__MAX_RETRIES` | `int` | `3` | Maximum retry attempts for failed HTTP page requests. |

---

## 3. Local Development Setup

### 3.1 Prerequisites
- Python 3.12 (64-bit recommended)
- Git
- (Optional) CUDA-enabled NVIDIA GPU with toolkit if running GPU llama.cpp

### 3.2 Installation

```bash
# 1. Clone repository
git clone https://github.com/your-username/Opportunities-Details.git
cd Opportunities-Details

# 2. Create and activate virtual environment
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# Linux/macOS:
source .venv/bin/activate

# 3. Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# 4. Copy environment configuration
cp .env.example .env
```

### 3.3 Running Locally

To run the backend and frontend simultaneously in separate terminals:

```bash
# Terminal 1: Launch FastAPI Backend
uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload

# Terminal 2: Launch Streamlit Frontend
streamlit run streamlit_app.py --server.port 8501
```

Access the user interfaces:
- **Streamlit Web UI**: [http://127.0.0.1:8501](http://127.0.0.1:8501)
- **FastAPI OpenAPI Docs**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **Health Check Endpoint**: [http://127.0.0.1:8000/api/v1/health](http://127.0.0.1:8000/api/v1/health)

---

## 4. Docker & Container Deployment

The repository provides a multi-stage `Dockerfile` and a 3-service `docker-compose.yml` for unified production deployment.

### 4.1 Multi-Stage Dockerfile Architecture

- **Stage 1 (Builder)**: Uses `python:3.12-slim`, installs system build tools (`build-essential`, `cmake`, `git`), compiles C++ extensions like `llama-cpp-python` and wheel dependencies into `/root/.local`.
- **Stage 2 (Runtime)**: Clean `python:3.12-slim` image, installs runtime libraries (`libsndfile1`, `curl`), copies compiled wheels from builder, sets up non-root `appuser`, and configures health checks.

### 4.2 Docker Compose Services

```yaml
services:
  backend:
    build: .
    container_name: opportunities_backend
    ports:
      - "8000:8000"
    environment:
      - MODEL__OLLAMA_BASE_URL=http://ollama:11434
      - DATABASE__SQLITE_PATH=/app/data/opportunities_chat.db
      - DATABASE__FAISS_INDEX_PATH=/app/data/faiss_store
    volumes:
      - opportunities_data:/app/data
      - ./models:/app/models:ro
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/v1/health"]
      interval: 10s
      retries: 3

  frontend:
    build: .
    container_name: opportunities_frontend
    command: ["streamlit", "run", "streamlit_app.py", "--server.port=8501", "--server.address=0.0.0.0"]
    ports:
      - "8501:8501"
    environment:
      - BACKEND_API_URL=http://backend:8000
    depends_on:
      backend:
        condition: service_healthy

  ollama:
    image: ollama/ollama:latest
    container_name: opportunities_ollama
    ports:
      - "11434:11434"
    volumes:
      - ollama_models:/root/.ollama
```

### 4.3 Launching with Docker Compose

```bash
# Build and run all services in background
docker compose up -d --build

# Pull default Ollama model inside container
docker exec -it opportunities_ollama ollama pull qwen2.5:3b

# View application logs
docker compose logs -f backend
```

---

## 5. CI / CD Pipeline (GitHub Actions)

Continuous Integration is automated via `.github/workflows/ci.yml`.

### Pipeline Trigger Conditions
- Push to branch `main`
- All Pull Requests targeting `main`
- Semantic version release tags (`v*`)

### Steps Executed
1. **Checkout Code**: Checks out repository with full git history.
2. **Setup Python**: Initializes Python 3.12 with pip dependency caching.
3. **Linting (`ruff`)**: Executes `ruff check .` with zero tolerance for syntax errors or lint regressions.
4. **Automated Testing (`pytest`)**: Runs full test suite (`pytest tests/ -q`), validating database operations, rate limiting, metadata extraction, SLM profiling, routing, and scraper normalization.

