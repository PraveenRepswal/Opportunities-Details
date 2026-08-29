# Docker & Deployment — `Dockerfile` and `docker-compose.yml`

## 1. Purpose & Overview

The project ships a **multi-stage Dockerfile** (`Dockerfile`) and a **three-service Docker Compose file** (`docker-compose.yml`) that together orchestrate the entire platform:

- **FastAPI backend** (Uvicorn-served) — the RAG + scraping API.
- **Streamlit frontend** — the user-facing chat UI.
- **Ollama** — the local LLM inference server (downloaded as `ollama/ollama:latest`).

The Dockerfile is a **single-image, multi-stage build** that produces a lean runtime image. The Compose file **launches the same image twice with different commands** — one as the backend (`uvicorn`) and one as the frontend (`streamlit run`) — plus a third container for Ollama. This avoids having to maintain two Dockerfiles and keeps the build cache hot.

## 2. Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                         docker-compose                             │
│                                                                    │
│  ┌────────────────────┐    ┌────────────────────┐ ┌─────────────┐  │
│  │ opportunities_     │    │ opportunities_     │ │ opportunities│  │
│  │   backend          │    │   frontend         │ │   _ollama   │  │
│  │ (image: same       │    │ (image: same       │ │ (image:     │  │
│  │  Dockerfile)       │◄───┤  Dockerfile)       │ │  ollama/    │  │
│  │ command: uvicorn   │    │ command: streamlit │ │  ollama)    │  │
│  │  backend.main:app  │    │  run streamlit_app │ │             │  │
│  │ port 8000          │    │ port 8501          │ │ port 11434  │  │
│  └─────────┬──────────┘    └─────────┬──────────┘ └──────┬──────┘  │
│            │                         │                   │         │
│            ▼                         │                   │         │
│   volume: sqlite_data (/app/data)    │                   │         │
│                                      │                   │         │
│   depends_on: backend                │                   │         │
│            (condition: healthy)      │                   │         │
│                                      │                   ▼         │
│                              BACKEND_API_BASE_URL ──► http://backend:8000
│                              OLLAMA_BASE_URL ───────► http://ollama:11434
└────────────────────────────────────────────────────────────────────┘
```

Networking:
- Docker Compose creates a default bridge network.
- The frontend reaches the backend by service name (`http://backend:8000`) because both containers share the Compose-created network (`docker-compose.yml:37`).
- The backend reaches Ollama at `http://ollama:11434` (`docker-compose.yml:16`).

Volumes:
- `sqlite_data` (`/app/data`) — persistent SQLite database.
- `ollama_storage` (`/root/.ollama`) — persistent Ollama model cache.

Both are **named local-driver volumes** declared at `docker-compose.yml:51-55`.

## 3. Dockerfile — Stages

The Dockerfile is a **two-stage build** that compiles wheels in stage 1 and copies the resulting site-packages into a slim runtime image at stage 2.

### 3.1 Stage 1: `builder` (`Dockerfile:1-17`)

| Step | Line | Purpose |
|---|---|---|
| Base image | `Dockerfile:4` | `python:3.12-slim AS builder` — Debian Bookworm slim with Python 3.12. |
| Env vars | `Dockerfile:6-7` | `PYTHONDONTWRITEBYTECODE=1` (no `.pyc` files) and `PYTHONUNBUFFERED=1` (unbuffered stdout/stderr for log streaming). |
| Workdir | `Dockerfile:9` | `/build`. |
| System packages | `Dockerfile:11-14` | `build-essential` (gcc, make) and `curl` (for healthchecks). Cleaned up with `rm -rf /var/lib/apt/lists/*`. |
| Copy requirements | `Dockerfile:16` | `COPY requirements.txt .` |
| Install deps | `Dockerfile:17` | `pip install --no-cache-dir --prefix=/install -r requirements.txt` — installs into `/install` so it can be copied in stage 2 without polluting the runtime image with pip cache or build tools. |

**Why a builder stage?** `torch` and `sentence-transformers` can pull in C extensions that need to be compiled or pre-built. The builder provides the toolchain; the runtime does not need it, so the final image is much smaller.

### 3.2 Stage 2: `runtime` (`Dockerfile:19-56`)

