# Dependencies — `pyproject.toml` and `requirements.txt`

## 1. Purpose & Overview

The project uses a **dual dependency definition**:

- **`pyproject.toml`** is the **canonical** source of truth. It declares the project metadata (`name`, `version`, `description`, `requires-python`) and the high-level dependency list using the modern PEP 621 / PEP 735 format. It also configures **ruff** lint rules and the **uv** package manager's PyTorch source index.
- **`requirements.txt`** is a **generated, fully resolved** flat list produced by `uv export --format requirements-txt --no-hashes --no-dev --no-emit-project` (see the file header at `requirements.txt:1-3`). It pins every transitive dependency to an exact version and includes `# via …` comments documenting the dependency graph. The `Dockerfile` and the legacy `pip install -r requirements.txt` workflow consume this file.

The combination lets the developer work in a modern, constraint-resolved workflow (uv) while keeping a portable, lockfile-equivalent file that any `pip`-based tool (including the multi-stage Docker build) can consume directly.

## 2. Architecture

```
┌────────────────────────────┐
│   pyproject.toml           │  (canonical metadata + abstract deps)
│   [project]                │  → consumed by `uv sync`, IDEs
│   [dependency-groups]      │
│   [tool.ruff]              │  → consumed by ruff
│   [tool.uv]                │  → consumed by uv
└────────────┬───────────────┘
             │  uv export --format requirements-txt
             ▼
┌────────────────────────────┐
│   requirements.txt         │  (fully resolved, pinned, with provenance)
│   ~130 packages            │  → consumed by Dockerfile, CI
└────────────┬───────────────┘
             │
             ├─► Docker builder stage: `pip install --prefix=/install -r requirements.txt`
             │   (Dockerfile:16-17)
             └─► (potentially) bare-metal `pip install -r requirements.txt`
```

The `uv.lock` file in the repository root is the lockfile that `uv` consults when regenerating `requirements.txt`. The `pyproject.toml` references it implicitly via the `tool.uv` table.

## 3. `pyproject.toml` — Field-by-field

### 3.1 `[project]`  (`pyproject.toml:1-106`)

| Field | Value | Line | Notes |
|---|---|---|---|
| `name` | `opportunities-details` | `:2` | Distribution name; used in `# via` provenance comments. |
| `version` | `0.5.0` | `:3` | SemVer; reflects the 0.5.x feature set (Moonshine STT, semantic cache, debug inspector). |
| `description` | `High-Performance RAG System & Scraper for Global Opportunity & Scholarship Data` | `:4` | |
| `readme` | `README.md` | `:5` | |
| `requires-python` | `>=3.12,<3.14` | `:6` | Matches CI Python 3.12 and excludes 3.14+ (which is not yet released as of this writing). |

### 3.2 `dependencies` (abstract list)  (`pyproject.toml:7-106`)

A wide range of Python packages covering web, ML, scraping, and Streamlit. Selected highlights:

**Web / API server:**
- `fastapi>=0.128.0` (`:28`) — the REST/SSE backend.
- `uvicorn>=0.40.0` (`:103`) — ASGI server.
- `httpx==0.28.1` (`:37`) — async + sync HTTP client (used by tests and LLM clients).
- `requests==2.32.5` (`:77`) — sync HTTP used by the Streamlit frontend.
- `aiohttp>=3.13.2` (`:8`) — async HTTP used by the multi-portal scraper.
- `pydantic==2.12.4` (`:64`) and `pydantic-core==2.41.5` (`:65`) — typed settings and request/response schemas.

**LLM / RAG:**
- `langchain>=1.0.7` (`:45`), `langchain-classic>=1.0.7` (`:46`), `langchain-community>=0.4.1` (`:47`).
- `langchain-huggingface>=1.0.1` (`:48`) — for the embedding model.
- `langchain-ollama>=1.0.0` (`:49`) — Ollama provider.
- `langchain-openai>=1.0.0` (`:50`) — OpenAI-compatible provider.
- `langgraph>=0.2.0` (`:51`) — graph-based agent orchestration.
- `faiss-cpu>=1.12.0` (`:27`) — vector store.
- `sentence-transformers>=5.1.2` (`:80`) — embedding model wrapper.
- `flashrank>=0.2.10` (`:30`) — cross-encoder reranker.
- `rank-bm25>=0.2.2` (`:72`) — BM25 keyword retriever for the ensemble.
- `rapidfuzz>=3.14.3` (`:73`) — fuzzy deduplication.
- `transformers==4.57.6` (`:97`), `tokenizers==0.22.1` (`:91`), `safetensors==0.6.2` (`:79`).
- `torch` (`:93`) — pinned to the PyTorch CUDA 13.0 index (`pyproject.toml:129-137`).
- `ollama==0.6.1` (`:58`) — Ollama Python client.
- `whichllm>=0.5.10` (`:105`) — backend selection helper.
- `duckduckgo-search>=6.0.0` (`:52`) — web fallback search.
- `tiktoken>=0.12.0` (`:89`) — token counting.
- `tenacity==9.1.2` (`:87`) — retry logic.

