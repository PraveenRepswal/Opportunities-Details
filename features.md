# Features & Roadmap

This document tracks the implemented features and planned enhancements for the **Opportunities Details** RAG & Scraping platform.

---

## ✅ Currently Implemented Features

- **Async Multi-Portal Scraping**: Concurrent sitemap scraping using `aiohttp` & `BeautifulSoup` across 6 portals ([youthop](file:///x:/Opportunities-Details/scrapers/youthop.py), [greatyop](file:///x:/Opportunities-Details/scrapers/greatyop.py), [scholars4dev](file:///x:/Opportunities-Details/scrapers/scholars4dev.py), [scholarshipscorner](file:///x:/Opportunities-Details/scrapers/scholarshipscorner.py), [opportunitiescorner](file:///x:/Opportunities-Details/scrapers/opportunitiescorner.py), [opportunitiesforyouth](file:///x:/Opportunities-Details/scrapers/opportunitiesforyouth.py)) managed by [scraper.py](file:///x:/Opportunities-Details/scraper.py).
- **Hybrid RAG & Reranking**: Dense FAISS vector search (`e5-small-v2`) combined with BM25 keyword search via `EnsembleRetriever` and `CrossEncoder` reranking ([LlamaWrapper.py](file:///x:/Opportunities-Details/LlamaWrapper.py)).
- **Dual Inference Engine**: Provider toggle supporting both Ollama (`ChatOllama`) and LlamaCPP (`ChatLlamaCpp`).
- **FastAPI Backend REST API**: Exposed clean versioned REST endpoints (`/api/v1/chat`, `/api/v1/opportunities`, `/api/v1/health`, `/api/v1/scrape`) and root aliases ([main.py](file:///x:/Opportunities-Details/backend/main.py)) to decouple RAG backend logic from Streamlit frontend UI.
- **SQLite Persistent Database**: SQLite table storage for opportunity metadata with SHA-256 deduplication hash and structured search ([database.py](file:///x:/Opportunities-Details/backend/database.py)).
- **LangGraph / Tool-Calling Agent Framework**: Level 1 Fast Router (0.01ms) combined with a 2-tool LangGraph Agent (`search_local_opportunities` RAG pipeline + `search_live_web` fallback) replacing static regex prompt routing ([agent.py](file:///x:/Opportunities-Details/backend/agent.py), [rag.py](file:///x:/Opportunities-Details/backend/rag.py)).


---

## 🚀 To Be Implemented

### 1. Architecture & Background ETL
- [ ] **Decoupled Ingestion Pipeline**: Move `scraper.py` execution out of Streamlit UI initialization into a scheduled background worker (Cron / Celery / APScheduler / GitHub Actions).
- [x] **Persistent Structured Database**: Replace raw [scraped_data.txt](file:///x:/Opportunities-Details/scraped_data.txt) flat-file output with a SQLite database storing structured metadata, opportunity hash, creation date, and application links.
- [x] **Incremental Scraper Run Support**: Track previously scraped content using cryptographic content hashes (SHA-256) to avoid duplicate scraping and re-embedding.

### 2. RAG Index & Persistence Optimizations
- [x] **Vector Database Persistence**: Save and reload the FAISS index to/from disk (`faiss_store/`) or migrate to a managed vector store (Qdrant / Chroma / pgvector).
- [x] **Dynamic Document Chunking Strategy**: Implement smart parent-child document chunking (500-char child chunks mapped to parent docstore) for long opportunity posts.
- [ ] **Citation & Source Link Formatting**: Parse and return direct clickable application URLs in the final LLM response.

### 3. REST API & Backend Decoupling
- [x] **FastAPI Backend Service**: Expose clean REST endpoints (`/api/v1/chat`, `/api/v1/opportunities`, `/api/v1/health`, `/api/v1/scrape`) to decouple backend RAG logic from Streamlit.
- [ ] **API Authentication & Rate Limiting**: Add API key validation or OAuth2 JWT security for backend endpoints.

### 4. Infrastructure, Deployment & Configuration
- [x] **Environment Variable File (`.env`) Support**: Load configuration dynamically from `.env` instead of hardcoded Windows model paths.
- [x] **Production Dockerfile**: Update [.dockerfile](file:///x:/Opportunities-Details/.dockerfile) to standard `Dockerfile` with multi-stage builds, non-root user execution, `EXPOSE 8501`, and `HEALTHCHECK`.
- [x] **Docker Compose Orchestration**: Add a `docker-compose.yml` to launch the Streamlit frontend, FastAPI backend, and Ollama server together.

### 5. Quality, Observability & Security
- [ ] **Structured Logging**: Replace debugging print calls (`icecream`) with standard structured JSON logging (`logging` / `loguru`).
- [ ] **Developer Mode UI Switch**: Hide prompt inspection expanders and retrieved document title expanders behind a developer debug toggle in Streamlit.
- [ ] **RAG Tracing & Evaluation**: Integrate LangSmith, Phoenix, OpenTelemetry, or Ragas/TruLens to track response latency, context precision/recall, and hallucination rates.
- [ ] **Automated Testing Suite**: Create unit and integration tests (`pytest`, `pytest-asyncio`, `respx`, `httpx`) to test site scrapers, RAG retrievers, and FastAPI backend endpoints.

### 7. Agentic AI & Autonomous Workflows
- [x] **LangGraph / Tool-Calling Agent Framework**: Replace regex-based prompt routing with Level 1 Fast Router (0.01ms) and 2-tool LangGraph Agent (`search_local_opportunities` RAG pipeline + `search_live_web` fallback).
- [ ] **On-Device Micro-LLM Tool Router (`cactus-compute/needle`)**: Integrate the 26M parameter ultra-lightweight `cactus-compute/needle` model (~14MB footprint) for local function calling, multi-tool selection, and natural language query parameter extraction (e.g., parsing country, degree level, and deadline into structured JSON filters) with ~10ms execution latency.
- [ ] **Corrective RAG (CRAG) & Self-Reflection**: Implement a grade-and-correct loop where an evaluator node verifies retrieved document quality and falls back to live web search if local context is insufficient, followed by a hallucination-checking node.
- [ ] **Multi-Agent Orchestration**: Divide complex requests into specialized sub-agents:
  - *Scholarship Matcher Agent*: Discovers opportunities across local and web sources.
  - *Eligibility Evaluator Agent*: Compares user GPA, nationality, and degree target against scholarship criteria.
  - *Application Roadmap Agent*: Drafts custom document checklists, SOP outlines, and submission timelines.
- [ ] **Autonomous Web Scraping Agent**: Deploy goal-driven browser agents (e.g. via Crawl4AI / Browser Use) capable of navigating complex portal pagination, solving dynamic rendering, and extracting structured schemas directly into the vector database.

### 9. Advanced Agentic Tooling & Autonomous AI Features
- [ ] **Autonomous Document & Motivation Letter Generator**: A tool-equipped agent (`draft_motivation_letter`) that takes user bio/resume + opportunity details, performs a requirement-gap analysis, and drafts tailored Statements of Purpose (SOP) or Letters of Motivation (LOM).
- [ ] **Self-Healing & Adaptive Scraper Agent**: When HTML layout changes cause scraper failures, a diagnostic agent fetches the page DOM via Playwright, uses an LLM to generate updated CSS/XPath selectors, tests them in a sandbox, and auto-patches the scraper code.
- [ ] **Multi-Source Fact Verifier & Scam Detector Tool**: `verify_opportunity_legitimacy(url)` tool that cross-checks aggregator postings against official `.edu` / `.org` domains to detect fake/expired scholarships or misquoted stipend amounts.
- [ ] **Interactive Mock Interview Simulator Agent**: An agent that roleplays as a scholarship selection panel (e.g. Fulbright, DAAD, Chevening), conducts multi-turn practice interviews, scores answers against selection rubrics, and provides feedback.
- [ ] **Automated Application Form Prefiller Agent**: A browser-use agent that takes verified user profile data, navigates to the official portal, pre-fills non-sensitive fields (Education, GPA, Contact), and leaves the final submission for user review.
- [ ] **Financial & Stipend Gap Calculator Tool**: A tool (`calculate_financial_gap`) that compares scholarship funding vs destination city cost-of-living metrics (housing, health insurance, visa costs) to calculate net out-of-pocket expenses.
- [ ] **Query Decomposition & Parallel Multi-Hop Search**: Breaks complex multi-constraint queries (e.g., *"No GRE required PhDs in Germany or Switzerland with Q4 deadlines"*) into parallel sub-searches and aggregates results into a comparison matrix.



