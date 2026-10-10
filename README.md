# Opportunities Details

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](pyproject.toml)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.128+-009688.svg)](https://fastapi.tiangolo.com/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.54+-FF4B4B.svg)](https://streamlit.io/)
[![Tests](https://img.shields.io/badge/Tests-86%20Passed-brightgreen.svg)](tests/)
[![CI/CD](https://github.com/PraveenRepswal/Opportunities-Details/actions/workflows/ci.yml/badge.svg)](https://github.com/PraveenRepswal/Opportunities-Details/actions/workflows/ci.yml)

A high-performance, privacy-first **Retrieval-Augmented Generation (RAG)** platform designed to aggregate, structure, and query global scholarships, fellowships, grants, and internships. 

The system continuously crawls opportunity portals asynchronously, distills raw announcements into structured metadata using **Small Language Models (SLMs)** and deterministic rule engines, indexes opportunities using **hybrid semantic + lexical search**, and serves answers through a **FastAPI** backend and an interactive **Streamlit** user interface powered by local LLMs (**LlamaCPP** / **Ollama**).

---

## 🚀 Key Features

* **Decoupled Microservice Architecture:** Dedicated **FastAPI** backend handling RAG, vector search, scraping jobs, and model inference, completely decoupled from frontend clients.
* **SLM Structured Opportunity Extraction (`backend/metadata_extractor.py`):** 
  * Extracts high-signal attributes (`title`, `organization`, `location`, `deadline`, `type`, `eligibility`, `benefits`, `summary`) from raw announcements.
  * Employs on-device SLMs (e.g. Liquid LFM-350M or Qwen 0.8B) via GBNF grammar-constrained decoding on `llama-server` or Ollama.
  * Robust deterministic fallback ensuring 0ms sentence-bounded summaries even when models are offline.
* **Explore Opportunities Directory:**
  * Clean, single-column table catalog directly rendering structured opportunities from SQLite.
  * Real-time keyword filtering, deadline indicators, opportunity type badges, and direct application links.
* **Hybrid Search & Optional Reranking (`backend/rag.py`):**
  * Ensemble retrieval combining dense **FAISS** vector search (`intfloat/e5-small-v2`) with sparse **BM25** lexical keyword matching.
  * Hierarchical parent-child chunking preserving global context for retrieved document passages.
  * Optional on-demand re-ranking via **CrossEncoder** (`ms-marco-MiniLM-L12-v2`) when high precision is requested.
* **Needle Fast Query Routing (`backend/agent.py`):**
  * Sub-millisecond deterministic routing for high-frequency queries (e.g. "Top N fully funded scholarships", curated roundups) bypassing LLM latency.
* **Agentic Query Routing & Tool Indicators (`backend/agent.py`):**
  * LangGraph-powered agent router distinguishing between conversational banter, local knowledge-base queries, and live web search fallbacks via DuckDuckGo.
  * Sleek, non-expandable native status badges (e.g., `🌐 Searched the web`, `🔍 Deep Dived into Opportunity`) in the Streamlit UI displaying active tool usage without exposing raw internals.
* **Local & Privacy-First Inference:**
  * Fully self-hosted inference via **LlamaCPP** (`llama-server`) or **Ollama**; zero telemetry or external API costs.
* **Semantic Answer Cache (`backend/answer_cache.py`):**
  * SQLite-backed embedding similarity cache returning instant answers for recurring queries, automatically invalidated upon corpus re-indexing.
* **Offline Speech-to-Text (`backend/stt.py`):**
  * Hands-free voice querying in ~125ms on CPU using **Moonshine Tiny**, leaving 100% of GPU VRAM for LLM generation.
* **Decoupled Scraping & Extraction Pipelines (`scraper.py` & `extract.py`):**
  * `scraper.py` handles fast, resilient multi-portal crawling and persists raw opportunities to SQLite without blocking on model inference.
  * `extract.py` provides dedicated SLM structured extraction with automatic regex fallback and self-managing `llama-server` process lifecycles (auto-starts, extracts, and terminates to release GPU VRAM).
* **Automated Application Launcher (`pipeline.py`):**
  * Launches the full stack (`llama-server` chat LLM, FastAPI backend, and Streamlit UI) directly with zero port conflicts, alongside optional extraction commands (`--extract`).

---

## 🛠️ Tech Stack

| Layer | Technologies |
| :--- | :--- |
| **Backend & API** | FastAPI, Uvicorn, Pydantic, HTTPX |
| **LLM & Agent Framework** | LangChain (Core, Community, Ollama, LlamaCpp), LangGraph |
| **Vector DB & Search** | FAISS, BM25, CrossEncoder (`ms-marco-MiniLM-L12-v2`) |
| **Embeddings** | HuggingFace (`intfloat/e5-small-v2`) |
| **Models Supported** | Qwen 2.5 / 3.5, LFM-350M, LlamaCPP (GGUF), Ollama |
| **Scraping Engine** | `aiohttp`, `asyncio`, BeautifulSoup4, Trafilatura |
| **Speech-to-Text** | UsefulSensors Moonshine Tiny (ONNX / PyTorch) |
| **Database** | SQLite (`opportunities_chat.db`) |
| **Frontend** | Streamlit |
| **Package Manager** | `uv` / `pip` |

---

## 📋 Prerequisites

1. **Python 3.12+**
2. **Local LLM Backend** (choose one):
   * **LlamaCPP / llama-server** (Recommended): Download your desired GGUF model (e.g. `Qwen3.5-4B-IQ4_NL.gguf`) into `models/` and serve it via `llama-server`:
     ```bash
     llama-server.exe -m models/Qwen3.5-4B-IQ4_NL.gguf --port 8080 -ngl 99 -c 8192
     ```
   * **Ollama**: Install from [ollama.com](https://ollama.com/) and pull a model:
     ```bash
     ollama pull qwen2.5:3b
     ```
3. *(Optional)* **Moonshine STT** weights are downloaded automatically on first voice query.

---

## ⚡ Quick Start

### 1. Clone & Install Dependencies

Using [`uv`](https://github.com/astral-sh/uv) (recommended):
```bash
git clone https://github.com/PraveenRepswal/Opportunities-Details.git
cd Opportunities-Details
uv sync
```

Or using standard `pip`:
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Environment

Copy `.env.example` to `.env` and adjust host, port, or model settings if needed:
```bash
cp .env.example .env
```

### 3. Ingest & Extract Data (Optional Initial Run)

1. **Scrape Raw Opportunities** (fast, pure web crawler):
   ```bash
   python scraper.py
   ```

2. **Extract Structured Profiles** (SLM with automatic regex fallback):
   ```bash
   python extract.py
   ```

### 4. Start the Application

#### Option A: Automated Pipeline (All-in-One)
Launches the local `llama-server` LLM, FastAPI backend, and Streamlit frontend together:
```bash
python pipeline.py
```

#### Option B: Manual Service Startup
1. **Start the FastAPI Backend:**
   ```bash
   uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
   ```
   * Interactive Swagger UI: **http://127.0.0.1:8000/docs**

2. **Launch the Streamlit Frontend (in a separate terminal):**
   ```bash
   streamlit run streamlit_app.py
   ```
   * Web UI: **http://localhost:8501**

---

## 🖥️ Streamlit Interface Overview

The frontend is divided into two primary modes:

1. **💬 Chat Assistant**:
   * Multi-turn RAG chat interface with real-time SSE token streaming.
   * Hands-free voice input via local Moonshine STT recorder.
   * Deep Thinking expander revealing internal reasoning steps.
   * Suggested quick-query chips for rapid exploration.
   * Chat session history management in the sidebar.

2. **🔎 Explore Opportunities**:
   * Clean, single-column table directory showing all verified opportunities.
   * Keyword search across titles, summaries, and contents.
   * Metadata pills displaying opportunity type, deadline countdowns, host organization, location, and direct source portal links.
   * Complete, grammatically bounded summaries with key eligibility and benefit highlights.

---

## 🐳 Docker Deployment

Run the complete stack (Backend + Frontend + Ollama) using Docker Compose:

```bash
docker-compose up --build
```
* **FastAPI Backend:** http://localhost:8000
* **Streamlit UI:** http://localhost:8501

---

## 🔌 API Endpoints Summary

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/v1/health` | Service health status, index counts, cache metrics |
| `POST` | `/api/v1/chat` | RAG chat & answer generation (synchronous JSON) |
| `POST` | `/api/v1/chat/stream` | RAG chat with Server-Sent Events (SSE) token streaming |
| `POST` | `/api/v1/transcribe` | Local speech-to-text audio transcription (Moonshine) |
| `GET` | `/api/v1/opportunities` | Search and list indexed opportunities (supports up to 1000 items) |
| `GET` | `/api/v1/opportunities/curated` | List curated roundups and Top-N compilations |
| `GET` | `/api/v1/opportunities/{opp_id}` | Retrieve full details of an opportunity by ID |
| `POST` | `/api/v1/scrape` | Trigger asynchronous background scraping pipeline |
| `GET` | `/api/v1/scrape/status` | Current status of the background scraper job |
| `GET` | `/api/v1/sessions` | List persistent chat sessions |
| `POST` | `/api/v1/sessions` | Create a new chat session |
| `GET` | `/api/v1/sessions/{session_id}/messages` | Retrieve conversation history for a session |
| `DELETE` | `/api/v1/sessions/{session_id}` | Delete a chat session and its messages |

> Root aliases (`/chat`, `/chat/stream`, `/opportunities`, `/health`) are also supported for backwards compatibility.

---

## 🧪 Testing

The test suite contains **86 comprehensive unit and integration tests** covering all modules:

```bash
pytest tests/
```

Test coverage includes:
* `test_metadata_extractor.py`: Rule-based deadline parsing, entity extraction, and SLM structured profiling.
* `test_routing.py`: Fast deterministic intent routing, NeedleRouter deep dives, and Top-N queries.
* `test_answer_cache.py`: Cosine similarity caching, epoch invalidation, and TTL.
* `test_rate_limit.py`: Sliding-window request quotas and client IP isolation.
* `test_scrapers_base.py`: Robust crawler networking, backoff retry, and date extraction.

---

## 📂 Project Structure

```
Opportunities-Details/
├── .github/
│   └── workflows/ci.yml         # GitHub Actions CI pipeline (Ruff + Pytest)
├── backend/
│   ├── main.py                  # FastAPI application & REST endpoint definitions
│   ├── rag.py                   # RAG pipeline, FAISS vectorstore, BM25 & CrossEncoder
│   ├── agent.py                 # LangGraph router, NeedleRouter, & live web fallback
│   ├── answer_cache.py          # SQLite semantic answer caching engine
│   ├── database.py              # SQLite persistence for sessions & opportunities (WAL mode)
│   ├── metadata_extractor.py    # Hybrid rule-based & SLM structured profiling
│   ├── rate_limit.py            # Sliding-window rate limiting middleware
│   ├── stt.py                   # Moonshine speech-to-text transcriber
│   ├── text_utils.py            # Mojibake normalization, sentence splitting, title casing
│   └── schemas.py               # Pydantic data schemas and response models
├── scrapers/                    # Multi-portal asynchronous scrapers
│   ├── base.py                  # Base scraper class with retry & SSL fallback
│   ├── youthop.py               # Youth Opportunities crawler
│   ├── greatyop.py              # Greatyop crawler
│   ├── scholars4dev.py          # Scholars4Dev crawler
│   └── ...
├── tests/                       # Complete pytest suite
├── config.py                    # Centralized settings & model configuration (Pydantic v2)
├── docker-compose.yml           # Multi-container orchestration
├── Dockerfile                   # Multi-stage production container definition
├── pipeline.py                  # Unified CLI orchestrator, model swapper, and batch seeder
├── scraper.py                   # CLI scraping orchestrator
└── streamlit_app.py             # Streamlit dual-tab user interface
```

---

## 🚀 Roadmap

### 1. Architecture & Data Pipeline Quality
- [ ] **Decoupled Scheduled Ingestion**: Move `scraper.py` execution into a dedicated background worker (APScheduler / Celery beat) with persisted run history and retry backoff.
- [ ] **Near-Duplicate Detection & Source Health Monitoring**: Cross-portal fuzzy deduplication (`rapidfuzz` token similarity with embedding cosine fallback) to collapse identical opportunities republished across portals.

### 2. Search & Retrieval Intelligence
- [ ] **Structured Metadata Filtering End-to-End**: Expose `deadline`, `location`, `organization`, and `type` columns as query parameters on `GET /api/v1/opportunities`.
- [ ] **Query Understanding Layer**: LLM-driven query rewriting, HyDE hypothetical-document expansion, and constraint extraction.
- [ ] **Citation & Source Link Formatting**: Render clickable application URLs inline in chat responses.
- [ ] **RAG Evaluation Harness**: Golden Q&A dataset scored with Ragas (faithfulness, context precision/recall, answer relevancy).

### 3. REST API Hardening & AI Safety
- [ ] **API Authentication**: API-key or OAuth2 JWT security on `/api/v1` endpoints.
- [ ] **Prompt-Injection Defense & Output Guardrails**: Strict delimiting of web text vs system instructions and instruction-stripping.

### 4. Advanced Agentic Tooling
- [ ] **Autonomous Document & Motivation Letter Generator**: Tool-equipped agent (`draft_motivation_letter`) drafting tailored Statements of Purpose.
- [ ] **Self-Healing Scraper Agent**: Diagnostic agent fetching page DOM via Playwright to auto-patch outdated CSS selectors.
- [ ] **Model Context Protocol (MCP) Server**: Expose search tools and scrape triggering as an MCP server.
- [ ] **Full-Duplex Voice Assistant (STT -> LLM -> TTS)**: Add streaming local text-to-speech (Kokoro / Piper) for fully offline spoken conversation.

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.