| Step | Line | Purpose |
|---|---|---|
| Base image | `Dockerfile:22` | `python:3.12-slim AS runtime` — fresh slim image, no build tools. |
| Env vars | `Dockerfile:24-27` | `PYTHONDONTWRITEBYTECODE=1`, `PYTHONUNBUFFERED=1`, `PATH="/install/bin:$PATH"` (so the copied site-packages are importable), `PYTHONPATH="/app"` (so `backend`, `scrapers`, `streamlit_app.py` are importable). |
| Workdir | `Dockerfile:29` | `/app`. |
| System packages | `Dockerfile:31-33` | Only `curl` (for healthchecks). |
| User | `Dockerfile:36-38` | `useradd -u 1000 -m appuser` and `mkdir -p /app/data /app/models` (FAISS / GGUF models). Ownership given to `appuser`. |
| Copy deps | `Dockerfile:40` | `COPY --from=builder /install /usr/local` — moves the installed Python packages from the builder's `/install` prefix to the system site-packages (`/usr/local/lib/python3.12/site-packages` and `/usr/local/bin`). |
| Copy app | `Dockerfile:42-47` | Application source files, all chowned to `appuser` (avoids permission errors on bind-mounts): `backend/`, `scrapers/`, `config.py`, `scraper.py`, `streamlit_app.py`, `.env.example`. |
| Expose | `Dockerfile:49` | `EXPOSE 8000 8501` — both ports are declared. The actual port used depends on which `command` the container is launched with. |
| Healthcheck | `Dockerfile:51-52` | `HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD curl -f http://localhost:8000/api/v1/health \|\| exit 1`. |
| User switch | `Dockerfile:54` | `USER appuser` — runs the container as a non-root user (uid 1000). |
| Default command | `Dockerfile:56` | `["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]` — the backend. **The Compose file overrides this** for the frontend service. |

**Note on model files:** the Dockerfile creates `/app/models` and the Compose file points the backend at `MODEL__MAIN_MODEL=/app/models/Qwen3.5-4B-IQ4_NL.gguf` (`docker-compose.yml:17`). The user is expected to either bind-mount a model volume or pre-bake the GGUF into the image (e.g. via an additional `COPY` line). Out of the box, the container will start, but inference will fail until the GGUF is present — `config.py` (`config.py:45-60`) does provide a `resolved_main_model_path` property with fallbacks for the local Windows and Docker locations.

## 4. docker-compose.yml — Services

The Compose file (`docker-compose.yml:1-55`) defines **three services** and **two named volumes**.

### 4.1 `backend`  (`docker-compose.yml:4-25`)

| Field | Value | Notes |
|---|---|---|
| `build.context` | `.` | The repository root. |
| `build.dockerfile` | `Dockerfile` | The single multi-stage Dockerfile. |
| `container_name` | `opportunities_backend` | Fixed name for easy `docker exec`. |
| `restart` | `unless-stopped` | Always restart unless explicitly stopped. |
| `command` | `["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]` | Overrides the Dockerfile's `CMD`. List form avoids shell wrapping. |
| `ports` | `"8000:8000"` | Maps host 8000 to container 8000. |
| `environment` | `API_HOST=0.0.0.0`, `API_PORT=8000`, `OLLAMA_BASE_URL=http://ollama:11434`, `MODEL__MAIN_MODEL=/app/models/Qwen3.5-4B-IQ4_NL.gguf` | Loaded by `pydantic-settings` via `env_nested_delimiter="__"`. |
| `volumes` | `sqlite_data:/app/data` | SQLite DB persistence across container restarts. |
| `healthcheck.test` | `["CMD", "curl", "-f", "http://localhost:8000/api/v1/health"]` | Same endpoint as the Dockerfile healthcheck. |
| `healthcheck.interval` | `15s` | Slightly more frequent than the Dockerfile's 30s. |
| `healthcheck.timeout` | `5s` | |
| `healthcheck.retries` | `3` | |
| `healthcheck.start_period` | `10s` | Grace period before the first failure counts. |

### 4.2 `frontend`  (`docker-compose.yml:27-40`)

| Field | Value | Notes |
|---|---|---|
| `build` | Same context + Dockerfile as backend | Re-uses the cached image. |
| `container_name` | `opportunities_frontend` | |
| `restart` | `unless-stopped` | |
| `command` | `["streamlit", "run", "streamlit_app.py", "--server.port=8501", "--server.address=0.0.0.0"]` | List form. `--server.address=0.0.0.0` is required so Streamlit accepts non-localhost connections (and so the port mapping is usable). |
| `ports` | `"8501:8501"` | |
| `environment` | `BACKEND_API_BASE_URL=http://backend:8000` | The Streamlit app reads `api_url` from a session state widget defaulting to `settings.api.host:port` (see `streamlit_app.py:18`). The user can also override it in the sidebar at runtime, but the env-derived default goes via `settings.api`. This env var is read directly by `streamlit_app.py` consumers when needed. |
| `depends_on.backend.condition` | `service_healthy` | The frontend does not start until the backend's healthcheck reports `healthy`. |