**Web scraping & extraction:**
- `beautifulsoup4==4.14.2` (`:15`), `lxml==6.0.2` (`:53`), `lxml-html-clean==0.4.3` (`:54`).
- `trafilatura==2.0.0` (`:96`) — main content extraction.
- `justext==3.0.2` (`:44`), `readability-lxml==0.8.4.1` (`:74`) — alternative extractors.
- `courlan==1.3.2` (`:23`), `htmldate==1.9.4` (`:35`), `dateparser==1.2.2` (`:25`), `tld==0.13.1` (`:90`) — URL hygiene and date normalization.
- `cssselect==1.3.0` (`:24`) — CSS selector support for BeautifulSoup.

**Speech-to-text (Moonshine):**
- `soundfile>=0.12.0` (`:85`).
- (The Moonshine ONNX model itself is **not** in PyPI; `backend/stt.py` loads `UsefulSensors/moonshine-tiny` at runtime from the local model directory or Hugging Face Hub.)

**Streamlit UI:**
- `streamlit==1.54.0` (`:86`).
- `altair==6.0.0` (`:9`), `pydeck==0.9.1` (`:66`), `pyarrow==22.0.0` (`:63`), `pandas==2.3.3` (`:60`), `pillow==12.0.0` (`:61`) — Streamlit chart/table deps.
- `watchdog==6.0.0` (`:104`) — Streamlit file watcher.
- `tornado==6.5.2` (`:94`) — Streamlit web server.
- `gitpython==3.1.45` (`:33`) — Streamlit version display.
- `blinker==1.9.0` (`:16`) — Streamlit signal bus.

**Other utilities:**
- `jinja2==3.1.6` (`:41`), `pyyaml==6.0.3` (`:71`).
- `numpy==2.3.4` (`:57`) — used by FAISS, sentence-transformers, etc.
- `python-slugify==8.0.4` (`:69`), `text-unidecode==1.3` (`:88`).
- `protobuf==6.33.1` (`:62`), `jsonschema==4.25.1` (`:42`).
- `attrs`, `certifi`, `chardet`, `charset-normalizer`, `colorama`, `filelock`, `fsspec`, `h11`, `httpcore`, `idna`, `markupsafe`, `packaging`, `pytz`, `referencing`, `regex`, `rpds-py`, `six`, `smmap`, `sniffio`, `soupsieve`, `toml`, `tqdm`, `typing-extensions`, `typing-inspection`, `tzdata`, `tzlocal`, `urllib3`, `annotated-types`, `anyio`, `babel`, `cachetools`, `asttokens`, `executing`, `icecream`, `narwhals`, `pygments`, `python-dateutil`, `pydantic-core`.

A `^` on `>=` constraints (e.g. `fastapi>=0.128.0`) means the lockfile resolves to a specific version, which is then pinned in `requirements.txt` (e.g. `fastapi==0.136.3` at `requirements.txt:119`). A `==` exact pin in `pyproject.toml` forces that version in the lockfile.

### 3.3 `[dependency-groups]`  (`pyproject.toml:108-111`)

```toml
[dependency-groups]
dev = [
    "uv-bump>=0.2.0",
]
```

The only development dependency is **`uv-bump`**, a tiny utility for managing uv project versions. The `--no-dev` flag used in the `uv export` command that produces `requirements.txt` (`requirements.txt:2`) excludes this group from the Docker image.

### 3.4 `[tool.ruff]`  (`pyproject.toml:113-127`)

| Field | Value | Notes |
|---|---|---|
| `line-length` | `120` | |
| `target-version` | `"py312"` | |
| `extend-exclude` | `["scratch_*"]` | Skip `scratch_*` directories. |
| `lint.select` | `["E4", "E7", "E9", "F"]` | E4 (imports), E7 (statements), E9 (runtime), F (pyflakes). |
| `lint.ignore` | `["E402"]` | Allow imports after optional-dependency try/except blocks in `rag.py` and `agent.py`. |

The full CI lint pipeline is documented in `docs/ci_pipeline.md`.

