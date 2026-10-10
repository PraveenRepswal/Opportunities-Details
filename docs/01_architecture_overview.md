# 01 — System Architecture & Overview

## 1. Purpose & Vision

**Opportunities-Details** is a high-performance, production-ready Retrieval-Augmented Generation (RAG) platform, crawler, and search engine for global scholarships, fellowships, grants, and internships.

The system addresses the information fragmentation across global scholarship databases:
1. **Automated Scraping & Ingestion**: Crawls and parses structured content from 6 major scholarship portals with content hashing and deduplication.
2. **Metadata Enrichment**: Employs deterministic regex rules (Stage 1) and local Small Language Models (SLM via GGUF/Ollama, Stage 2) to extract normalized deadlines, organizations, target locations, program levels, and funding types.
3. **Hybrid Search & Retrieval**: Combines SQLite FTS5 (BM25 full-text search) for exact metadata and title matching with FAISS (`e5-small-v2`) vector search and `ms-marco-MiniLM-L12-v2` cross-encoder reranking.
4. **Intelligent Query Routing**: Distinguishes between conversational chit-chat, specific opportunity deep-dives (via `NeedleRouter`), broad semantic searches, and live web inquiries.
5. **Multi-Modal Client Interfaces**: A FastAPI backend providing REST and Server-Sent Events (SSE) streaming APIs, paired with a feature-rich Streamlit frontend featuring voice input (Moonshine STT), thought-chain visualization, and virtualized opportunity browsing.

---

## 2. System Architecture

```mermaid
flowchart TD
    subgraph Web_Portals["Web Portals (6 Sources)"]
        P1["Youth Opportunities"]
        P2["Greatyop"]
        P3["Scholars4Dev"]
        P4["ScholarshipsCorner"]
        P5["OpportunitiesCorner"]
        P6["OpportunitiesForYouth"]
    end

    subgraph Ingestion_Pipeline["Ingestion & Enrichment Pipeline"]
        SCR["scraper.py\n(Async Multi-Source Crawler)"]
        DEDUP["SHA-256 Content Deduplication"]
        EXTRACT["extract.py\n(Dedicated Extraction Pipeline)"]
        STAGE1["Regex Heuristics Fallback\n(metadata_extractor.py)"]
        STAGE2["SLM Structured Profiling\n(llama-server GBNF / Ollama)"]
    end

    subgraph Data_Storage["Persistent Storage Layer"]
        DB[("SQLite Database\n(opportunities_chat.db)")]
        FTS[("SQLite FTS5 Index\n(BM25 Full-Text Search)")]
        FAISS_STORE[("FAISS Vector Store\n(faiss_store/)")]
        BM25_STORE["BM25 In-Memory Index\n(Child Chunks)"]
        DOC_STORE["Parent Docstore\n(Full Context)"]
    end

    subgraph Core_Backend["FastAPI Backend (Port 8000)"]
        API["REST & SSE Streaming API\n(backend/main.py)"]
        RL["Sliding-Window Rate Limiter\n(backend/rate_limit.py)"]
        CACHE["Semantic & Hash Cache\n(backend/answer_cache.py)"]
        STT["Moonshine STT Engine\n(backend/stt.py)"]
        ROUTER["Fast Router & Needle Router\n(backend/agent.py)"]
        RAG["Hybrid RAG Pipeline\n(backend/rag.py)"]
        AGENT["LangGraph Tool Agent\n(Local RAG + DuckDuckGo)"]
    end

    subgraph Inference_Engine["LLM / SLM Inference"]
        OLLAMA["Ollama Server (11434)"]
        LLAMACPP["Local llama.cpp (GGUF)"]
    end

    subgraph Frontend["Streamlit Frontend (Port 8501)"]
        CHAT_UI["Interactive Chat Tab\n(SSE Token Stream + Badges)"]
        EXPLORE_UI["Explore Opportunities Tab\n(FTS5 Search + Virtualized Load More)"]
        VOICE_REC["Moonshine Voice Recorder"]
        INSPECTOR["Debug & Context Inspector"]
    end

    Web_Portals --> SCR
    SCR --> DEDUP --> STAGE1 --> STAGE2
    STAGE2 --> DB
    DB --> FTS
    DB --> FAISS_STORE
    DB --> BM25_STORE
    DB --> DOC_STORE

    Frontend <-->|HTTP / SSE| RL
    RL --> API
    API --> STT
    API --> CACHE
    API --> ROUTER
    ROUTER -->|Specific Entity| ROUTER
    ROUTER -->|Semantic Search| RAG
    ROUTER -->|Live Fallback| AGENT
    RAG --> FAISS_STORE & BM25_STORE & DOC_STORE
    RAG --> Inference_Engine
    AGENT --> Inference_Engine
```