> **Operational note:** because the frontend container's `BACKEND_API_BASE_URL` env is not used by the current `streamlit_app.py` (which constructs `DEFAULT_API_URL` from `settings.api.host:port`), the frontend's default base URL will resolve to `http://127.0.0.1:8000` — i.e. **inside the frontend container**, which will fail. In practice the user must change the sidebar's `Backend API Base URL` to `http://backend:8000` (or `http://host.docker.internal:8000` on Docker Desktop) on first run. This is the documented behavior of the current build.

### 4.3 `ollama`  (`docker-compose.yml:42-49`)

| Field | Value | Notes |
|---|---|---|
| `image` | `ollama/ollama:latest` | Pulled from Docker Hub. |
| `container_name` | `opportunities_ollama` | |
| `restart` | `unless-stopped` | |
| `ports` | `"11434:11434"` | Exposed so the host can also call Ollama directly (e.g. via `ollama run`). |
| `volumes` | `ollama_storage:/root/.ollama` | Persists downloaded GGUF models across restarts. |

The backend reads `OLLAMA_BASE_URL=http://ollama:11434` (`docker-compose.yml:16`), which resolves via Docker's internal DNS.

**Pulling a model:** the Ollama container starts empty. After the stack is up, run:
```bash
docker exec -it opportunities_ollama ollama pull qwen2.5:4b
```
(Substitute the actual model name matching `MODEL__MAIN_MODEL` if you want to use Ollama as the LLM provider. The default in `docker-compose.yml` points at a GGUF file in `/app/models/`, which is the **LLamaCPP** provider path.)

### 4.4 Volumes  (`docker-compose.yml:51-55`)

```yaml
volumes:
  sqlite_data:
    driver: local
  ollama_storage:
    driver: local
```

- `sqlite_data` — mounted at `/app/data` on the backend. Holds `opportunities_chat.db`.
- `ollama_storage` — mounted at `/root/.ollama` on the Ollama container. Holds downloaded model weights.

## 5. Startup / runtime / shutdown lifecycle

### Startup
1. `docker compose up -d` (or `docker-compose up -d` on older Docker) builds the image (using the multi-stage Dockerfile) and starts the three containers in dependency order.
2. The `backend` container starts first; Docker's built-in healthcheck from the Dockerfile (`Dockerfile:51-52`) is one mechanism, and the Compose-level healthcheck (`docker-compose.yml:20-25`) is another — both ping `http://localhost:8000/api/v1/health` every 15–30 s.
3. The `ollama` container starts in parallel (no `depends_on`).
4. The `frontend` container waits for `backend` to become `healthy` (`docker-compose.yml:38-40`), then starts Streamlit on port 8501.

### Runtime
- Backend log lines are streamed to `docker compose logs -f backend` (stdout, unbuffered thanks to `PYTHONUNBUFFERED=1`).
- Scraper runs are triggered from the UI sidebar (`streamlit_app.py:418` → backend `POST /api/v1/scrape`). They execute in a background asyncio task in the backend process and report to `/api/v1/scrape/status`.
- Rate limiting is enforced per-client-IP (in-memory; resets on container restart).

### Shutdown
- `docker compose down` stops and removes the containers. Volumes are **preserved**.
- `docker compose down -v` also removes the named volumes (destructive — wipes the SQLite DB and the Ollama model cache).
- `restart: unless-stopped` (`docker-compose.yml:9, 32, 45`) means a Docker daemon restart will also bring the services back up.

## 6. Configuration / environment variables

| Variable | Set in | Consumed by | Effect |
|---|---|---|---|
| `API_HOST` | `docker-compose.yml:14` | `config.py:66` (`APISettings.host`) | Bind address. Override at `0.0.0.0` so the port mapping works. |
| `API_PORT` | `docker-compose.yml:15` | `config.py:67` | Same as host but for port. |
| `OLLAMA_BASE_URL` | `docker-compose.yml:16` | `config.py:34` | URL the LLM client uses to reach Ollama. Set to the in-network service name `http://ollama:11434`. |
| `MODEL__MAIN_MODEL` | `docker-compose.yml:17` | `config.py:33` (via `env_nested_delimiter="__"`) | Path to the GGUF file the LLamaCPP provider should load. |
| `BACKEND_API_BASE_URL` | `docker-compose.yml:37` | (declared but not currently read by `streamlit_app.py`) | Intended override of the default backend URL. |
| `DEBUG` | (not set by Compose; can be added) | `streamlit_app.py:11-16`, `config.py:87` | Enables debug mode in UI and backend. |
| All `SCRAPER__*`, `MODEL__*`, `SEMANTIC_CACHE_*`, `API__RATE_LIMIT_*` | not set by Compose; can be added | `config.py` | See `docs/environment_config.md`. |