### 3.5 `[tool.uv.index]` and `[tool.uv.sources]`  (`pyproject.toml:129-137`)

```toml
[[tool.uv.index]]
name = "pytorch"
url = "https://download.pytorch.org/whl/cu130"
explicit = true

[tool.uv.sources]
torch = [{ index = "pytorch" }]
torchvision = [{ index = "pytorch" }]
torchaudio = [{ index = "pytorch" }]
```

- Adds the **PyTorch CUDA 13.0** wheel index as a named source called `pytorch`.
- `explicit = true` means uv will only use this index for the packages listed in `tool.uv.sources`, not as a fallback for other packages. This is critical: it prevents `torch` from being silently pulled from a public PyPI mirror that might not have a CUDA 13 wheel.
- `torch`, `torchvision`, and `torchaudio` are pinned to this index. On Linux + CUDA 13.x hardware, the resulting install gets GPU-enabled builds. On Windows or on non-CUDA machines, uv falls back to the platform-appropriate default (CPU or ROCm).

> The `requirements.txt` reflects the resolved set for the platform that produced the lockfile. The `cuda-*` and `nvidia-*` packages (`requirements.txt:97-102, 318-360`) are tagged with `; sys_platform == 'linux'`, so they are skipped on Windows automatically.

## 4. `requirements.txt` — Layout

The file is **flat**, every package pinned with `==`, and every transitive entry has a `# via` provenance block (`requirements.txt:5-677`). Example:

```
faiss-cpu==1.14.2
    # via opportunities-details
fastapi==0.136.3
    # via opportunities-details
```

Some entries are platform-conditional:

```
cuda-bindings==13.3.1 ; sys_platform == 'linux'
    # via torch
greenlet==3.5.1 ; platform_machine == 'AMD64' or platform_machine == 'WIN32' or ...
    # via sqlalchemy
hf-xet==1.5.0 ; platform_machine == 'aarch64' or platform_machine == 'amd64' or ...
    # via huggingface-hub
triton==3.7.0 ; sys_platform == 'linux'
    # via torch
```

`pip` (modern enough — 20.3+) honors these PEP 508 markers. The Windows installer will skip the `cuda-*`, `nvidia-*`, and `triton` lines.

**Key resolved versions** (from `requirements.txt`):

| Package | Version | Line |
|---|---|---|
| `aiohttp` | `3.14.0` | `:7` |
| `duckduckgo-search` | `8.1.1` | `:111` |
| `faiss-cpu` | `1.14.2` | `:117` |
| `fastapi` | `0.136.3` | `:119` |
| `flashrank` | `0.2.10` | `:127` |
| `langchain` | `1.3.4` | `:218` |
| `langchain-community` | `0.4.2` | `:224` |
| `langchain-core` | `1.5.6` | `:226` |
| `langchain-huggingface` | `1.2.2` | `:239` |
| `langchain-ollama` | `1.1.0` | `:241` |
| `langchain-openai` | `1.5.2` | `:243` |
| `langgraph` | `1.2.4` | `:251` |
| `numpy` | `2.3.4` | `:302` |
| `ollama` | `0.6.1` | `:361` |
| `openai` | `2.54.0` | `:367` |
| `pandas` | `2.3.3` | `:386` |
| `pydantic` | `2.12.4` | `:413` |
| `sentence-transformers` | `5.5.1` | `:516` |
| `soundfile` | `0.14.0` | `:535` |
| `streamlit` | `1.54.0` | `:547` |
| `torch` | `2.12.0` | `:583` |
| `tornado` | `6.5.2` | `:587` |
| `transformers` | `4.57.6` | `:601` |
| `uvicorn` | `0.49.0` | `:658` |
| `whichllm` | `0.5.10` | `:668` |

## 5. Lifecycle

### 5.1 Adding a dependency
1. Edit `pyproject.toml` and add the package to `dependencies` (or `dev` for development-only). Use `>=` for new packages (let the lockfile pin the exact version), `==` only when you need a specific known-good build.
2. Run `uv sync` to update the lockfile and install into the venv.
3. Run `uv export --format requirements-txt --no-hashes --no-dev --no-emit-project > requirements.txt` to regenerate the flat file.
4. Open a PR — CI will run `ruff` and `pytest` on the new lockfile.

### 5.2 Installing for development
```bash
uv sync
```
This installs everything in `[project.dependencies]` and `[dependency-groups].dev` into `.venv/`.

### 5.3 Installing in Docker
The `Dockerfile` builder stage does (`Dockerfile:16-17`):
```bash
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt
```
Then the runtime stage copies `/install` to `/usr/local` (`Dockerfile:40`). No `uv` is required inside the image, which keeps the image slim.