---

## 3. Core Component Overview

| Component | Primary Files | Key Responsibilities |
|---|---|---|
| **API Server** | `backend/main.py` | FastAPI application exposing endpoints for chat streaming, opportunities CRUD, session management, audio transcription, and scraper triggers. |
| **Configuration** | `config.py` | Centralized, validated Pydantic v2 settings with environment variable aliases supporting nested prefixes (`MODEL__`, `API__`, `DATABASE__`). |
| **Data Persistence** | `backend/database.py`, `backend/text_utils.py` | SQLite connection manager with WAL mode, FTS5 virtual tables with BM25 ranking, SHA-256 deduplication, session/message storage, and text sanitation. |
| **Hybrid Retrieval (RAG)** | `backend/rag.py` | Parent-child document chunking, FAISS dense vector search (`e5-small-v2`), BM25 lexical retriever, Cross-Encoder reranker (`ms-marco-MiniLM-L12-v2`), and SSE streaming generator. |
| **Agent & Routing** | `backend/agent.py` | Multi-tier routing: Level-1 regex classifier, exact needle router with deep-dive formatting, and 2-tool LangGraph agent combining local RAG with live DuckDuckGo web search. |
| **Metadata & Profiling** | `backend/metadata_extractor.py` | Stage 1 regex & ISO date normalization (multilingual), and Stage 2 SLM JSON profile extraction into structured `OpportunityProfile` schemas. |
| **Semantic Cache** | `backend/answer_cache.py` | Query-embedding cosine similarity cache (0.93 threshold) and exact SHA1 config hash matching with automatic corpus invalidation on re-indexing. |
| **Rate Limiter** | `backend/rate_limit.py` | Tiered in-memory sliding-window rate limiting per client IP with `429 Too Many Requests` responses and `Retry-After` headers. |
| **Voice Transcription (STT)** | `backend/stt.py` | CPU-optimized automatic speech recognition using `UsefulSensors/moonshine-tiny` with audio decoding fallbacks (`soundfile` and `pydub`). |
| **Scraper Suite** | `scraper.py`, `scrapers/*.py` | Asynchronous multi-source crawlers with retry backoff, SSL fallback, and raw SQLite upserts. |
| **Extraction Pipeline** | `extract.py` | Standalone SLM profiling with auto-managed `llama-server` process lifecycle and automatic regex fallback. |
| **Application Launcher** | `pipeline.py` | Unified CLI orchestrator launching local LLM, FastAPI backend, and Streamlit frontend together. |
| **User Interface** | `streamlit_app.py` | Multi-tab UI featuring SSE streaming chat, formatted metadata badges, collapsible `<think>` reasoning blocks, FTS5 opportunity browser with incremental pagination, and debug inspect controls. |

---

## 4. End-to-End Request Lifecycles

