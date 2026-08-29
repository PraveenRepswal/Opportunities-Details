# Opportunities Details

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](pyproject.toml)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.128+-009688.svg)](https://fastapi.tiangolo.com/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.54+-FF4B4B.svg)](https://streamlit.io/)
[![CI/CD](https://github.com/PraveenRepswal/Opportunities-Details/actions/workflows/ci.yml/badge.svg)](https://github.com/PraveenRepswal/Opportunities-Details/actions/workflows/ci.yml)

A high-performance **Retrieval-Augmented Generation (RAG)** system designed to aggregate and query global scholarship and opportunity data. This project scrapes multiple opportunity portals asynchronously, indexes them using vector and lexical search, and uses a dedicated **FastAPI** backend coupled with local LLMs (**Ollama** / **LlamaCPP**) to answer user queries with high precision and privacy.

---

## 🚀 Key Features

* **Decoupled Microservice Architecture:** Dedicated **FastAPI** backend handling vector search, scraping, and LLM inference, decoupled from visual frontends.
* **Multi-Client Support:** RESTful API ready for Streamlit, React, Vue, CLI, or mobile clients.
* **High-Speed Concurrent Scraper:** Asynchronous pipeline using `aiohttp` and `asyncio` fetching data from 6+ opportunity portals simultaneously, with hybrid metadata extraction (rules + local LLM fallback) capturing deadlines, organizations, locations, and types per opportunity.
* **Hybrid Search & Reranking:** Ensemble retrieval using **FAISS** (vector similarity) + **BM25** (lexical search) with **CrossEncoder** reranking (`ms-marco-MiniLM-L12-v2`).
* **Local RAG & Privacy First:** Runs completely locally via **Ollama** or **LlamaCPP**, eliminating cloud API costs and keeping data private.
* **Semantic Answer Cache:** SQLite-backed embedding cache (`backend/answer_cache.py`) that returns instant responses for recurring/similar questions, automatically invalidated on re-indexing.
* **Tiered API Rate Limiting:** Sliding-window rate limiter middleware (`backend/rate_limit.py`) protecting chat, STT, and scraping endpoints against abuse.
* **Agentic Query Routing:** Lightweight LangGraph-powered router (`backend/agent.py`) that classifies prompts as direct chat vs. RAG/tool usage, with DuckDuckGo web search fallback.
* **Persistent Chat Sessions:** Multi-session chat history stored in SQLite (`backend/database.py`) with full CRUD via the REST API.
* **Speech-to-Text:** Voice queries transcribed locally via **Moonshine tiny** (`backend/stt.py`).

---

## 🛠️ Tech Stack

* **Backend Framework:** FastAPI, Uvicorn, Pydantic
* **LLM Orchestration:** LangChain (Core, Community, Ollama, LlamaCpp)
* **Vector DB & Search:** FAISS, BM25, CrossEncoder Reranker
* **Scraping Engine:** `aiohttp`, `asyncio`, BeautifulSoup4, Trafilatura
* **Embeddings:** HuggingFace (`intfloat/e5-small-v2`)
* **Frontend:** Streamlit
* **Package Manager:** `uv` / `pip`

---

## 📋 Prerequisites

1. **Python 3.12+**
2. **LLM Backend** (choose one):
   * **LlamaCPP** (default): Download the GGUF model `Qwen3.5-4B-IQ4_NL.gguf` into a `models/` directory and serve it via `llama-server` (default: `http://localhost:8080`).
   * **Ollama**: Install from [ollama.com](https://ollama.com/) and pull a model:
     ```bash
     ollama pull qwen3.5:4b
     ```
3. *(Optional)* **Moonshine STT** weights are downloaded automatically on first use of the `/transcribe` endpoint.

---

## ⚡ Quick Start

### 1. Clone & Install Dependencies

Using [`uv`](https://github.com/astral-sh/uv) (recommended):
```bash
git clone https://github.com/YOUR_USERNAME/Opportunities-Details.git
cd Opportunities-Details
uv sync
```

Or using standard `pip`:
```bash
pip install -r requirements.txt
```

### 2. Configure Environment

Copy `.env.example` to `.env` and adjust settings if necessary:
```bash
cp .env.example .env
```

### 3. Run the Scraper (Optional Initial Ingestion)

To pre-populate the database and vector store with live opportunity data:
```bash
python scraper.py
```

### 4. Start the FastAPI Backend

```bash
uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```
* The backend automatically pre-warms scrapers, FAISS vector store, and model weights on startup.
* Interactive API Documentation (Swagger UI): **http://127.0.0.1:8000/docs**

### 5. Launch the Streamlit Frontend

In a separate terminal window:
```bash
streamlit run streamlit_app.py
```
Open **http://localhost:8501** in your browser.

---

## 🐳 Docker Deployment

Run the entire application (Backend + Frontend + Ollama) using Docker Compose:

```bash
docker-compose up --build
```
* **FastAPI Backend:** http://localhost:8000
* **Streamlit UI:** http://localhost:8501

---

## 🔌 API Endpoints Summary

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/v1/health` | Service health status |
| `POST` | `/api/v1/chat` | RAG chat & answer generation (non-streaming) |
| `POST` | `/api/v1/chat/stream` | RAG chat with SSE token streaming |
| `POST` | `/api/v1/transcribe` | Speech-to-text transcription (Moonshine) |
| `GET` | `/api/v1/opportunities` | List scraped opportunities (paginated) |
| `GET` | `/api/v1/opportunities/{opp_id}` | Get a single opportunity |
| `POST` | `/api/v1/scrape` | Trigger asynchronous scraping pipeline |
| `GET` | `/api/v1/scrape/status` | Status of the last scraping job |
| `GET` | `/api/v1/sessions` | List chat sessions |
| `POST` | `/api/v1/sessions` | Create a new chat session |
| `GET` | `/api/v1/sessions/{session_id}/messages` | Get messages of a session |
| `DELETE` | `/api/v1/sessions/{session_id}` | Delete a chat session |

> Legacy unversioned aliases (`/chat`, `/chat/stream`, `/sessions`, etc.) are also registered for backwards compatibility.

---

## 📂 Project Structure

```
Opportunities-Details/
├── .github/
│   └── workflows/ci.yml # GitHub Actions CI pipeline (lint + tests)
├── backend/
│   ├── main.py          # FastAPI web server & endpoints
│   ├── rag.py           # RAG engine, vectorstore, reranker & SSE streaming
│   ├── agent.py         # Query router (direct chat vs. RAG/tools) & web search fallback
│   ├── answer_cache.py  # SQLite-backed semantic answer caching engine
│   ├── database.py      # SQLite persistence for chat sessions & opportunities
│   ├── metadata_extractor.py # Hybrid metadata extraction (rules + LLM enrichment)
│   ├── rate_limit.py    # Sliding-window rate limiter middleware
│   ├── stt.py           # Moonshine speech-to-text transcriber
│   └── schemas.py       # Pydantic request/response schemas
├── scrapers/            # Individual site scrapers (YouthOp, Scholars4Dev, etc.)
│   ├── base.py          # Base scraper class & Jaccard deduplication
│   └── ...
├── tests/               # Pytest suite (answer cache, metadata extractor, rate limiter)
├── .dockerignore        # Docker build ignore rules
├── .env.example         # Environment configuration template
├── config.py            # Centralized Pydantic settings & model resolver
├── docker-compose.yml   # Multi-container orchestration
├── Dockerfile           # Multi-stage container build definition
├── features.md          # Comprehensive feature list & roadmap
├── LICENSE              # MIT Open Source License
├── pyproject.toml       # Project metadata & dependencies
├── README.md            # Project documentation
├── requirements.txt     # Standard requirements file
├── scraper.py           # Scraping orchestrator CLI
└── streamlit_app.py     # Streamlit web UI frontend
```

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.