### 5.4 CI
The CI workflow does **not** install from `requirements.txt` (see `docs/ci_pipeline.md`). The `test` job installs only `pytest dateparser pydantic-settings fastapi httpx` — enough to import the modules currently under test.

## 6. Configuration / environment variables

Neither file reads environment variables directly. `pyproject.toml` is read by `uv` and by tools that respect PEP 621. `requirements.txt` is read by `pip` (or any PEP 503-compatible installer).

The `config.py` module reads `.env` via `pydantic-settings` for runtime configuration — that is documented separately in `docs/environment_config.md`.

## 7. Network / API calls

The files themselves are static. They trigger network activity when consumed:

- **`uv sync`** — fetches packages from PyPI (`https://pypi.org`) and the configured PyTorch CUDA 13.0 index (`https://download.pytorch.org/whl/cu130`).
- **`pip install -r requirements.txt`** — fetches from PyPI (and the platform-conditional CUDA packages from `https://download.pytorch.org/whl/cu130` on Linux).

The project itself makes no API calls at install time beyond these index reads.

## 8. Error handling / fallbacks

- **Platform markers:** if `pip` or `uv` is too old to understand PEP 508 markers, the `; sys_platform == 'linux'` blocks will fail to parse. Use `pip >= 20.3` or `uv` (any modern version).
- **PyTorch index not reachable:** if the CUDA 13.0 index at `https://download.pytorch.org/whl/cu130` is unreachable and `explicit = true` is set, `torch` will fail to install. On a CPU-only or non-Linux machine, you can comment out the `tool.uv.sources` block and re-export; uv will then pull `torch` from PyPI's default index.
- **Lockfile drift:** if `pyproject.toml` is updated without running `uv lock`, `requirements.txt` may be out of date. The header comment at `requirements.txt:1-3` warns about this and tells you to regenerate.
- **Version conflicts:** the lockfile is the resolution; if you change `pyproject.toml` in a way that no valid resolution exists, `uv lock` will error. Resolve by relaxing constraints or pinning a specific known-good combination.
- **Windows + CUDA packages:** the `requirements.txt` already excludes CUDA packages on Windows via the `sys_platform == 'linux'` markers, so a Windows `pip install -r requirements.txt` will not try to fetch the NVIDIA wheels.

## 9. Notable design decisions

1. **`pyproject.toml` is the source of truth, `requirements.txt` is the build artifact.** Editing `requirements.txt` by hand is unsupported; always regenerate from the lockfile. The header comment at `requirements.txt:1-3` makes this contract explicit.

2. **Most packages are version-pinned with `==` in `requirements.txt` but use `>=` in `pyproject.toml`.** This gives the developer flexibility (a fresh `uv lock` will pick up patch versions) while guaranteeing reproducible Docker builds (the lockfile is the source of pins, not the abstract spec).

3. **The `tool.uv.sources` `explicit = true` flag.** This is a deliberate safety measure: it prevents accidental CPU-only `torch` installs on a developer machine that happens to have a working public-PyPI fallback, which would otherwise cause silent CUDA failures at runtime.

4. **Minimal `dev` group.** Only `uv-bump` is included. This keeps the test/CI matrix from ballooning. Test-only dependencies (e.g. `pytest`, `httpx`) are installed in-line in the CI workflow (`.github/workflows/ci.yml:47`) instead of being declared in `pyproject.toml`.

5. **Ruff config lives in `pyproject.toml` rather than a `.ruff.toml` file.** This is the modern recommendation — a single config file for both project metadata and tooling.

6. **No `setup.py`.** Pure `pyproject.toml` (PEP 621) means the project is installable by any PEP 517/518 frontend (`uv`, `pip`, `poetry` in PEP 621 mode, `hatch`).

7. **No `[project.scripts]` or `[project.entry-points]`.** The project ships as a library/service rather than a CLI tool. The entry points are the Uvicorn invocation (`Dockerfile:56`, `docker-compose.yml:10`) and the Streamlit invocation (`docker-compose.yml:33`).

8. **The lockfile is committed (`uv.lock`).** This is the uv-recommended pattern and guarantees that any developer or CI run that re-exports `requirements.txt` produces a byte-identical file.

9. **Heavy ML stack is on the user's machine and in Docker, not in CI.** The CI `test` job deliberately does not install `torch`/`transformers`/`faiss-cpu` to keep the runner fast. Tests that need these must either mock them or be promoted to a separate integration job.