### 4.1 Chat Query Lifecycle

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant UI as Streamlit Frontend
    participant API as FastAPI Backend
    participant Cache as Semantic Cache
    participant Router as Fast & Needle Router
    participant RAG as RAG Pipeline / Agent
    participant LLM as LLM (Ollama / LLaMA-CPP)
    participant DB as SQLite DB

    User->>UI: Submits Query ("Tell me about Chevening")
    UI->>API: POST /api/v1/chat/stream {prompt, session_id, history}
    API->>Cache: Check prompt & embedding similarity
    alt Cache Hit (Cosine >= 0.93)
        Cache-->>API: Cached response & metadata
        API-->>UI: Stream cached SSE tokens
    else Cache Miss
        API->>Router: Route query
        alt Needle Match (Exact Opportunity Detected)
            Router->>DB: Fetch specific opportunity & profile
            Router-->>API: Formatted Deep-Dive Context
        else Semantic / General Search
            Router->>RAG: Retrieve hybrid candidates
            RAG->>DB: Fetch FTS5 & Vector Chunks
            RAG->>RAG: CrossEncoder Rerank Top-K
        end
        API->>LLM: Stream prompt with enriched context
        LLM-->>API: Yield answer tokens
        API-->>UI: Stream SSE tokens (data: {"token": "..."})
        API->>DB: Save user & assistant messages
        API->>Cache: Store response in semantic cache
    end
    UI-->>User: Render styled badges & complete response
```

### 4.2 Scraping & Ingestion Lifecycle

```mermaid
sequenceDiagram
    autonumber
    participant CLI as scraper.py / pipeline.py
    participant Portals as 6 Opportunity Portals
    participant Extractor as Stage 1 & Stage 2 Extractors
    participant DB as SQLite & FTS5
    participant FAISS as FAISS Vector Store

    CLI->>Portals: Async crawl sitemaps / category listings
    Portals-->>CLI: HTML content & metadata
    CLI->>CLI: Clean text & compute SHA-256 content hash
    CLI->>DB: Check hash uniqueness
    CLI->>Extractor: Run Stage 1 Regex Rules (Deadlines, Orgs, Locations)
    CLI->>Extractor: (Optional) Run Stage 2 SLM Profiling (GGUF / Ollama)
    CLI->>DB: Upsert opportunities into SQLite (Auto-syncs FTS5 via Triggers)
    CLI->>FAISS: Chunk parent documents & generate e5-small-v2 embeddings
    CLI->>FAISS: Persist updated index to faiss_store/
```

---

## 5. Technology Stack & Design Decisions

| Category | Technology | Rationale |
|---|---|---|
| **Language Runtime** | Python 3.12 | Modern type hinting, high performance, compatible with PyTorch, Llama-CPP, and FastAPI. |
| **Backend Framework** | FastAPI + Uvicorn | High-throughput asynchronous HTTP routing, automatic OpenAPI validation, and clean SSE streaming. |
| **Relational & Search Storage** | SQLite 3 with FTS5 & WAL mode | Zero external database dependencies; WAL mode enables concurrent readers; FTS5 provides sub-millisecond BM25 keyword retrieval. |
| **Vector Store** | FAISS (`faiss-cpu`) | Fast, local, in-process L2 vector index avoiding heavy external vector database daemons. |
| **Embeddings** | `intfloat/e5-small-v2` | Lightweight (130MB), highly accurate representation for academic and opportunity domain texts. |
| **Reranking** | `cross-encoder/ms-marco-MiniLM-L12-v2` | High-precision cross-attention scoring over top-10 hybrid retrieval candidates. |
| **Local LLM / SLM** | Ollama & `llama-cpp-python` | Flexible dual-provider runtime supporting Qwen 2.5 / 3.5 GGUF models on CPU or GPU. |
| **Speech-to-Text** | `UsefulSensors/moonshine-tiny` | 50MB ONNX/Transformers model running on CPU in ~120ms, preserving GPU VRAM entirely for the LLM. |
| **Frontend Framework** | Streamlit | Rapid, stateful interactive UI development with direct custom HTML/CSS badge rendering and session management. |