## 7. Network / API calls (compose-internal)

Inside the Compose network, the following service-to-service calls happen:

- **frontend → backend** at `http://backend:8000`:
  - `GET /api/v1/health`
  - `GET /api/v1/sessions`
  - `POST /api/v1/sessions`
  - `DELETE /api/v1/sessions/{id}`
  - `GET /api/v1/sessions/{id}/messages`
  - `POST /api/v1/chat/stream`
  - `POST /api/v1/transcribe`
  - `POST /api/v1/scrape`
  - `GET /api/v1/scrape/status`
  - `GET /api/v1/opportunities`
- **backend → ollama** at `http://ollama:11434`:
  - Ollama-native chat completions (`/api/chat`) when the user selects the "Ollama" provider in the sidebar.
- **host → backend** at `http://localhost:8000`:
  - All of the above, plus the auto-generated `/docs` (Swagger UI) and `/openapi.json`.
- **host → frontend** at `http://localhost:8501`:
  - The Streamlit app itself.
- **host → ollama** at `http://localhost:11434`:
  - Direct `ollama` CLI or HTTP for ad-hoc model management.

## 8. Error handling / fallbacks

- **Healthcheck failure:** if the backend is unhealthy for 3 consecutive checks (5s timeout each, 15s interval), Docker marks the container `unhealthy`. The frontend's `depends_on: service_healthy` will **block** the frontend start. If the backend later recovers, the frontend will not auto-start; it must be `docker compose up`d again.
- **Ollama unreachable:** the backend's Ollama client will retry and surface a 502/500 to the Streamlit UI (`POST /api/v1/chat/stream` returns non-200; the UI shows an error in `st.error`).
- **GGUF missing:** the LLamaCPP provider will fail to initialize; the chat endpoint will return an error. The Ollama provider path is independent and can be selected from the UI sidebar (`streamlit_app.py:405`).
- **`docker compose build` cache invalidation:** changes to `requirements.txt` invalidate the builder stage (`Dockerfile:16-17`); changes to source files only invalidate the runtime stage (`Dockerfile:42-47`), making iterative development fast.
- **Port conflicts:** if host port 8000/8501/11434 is already in use, the Compose start will fail. The user must edit the `ports` mappings or stop the conflicting process.

## 9. Notable design decisions

1. **Single image, two services.** Both `backend` and `frontend` build from the same `Dockerfile`. The only difference is the `command` override. This halves the build cache footprint and ensures parity between dev and prod environments.

2. **Multi-stage build to drop build tools.** `build-essential` and `gcc` are needed for some Python wheels but not at runtime. The builder installs them, the runtime image never sees them. The `/install` prefix trick (`Dockerfile:17`) lets us copy site-packages cleanly into `/usr/local` (`Dockerfile:40`).

3. **Non-root `appuser` (uid 1000).** The runtime container runs as `appuser` (`Dockerfile:36, 54`) instead of root, satisfying the principle of least privilege and most container-security scanners.

4. **Healthcheck-driven dependency ordering.** Instead of `depends_on: backend` (which only waits for the container to start), the frontend uses `condition: service_healthy` (`docker-compose.yml:39-40`). This prevents a race where the user opens the UI and gets a 503 because Uvicorn hasn't bound the socket yet.

5. **Named volumes, not bind-mounts.** The Compose file uses `volumes: sqlite_data:/app/data` and `ollama_storage:/root/.ollama` rather than bind-mounting host paths. This makes the stack portable across host OSes (Windows/macOS/Linux all behave the same).

6. **Two healthchecks for the backend.** Both the Dockerfile (`Dockerfile:51-52`) and the Compose file (`docker-compose.yml:20-25`) declare a healthcheck. Docker only honors **one** of them per container; the Compose-level definition wins because it is applied last. The Dockerfile declaration is the safety net for plain `docker run`.

7. **`restart: unless-stopped`.** All three services are set to restart unless explicitly stopped (`docker-compose.yml:9, 32, 45`). This makes the stack self-healing after a daemon reboot or transient crash.

8. **No model pre-bake.** The image ships with `/app/models` and `/app/data` as empty directories but does not include the GGUF file. This keeps the image small but means the user must mount or `COPY` the model in. The fallback chain in `config.py:45-60` accommodates both local Windows and Docker paths.

9. **Ports exposed for both services from a single image.** `EXPOSE 8000 8501` (`Dockerfile:49`) documents both ports even though only one is used per container. This is informational; only the actual mapped port (`docker-compose.yml:12, 35`) is reachable from the host.
