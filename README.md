# Opportunities Details

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](pyproject.toml)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.128+-009688.svg)](https://fastapi.tiangolo.com/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.54+-FF4B4B.svg)](https://streamlit.io/)

A high-performance **Retrieval-Augmented Generation (RAG)** system designed to aggregate and query global scholarship and opportunity data. This project scrapes multiple opportunity portals asynchronously, indexes them using vector and lexical search, and uses a dedicated **FastAPI** backend coupled with local LLMs (**Ollama** / **LlamaCPP**) to answer user queries with high precision and privacy.

---

## 🚀 Key Features

* **Decoupled Microservice Architecture:** Dedicated **FastAPI** backend handling vector search, scraping, and LLM inference, decoupled from visual frontends.
* **Multi-Client Support:** RESTful API ready for Streamlit, React, Vue, CLI, or mobile clients.
* **High-Speed Concurrent Scraper:** Asynchronous pipeline using `aiohttp` and `asyncio` fetching data from 6+ opportunity portals simultaneously.
* **Hybrid Search & Reranking:** Ensemble retrieval using **FAISS** (vector similarity) + **BM25** (lexical search) with **CrossEncoder** reranking (`ms-marco-MiniLM-L12-v2`).
* **Local RAG & Privacy First:** Runs completely locally via **Ollama** or **LlamaCPP**, eliminating cloud API costs and keeping data private.
* **Real-time SSE Token Streaming:** Server-Sent Events (SSE) streaming support for token rendering and reasoning `<think>` block tracking.

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
2. **Ollama**: Download and install from [ollama.com](https://ollama.com/).
3. **Download LLM Model**:
   ```bash
   ollama pull qwen3.5:4b
   ```

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
| `POST` | `/api/v1/query` | RAG search & answer generation (supports streaming) |
| `POST` | `/api/v1/scrape` | Trigger asynchronous scraping pipeline |
| `GET` | `/api/v1/stats` | System, cache, and store metrics |

---

## 📂 Project Structure

```
Opportunities-Details/
├── backend/
│   ├── main.py          # FastAPI web server & endpoints
│   ├── rag.py           # RAG engine, vectorstore, reranker & SSE streaming
│   └── schemas.py       # Pydantic request/response schemas
├── scrapers/            # Individual site scrapers (YouthOp, Scholars4Dev, etc.)
│   ├── base.py          # Base scraper class & Jaccard deduplication
│   └── ...
├── .env.example         # Environment configuration template
├── config.py            # Centralized Pydantic settings & model resolver
├── docker-compose.yml   # Multi-container Orchestration
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