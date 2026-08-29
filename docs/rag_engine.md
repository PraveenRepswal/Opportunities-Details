# `backend/rag.py` — RAG Engine

> **File:** `backend/rag.py` (694 lines)
> **Purpose:** Core Retrieval-Augmented Generation pipeline that owns the FAISS vector store, BM25 retriever, CrossEncoder reranker, parent–child document store, semantic answer cache, prompt templates, and Server-Sent-Events (SSE) token streaming.

---

## 1. Purpose & Overview

`rag.py` is the **heart of the assistant**. It is the only module that:

1. Loads scraped opportunity documents from SQLite / disk / live scraper.
2. Chunks them into **parent / child** pairs and indexes the children in a **persistent FAISS vector store** (`faiss_store/`).
3. Builds a parallel **BM25 lexical index** over the same children.
4. Loads a **HuggingFace CrossEncoder** reranker to refine candidate sets.
5. Combines dense + sparse retrieval via a weighted **EnsembleRetriever** (hybrid search).
6. Resolves retrieved children back to their full parent documents (so the LLM sees complete postings, not fragments).
7. Selects an LLM provider (Ollama, an external llama.cpp server, or in-process llama.cpp).
8. Streams answer tokens back to the HTTP layer using a custom `[[METADATA]]…\n` framing convention so the client receives a one-shot JSON header before the body chunks.
9. Maintains a **semantic answer cache** so near-duplicate single-turn questions skip retrieval and generation entirely.

It is the only module that ever talks to the vector store, the reranker, or the answer cache. Everything else (`backend/agent.py`, `backend/main.py`, the Streamlit UI) interacts with retrieval and generation through `RAGPipeline`.

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          RAG PIPELINE                                       │
│                                                                             │
│  fetch_opportunity_documents()                                              │
│      │                                                                      │
│      ▼                                                                      │
│  List[Document] (parents) ──► _build_parent_docstore()  [parent_id → Doc]   │
│      │                                                                      │
│      ▼                                                                      │
│  create_child_chunks()  ──► List[Document] (children, 500/80 overlap)       │
│      │                                                                      │
│      ├─► HuggingFaceEmbeddings("intfloat/e5-small-v2")                      │
│      │       │                                                              │
│      │       └─► FAISS.from_documents(...) + save_local("faiss_store/")     │
│      │                                                                      │
│      └─► BM25Retriever.from_documents(...)                                  │
│                                                                             │
│  CrossEncoder("cross-encoder/ms-marco-MiniLM-L12-v2")                       │
│                                                                             │
│  SemanticAnswerCache(db_path, threshold, ttl, max_entries)                  │
└─────────────────────────────────────────────────────────────────────────────┘

Stream-time flow per request:
─────────────────────────────
  prompt + history + provider + flags
      │
      ▼
  (1) config_hash = sha1(provider|think|rerank|ollama_url|llama_url)
      │
      ▼
  (2) answer_cache.lookup(prompt, query_vec, config_hash)?
      │ HIT  → yield [[METADATA]]{cache_hit:true,...}\n + answer chunks → return
      │
      │ MISS
      ▼
  (3) fast_router(prompt) ──► RouteTarget.AGENT_TOOLS or DIRECT_CHAT
      │
      ▼
  (4) AGENT_TOOLS? ─► OpportunityAgentGraph.run_agent_workflow(...)
      │                 ├─ bind_tools({search_local, search_live_web})
      │                 ├─ tool_calls[0]? execute selected tool
      │                 └─ return {context_text, used_tools, retrieved_docs}
      │
      ▼
  (5) pick system_template_for_opportunities vs system_template_without_opportunities
      │
      ▼
  (6) build ChatPromptTemplate + LCEL chain:  prompt | llm | StrOutputParser
      │
      ▼
  (7) yield [[METADATA]]meta_info\n     ← headers (is_opportunity, used_tools, doc_names, debug_info)
      │
      ▼
  (8) for chunk in chain.stream(input_data):   yield chunk_str
      │
      ▼
  (9) answer_cache.store(prompt, query_vec, config_hash, joined_text, meta_info)
```

### Module-level layout

| Lines         | Section                                                       |
| ------------- | ------------------------------------------------------------- |
| `1–32`        | Imports, Windows DLL workaround, torch device probe            |
| `34–76`       | Graceful LangChain / sentence-transformers import fallbacks    |
| `77–86`       | App-level constants pulled from `config.settings`             |
| `89–181`      | `fetch_opportunity_documents()` — DB / file / live fallback    |
| `184–192`     | `rerank_docs()` — CrossEncoder rerank helper                  |
| `195–209`     | `format_context_snippets()` — de-duplicate + chunk context    |
| `212–248`     | `compress_history_for_opportunities()` — prefill token saver   |
| `251–257`     | `strip_thinking_tags()` — drop `<think>…</think>` blocks      |
| `260`         | `FAISS_STORE_DIR = Path("faiss_store")`                       |
| `263–280`     | `create_child_chunks()` — fixed-window splitter               |
| `283–694`     | `class RAGPipeline` — the orchestrator                        |

---

## 3. Key Classes & Functions

### 3.1 Module-level helpers

#### `fetch_opportunity_documents() -> List[Document]` — `backend/rag.py:89`

Three-tier loader. Returns a flat list of `langchain_core.documents.Document` objects, each with `page_content` (normalized via `unicodedata.normalize("NFKC", …)`) and metadata `{name, id, url?}`.

| Tier | Source | Behavior |
| ---- | ------ | -------- |
| 1    | SQLite `opportunities` table (`backend.database.list_opportunities(limit=1000)`) | If any rows are returned, build docs and **short-circuit** — this is the fast path on a warm cache. |
| 2    | `scraped_data.txt` (a JSON list dumped by the scraper) | Loads, **upserts into SQLite for next boot** (best-effort), and returns docs. |
| 3    | `CombinedScraper(days_back=DAYS_BACK, threshold=SCORE_THRESHOLD).run_all_scrapers() + await_enrichment()` | Live scrape, executed synchronously via `asyncio.run`. |
| Fallback | Synthetic doc | If every tier returned nothing, appends a "fallback" document so the LLM at least has *something* to answer casual questions with. |

Titles in a small ignore-set (`"about us"`, `"our services"`, `"contact us"`, `"privacy policy"`, `"terms of service"`, `"our team"`) are dropped — these are typically site chrome picked up by sitemap scrapes.

**Parameters / returns:**

| Parameter | Type | Description |
| --------- | ---- | ----------- |
| *(none)*  | —    | —           |
| **Returns** | `List[Document]` | Each `Document.page_content` is the opportunity's body text; `metadata["name"]` is the title, `metadata["id"]` is the DB row id or a fresh UUID, and `metadata["url"]` is set when loaded from SQLite. |

#### `rerank_docs(query, docs, reranker, top_k=5) -> List[Document]` — `backend/rag.py:184`

Pure-Python wrapper around `sentence_transformers.CrossEncoder.predict`.

```python
pairs = [(query, doc.page_content) for doc in docs]
scores = reranker.predict(pairs)
return [doc for doc, _ in sorted(zip(docs, scores), key=lambda x: x[1], reverse=True)[:top_k]]
```

| Parameter | Type | Description |
| --------- | ---- | ----------- |
| `query`   | `str` | The user query string. |
| `docs`    | `List[Document]` | Candidate documents from the ensemble retriever. |
| `reranker` | `CrossEncoder` | A loaded `cross-encoder/ms-marco-MiniLM-L12-v2`. |
| `top_k`   | `int` (default 5) | Max number of reranked docs to keep. |
| **Returns** | `List[Document]` | Top-`top_k` documents ordered by descending relevance score. |

Returns `[]` when `docs` is empty (defensive guard).

#### `format_context_snippets(docs, max_chars=3500) -> str` — `backend/rag.py:195`

Builds the `Context:` block injected into the human prompt. De-duplicates by title (so the same opportunity isn't listed twice when multiple child chunks are passed in), normalizes whitespace, and truncates each snippet to `max_chars`. Output format:

```
Title: <name>
Content: <first max_chars chars, whitespace-collapsed>

Title: <name>
Content: …
```

Used by both `RAGPipeline.stream_response` (via agent) and `OpportunityAgentGraph` (`backend/agent.py:223`).

#### `compress_history_for_opportunities(history, max_items=6) -> str` — `backend/rag.py:212`

Compresses prior assistant turns into a one-line summary to reduce prompt size while preserving multi-turn memory.

Algorithm:
1. Keep only the last `max_items` messages.
2. For each assistant message, prefer the structured `metadata.opportunity_cards[].title` list if present.
3. Otherwise, heuristically extract titles from lines that look like markdown headings (`# …`, `1. …`, `**…**`), excluding lines containing `deadline` / `location`.
4. Emit `Assistant: Provided details for [Title A, Title B, …]`.
5. If nothing extracted, fall back to the first 120 chars of the message.

User messages are emitted verbatim as `User: …`. This routine saves roughly 2,000 prefill tokens per turn in long conversations.

#### `strip_thinking_tags(text) -> str` — `backend/rag.py:251`

Used by the API layer (`backend/main.py:190`, `backend/main.py:268`) to scrub `<think>…</think>` blocks produced by reasoning-mode LLMs (qwen3.5 etc.). Also collapses runs of ≥3 newlines down to 2.

| Input | Output |
| ----- | ------ |
| `"<think>reasoning</think>\n\nfinal answer"` | `"final answer"` |
| `"preamble\n</think>\n\nbody"` | `"body"` (keeps anything after the tag when no opening tag exists) |
| `"with\n\n\n\nmultiple\n\nblanks"` | `"with\n\nmultiple\n\nblanks"` |

#### `FAISS_STORE_DIR` — `backend/rag.py:260`

`Path("faiss_store")`. The on-disk location for the saved FAISS index. Created lazily by `initialize()` and `reload_documents()`.

#### `create_child_chunks(docs, chunk_size=500, chunk_overlap=80) -> List[Document]` — `backend/rag.py:263`

Splits each parent document into overlapping fixed-size child chunks using a simple sliding window (`step = chunk_size - chunk_overlap = 420`). Each child inherits the parent's metadata and adds a `parent_id` field that points back to the parent's `id`.

> Note: This is a character-level chunker (not token-aware). With the default `intfloat/e5-small-v2` embedding model that's acceptable — the model has a 512-token context window and most scholarship postings fit comfortably.

---

### 3.2 `class RAGPipeline` — `backend/rag.py:283`

The orchestrator class. A single instance is created in `backend/main.py:45` (`rag_pipeline = RAGPipeline()`) and shared across requests.

#### 3.2.1 Constructor — `backend/rag.py:286`

```python
def __init__(self):
    self.docs: List[Document] = []
    self.parent_docstore: Dict[str, Document] = {}
    self.vectorstore: Optional[FAISS] = None
    self.bm25_retriever: Optional[BM25Retriever] = None
    self.reranker: Optional[CrossEncoder] = None
    self.embeddings: Optional[HuggingFaceEmbeddings] = None
    self.is_initialized: bool = False
    from backend.database import DB_PATH
    self.answer_cache = SemanticAnswerCache(
        db_path=DB_PATH,
        threshold=settings.model.semantic_cache_similarity_threshold,
        ttl_hours=settings.model.semantic_cache_ttl_hours,
        max_entries=settings.model.semantic_cache_max_entries,
    )
```

The `answer_cache` lives in the **same SQLite file** as chat sessions and opportunities (`opportunities_chat.db`), so a single `opportunities_chat.db` file contains everything except the FAISS index.

#### 3.2.2 `_build_parent_docstore()` — `backend/rag.py:304`

Builds an in-memory `Dict[parent_id -> Document]` so retrieved children can be resolved back to their full parent for context generation. Called by both `initialize()` and `reload_documents()`.

#### 3.2.3 `resolve_parent_docs(child_docs) -> List[Document]` — `backend/rag.py:312`

Reverse-lookup. For each child, finds the corresponding parent in `parent_docstore`. De-duplicates by `parent_id` (so multiple matched chunks from the same parent only contribute once). Falls back to the child itself if no parent id is found (e.g., the synthetic "fallback" doc).

#### 3.2.4 `initialize()` — `backend/rag.py:329`

Warm-up. Triggered automatically by `backend/main.py:61` (`lifespan` startup hook) and lazily on the first `stream_response` call when `is_initialized is False`.

Sequence:
1. Print device (`cuda` / `cpu`) and call `torch.cuda.empty_cache()` when applicable.
2. `fetch_opportunity_documents()` — load parents.
3. `_build_parent_docstore()`.
4. `create_child_chunks(self.docs, 500, 80)` — chunk.
5. **Embeddings + FAISS**:
   - Load `HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL, model_kwargs={"device": DEVICE}, encode_kwargs={"normalize_embeddings": True})`.
   - If `faiss_store/index.faiss` exists → `FAISS.load_local(...)` (with `allow_dangerous_deserialization=True`).
   - Otherwise → `FAISS.from_documents(child_docs, embeddings, distance_strategy=DistanceStrategy.COSINE)` and `save_local("faiss_store/")`.
6. **BM25**: `BM25Retriever.from_documents(child_docs or self.docs)` with `k = 3`.
7. **Reranker**: `CrossEncoder("cross-encoder/ms-marco-MiniLM-L12-v2")` (best-effort).
8. `self.is_initialized = True`.

> **Why `allow_dangerous_deserialization=True`?** `FAISS.load_local` pickles the docstore. Since both sides of the pickle are this codebase, this is safe — but the flag is mandatory to acknowledge the risk explicitly.

#### 3.2.5 `reload_documents()` — `backend/rag.py:388`

Invoked by `backend/main.py:117` (`rag_pipeline.reload_documents()`) after a successful `/api/v1/scrape` job when `reindex=True`. Steps:

1. **`self.answer_cache.bump_epoch()`** — bumps the monotonic epoch so all previously cached answers become orphaned. Without this, an answer generated against the old corpus could be served after the corpus changes.
2. Re-fetches documents, re-chunks, and rebuilds the FAISS index in place.
3. **Does not persist** to disk only when FAISS isn't already initialized (unlike `initialize`, the index was already loaded before this call, so we just overwrite it).

#### 3.2.6 `get_llm(provider, think_mode, ollama_base_url=None, llamacpp_server_url=None)` — `backend/rag.py:417`

Factory that returns a LangChain chat model bound to the chosen backend. Three branches:

| Provider | Returns |
| -------- | ------- |
| `"Ollama"` | `ChatOllama(model="qwen3.5:4b", temperature=0.0, reasoning=think_mode, base_url=ollama_url)` |
| Anything else (e.g. `"llama.cpp"`) with `llama_url/health` or `llama_url/` returning 200/204/404/405 within a 1-second timeout | `ChatOpenAI(base_url=f"{llama_url}/v1", api_key="not-needed", model="qwen3.5:4b", temperature=0.0, streaming=True)` — speaks the OpenAI-compatible protocol against an external llama-server. |
| Otherwise (fallback) | `ChatLlamaCpp(model_path=MODEL_PATH, temperature=0.0, n_gpu_layers=-1, n_ctx=8192, verbose=False)` — in-process GGUF inference. `n_gpu_layers=-1` offloads every layer to GPU when available. |

> **Model identity is hard-coded to `qwen3.5:4b` everywhere.** The embedding model is configurable (`settings.model.embedding_model`), but the chat model is not — see `backend/agent.py:430` and `backend/rag.py:460`.

#### 3.2.7 `stream_response(...)` — `backend/rag.py:476`

The big one. Async generator that yields SSE-formatted chunks to the FastAPI `StreamingResponse` (`backend/main.py:282`).

```python
async def stream_response(
    self,
    prompt: str,
    history: List[Dict[str, Any]],
    provider: str = "Ollama",
    think: bool = False,
    rerank: bool = True,
    debug: bool = False,
    ollama_base_url: Optional[str] = None,
    llamacpp_server_url: Optional[str] = None,
) -> AsyncGenerator[str, None]:
```

Step-by-step:

1. **Lazy init** (`backend/rag.py:488–489`): if `not self.is_initialized`, call `initialize()`.

2. **Build config_hash** (`backend/rag.py:494–504`): SHA1 of `"provider|think|rerank|ollama_url|llama_url"`. Used to **scope** cache entries — switching from Ollama to llama.cpp with the same prompt should NOT reuse the cached answer.

3. **Cacheability check** (`backend/rag.py:506–511`): `cacheable = semantic_cache_enabled AND history is empty AND not debug AND embeddings ready`.
   - Single-turn only — multi-turn answers depend on conversation context that the cache can't see.
   - Debug requests always bypass the cache so the live pipeline stays inspectable.

4. **Cache lookup** (`backend/rag.py:513–530`): embed the prompt with `self.embeddings.embed_query(prompt)`, then `self.answer_cache.lookup(...)`. On HIT, yield `[[METADATA]]{"cache_hit": true, …}\n` followed by the cached answer split into 32-char chunks, then `return`.

5. **Build ensemble retriever** (`backend/rag.py:532–544`): if both FAISS and BM25 are present, build:
   ```python
   dense = vectorstore.as_retriever(
       search_type="mmr",
       search_kwargs={"k": 7, "fetch_k": 15, "lambda_mult": 0.5},
   )
   ensemble = EnsembleRetriever(
       retrievers=[bm25_retriever, dense],
       weights=[0.4, 0.6],   # dense wins 60% / 40%
   )
   ```
   - **MMR** (Maximal Marginal Relevance) reduces redundancy in dense results by penalizing candidates that are too similar to ones already selected.
   - **Weights `[0.4, 0.6]`** mean lexical matches count 40% and semantic matches 60% — tuned empirically for opportunity search, where exact terms like "DAAD" or "Chevening" carry signal.

6. **Fast Python router** (`backend/rag.py:547–550`): calls `backend.agent.fast_router(prompt)` to get a `RouteTarget` in ~10 µs. Sets `is_opportunity_prompt = (route == AGENT_TOOLS)`. See `docs/agent_router.md` for routing rules.

7. **Agent workflow** (`backend/rag.py:556–570`): if opportunity-prompt, instantiate `OpportunityAgentGraph(self).run_agent_workflow(...)` which may invoke one of two native tools:
   - `search_local_opportunities_tool` → hybrid retrieval + rerank (this is where the ensemble is used).
   - `search_live_web_tool` → DuckDuckGo HTML scrape (`backend/agent.py:66`).
   The agent returns `{context_text, used_tools, retrieved_docs, has_tool_call}`. `is_opportunity_prompt` is **re-checked** — we only stay in opportunity-mode if either a tool was called OR `cleaned_info.strip()` is non-empty. This prevents the LLM from rendering an empty context block.

8. **Prompt selection** (`backend/rag.py:572–615`):

   | Mode | System template | Human template |
   | ---- | --------------- | -------------- |
   | Opportunity | `system_template_for_opportunities` (lines 572–584) — instructs "Anna" to format each opportunity with title heading, description, and a metadata line `• Deadline: … \| Organization: … \| Location: … \| Type: …` at the bottom. | `human_template_for_opportunities` — injects `Context:` block + `Conversation history:` + `Question:` |
   | Direct chat | `system_template_without_opportunities` (lines 586–592) — answers general questions, **declines** opportunity queries that have no context. | `human_template_without_opportunities` — no context block |

   The chat prompt is `ChatPromptTemplate.from_messages([system_msg, human_msg])`.

9. **History injection** (`backend/rag.py:617–625`):
   - If opportunity prompt → `compress_history_for_opportunities(history)`.
   - Else → last 6 messages rendered as `role: content` lines (no summarization).

10. **Build LCEL chain** (`backend/rag.py:635–641`):
    ```python
    llm = self.get_llm(provider=provider, think_mode=think, …)
    chain = chat_prompt | llm | StrOutputParser()
    ```
    `StrOutputParser` collapses `AIMessageChunk` objects to plain strings.

11. **Yield metadata header** (`backend/rag.py:657–681`): `meta_info` contains `is_opportunity`, `used_tools`, `initial_docs` (titles of retrieved docs), and (when `debug`) the rendered system/human prompts, the filled variables, and a 1500-char snippet of the retrieved context.

    ```python
    yield f"[[METADATA]]{json.dumps(meta_info)}\n"
    ```

    The API layer strips this prefix and exposes it as `metadata_dict` in the response. The Streamlit UI uses it to render opportunity cards, tool badges, and a "cache hit" badge.

12. **Stream token chunks** (`backend/rag.py:683–687`):
    ```python
    for chunk in chain.stream(input_data):
        chunk_str = chunk if isinstance(chunk, str) else getattr(chunk, "content", str(chunk))
        generated_chunks.append(chunk_str)
        yield chunk_str
    ```

13. **Cache write** (`backend/rag.py:689–694`): if the response was cacheable (no history, debug off, embeddings available), store `joined = "".join(generated_chunks)` along with `meta_info` under the current `config_hash` and `epoch`. Errors here are logged but never raised — caching must never break a successful response.

#### 3.2.8 `ensemble_retriever` — attribute assigned at `backend/rag.py:544`

Stored on the instance so `OpportunityAgentGraph.search_local_opportunities` (`backend/agent.py:125`) can reuse it without rebuilding.

---

## 4. Flow / Lifecycle

End-to-end lifecycle of an incoming `/api/v1/chat/stream` request:

```
 Streamlit / curl
      │ POST /api/v1/chat/stream  {prompt, history, provider, think, rerank, debug}
      ▼
 backend/main.py:230  chat_stream(request)
      │ 1. Persist user message → SQLite
      │ 2. Convert Pydantic history → list of dicts
      │ 3. rag_pipeline.stream_response(...)
      ▼
 backend/rag.py:476  stream_response
      │
      ├─► lazy initialize() if cold start
      │
      ├─► SemanticAnswerCache.lookup(prompt, query_vec, config_hash)
      │     └─► HIT  → yield [[METADATA]] + cached chunks → return
      │
      ├─► Build FAISS-MMR + BM25 EnsembleRetriever(weights=[0.4, 0.6])
      │
      ├─► fast_router(prompt)  (10 µs regex classifier)
      │     └─► DIRECT_CHAT?  → skip agent, skip opportunity template
      │     └─► AGENT_TOOLS    → run_agent_workflow()
      │           ├─► llm.bind_tools([search_local, search_live_web])
      │           ├─► tool_calls?  → execute → context_text
      │           └─► no tool call → keyword fallback (line 207)
      │
      ├─► Pick prompt template by is_opportunity_prompt
      │
      ├─► yield [[METADATA]]meta_info\n        ← headers to client
      │
      ├─► chain.stream(input_data) → yield each token chunk
      │
      └─► SemanticAnswerCache.store(prompt, query_vec, config_hash, joined_text, meta_info)
            ↑ only when cacheable (single-turn, debug=False)

 API layer (database_persisting_generator in backend/main.py:250)
      ├─ collects full_response + metadata_dict
      ├─ extract_cards_from_response(full_response) → adds opportunity_cards to metadata
      ├─ persists assistant message → SQLite
      └─ Streams chunks to the client verbatim
```

---

## 5. Dependencies

| Import | Used For | Why |
| ------ | -------- | --- |
| `asyncio`, `sys`, `os`, `warnings` | Windows DLL workaround for torch, runtime portability | Lines 17–32 |
| `datetime`, `hashlib`, `json`, `re`, `unicodedata`, `uuid` | Date injection, cache hashing, JSON metadata, regex history compression, NFKC text cleanup, id generation | Lines 2–12 |
| `pathlib.Path` | `faiss_store`, `scraped_data.txt` | Lines 117, 260 |
| `typing.{AsyncGenerator, Dict, List, Optional, Any}` | Static typing | Throughout |
| `torch` | CUDA detection (`DEVICE = "cuda" if torch.cuda.is_available() else "cpu"`) and `torch.cuda.empty_cache()` | Lines 27–33, 333 |
| `langchain_community.retrievers.BM25Retriever` | Sparse lexical retriever | Line 35 |
| `langchain_community.vectorstores.FAISS` + `DistanceStrategy` | Dense semantic retriever | Lines 36–37 |
| `langchain_core.documents.Document` | Document type | Line 38 |
| `langchain_core.output_parsers.StrOutputParser` | Collapse `AIMessageChunk` → `str` | Line 39 |
| `langchain_core.prompts.{ChatPromptTemplate, HumanMessagePromptTemplate, SystemMessagePromptTemplate}` | LCEL prompt construction | Line 40 |
| `langchain_classic.retrievers.EnsembleRetriever` | Weighted hybrid (BM25 + FAISS) | Line 41 |
| `langchain_ollama.ChatOllama` | Ollama backend | Line 42 |
| `langchain_community.chat_models.ChatLlamaCpp` | In-process GGUF inference | Line 60 |
| `langchain_huggingface.HuggingFaceEmbeddings` | E5 embeddings | Line 66 |
| `sentence_transformers.CrossEncoder` | Reranker | Line 72 |
| `backend.answer_cache.SemanticAnswerCache` | Semantic answer cache | Line 77 |
| `config.settings` | Config (`embedding_model`, `ollama_base_url`, `llamacpp_server_url`, etc.) | Line 78 |
| `scraper.CombinedScraper` | Live scrape fallback | Line 79 |

Every LangChain / HF / sentence-transformers import is **wrapped in `try/except`**. On failure the symbol becomes `Any` (or a stub `Document` class), so the module still imports cleanly on minimal installs — initialization logs a warning and the relevant feature degrades.

---

## 6. Models & External Services

| Component | Model / Service | Config knob |
| --------- | --------------- | ----------- |
| Embeddings | `intfloat/e5-small-v2` (default) | `settings.model.embedding_model` |
| Vector index | FAISS (cosine distance, COSINE strategy), persisted under `faiss_store/index.faiss` | hard-coded path |
| Sparse index | BM25 (langchain-community), `k = 3` | hard-coded |
| Reranker | `cross-encoder/ms-marco-MiniLM-L12-v2` (sentence-transformers) | hard-coded model id at `backend/rag.py:381` |
| Chat (default) | Ollama `qwen3.5:4b` | `settings.model.ollama_base_url` |
| Chat (external llama.cpp server) | llama-server speaking OpenAI protocol, model `qwen3.5:4b` | `settings.model.llamacpp_server_url` |
| Chat (in-process fallback) | GGUF model at `MODEL_PATH = settings.model.resolved_main_model_path` | `settings.model.main_model` + fallback chain (`X:\HuggingFace\models\…`, `/app/models/…`, `./models/…`) |
| Compute device | `cuda` if available else `cpu` | auto |

---

## 7. Notable Algorithms

### 7.1 Parent–Child retrieval

Each scraped opportunity is stored as a single **parent** Document. At indexing time it's split into ~500-char children with 80-char overlap (`backend/rag.py:263`). Children get a `parent_id` pointing back. At retrieval time we run hybrid search on children, then `resolve_parent_docs` (`backend/rag.py:312`) swaps each child back to its full parent before formatting context. This keeps retrieval **fine-grained** (small enough for the embedding model to score accurately) while giving the LLM **complete postings** (no mid-sentence truncation in the answer).

### 7.2 Hybrid search (BM25 + FAISS-MMR)

`EnsembleRetriever` linearly combines reciprocal ranks. The dense retriever uses **MMR** with `k=7, fetch_k=15, lambda_mult=0.5` — i.e., fetch 15 candidates, then pick 7 that maximize `[relevance − 0.5 × similarity_to_already_selected]`. The BM25 retriever uses `k=3`. The weighted ensemble sums the reciprocal ranks with weights `[0.4, 0.6]`, so dense matches dominate by ~1.5×.

### 7.3 CrossEncoder reranking

After hybrid retrieval produces ~10 candidates, the optional reranker (`cross-encoder/ms-marco-MiniLM-L12-v2`) jointly encodes `(query, doc)` pairs and outputs a calibrated relevance score. Top 5 are kept. This is the standard two-stage retrieve-and-rerank pattern.

### 7.4 Semantic answer cache

See `docs/answer_cache.md`. Embeddings are assumed L2-normalized, so cosine similarity reduces to a plain dot product (the cache does `_dot(a, b)` — no division, no sqrt). Cache writes are batched with epoch bumps so a `/scrape?reindex=true` invalidates all old answers instantly.

### 7.5 History compression

`compress_history_for_opportunities` reduces multi-turn context from full markdown answers to a single line `Assistant: Provided details for [Title A, Title B, …]` per turn. Empirically saves ~2,000 prefill tokens per turn for users with long chat histories.

### 7.6 Thinking-tag scrubbing

`strip_thinking_tags` (`backend/rag.py:251`) drops `<think>…</think>` blocks. The chain streams raw tokens including the reasoning block if `think=True`; the API layer strips them before persistence so the database never stores internal monologue.

---

## 8. Error Handling

| Failure point | Behavior |
| ------------- | -------- |
| `torch` import fails | `DEVICE` falls back to `cpu`; pipeline still runs but reranker / embeddings degrade. (`backend/rag.py:29`) |
| Any LangChain import fails | Symbol becomes `Any` stub so module import never raises. (`backend/rag.py:43`) |
| `HuggingFaceEmbeddings` import fails | `self.embeddings` stays `None` → semantic cache disabled, FAISS build skipped. |
| `CrossEncoder` load fails | `self.reranker` stays `None` → reranking skipped, retrieval still works. |
| `FAISS.load_local` fails | Catches the exception, falls back to rebuilding from `child_docs`. (`backend/rag.py:354`) |
| `fetch_opportunity_documents` tier 1 fails (DB) | Tier 2 (file) attempted. (`backend/rag.py:113`) |
| Tier 2 fails | Tier 3 (live scrape) attempted. (`backend/rag.py:143`) |
| Tier 3 fails | Synthetic fallback doc appended. (`backend/rag.py:171`) |
| Live llama.cpp server unreachable | In-process `ChatLlamaCpp` fallback. (`backend/rag.py:467`) |
| `bind_tools` not supported by LLM | Keyword fallback inside `OpportunityAgentGraph.run_agent_workflow`. (`backend/agent.py:204`) |
| Cache `lookup` raises | Caught in `stream_response`, logged; cache treated as miss. (`backend/rag.py:517`) |
| Cache `store` raises | Caught and logged; never affects response delivery. (`backend/rag.py:693`) |
| `reload_documents` raises | Print + swallow; old vectorstore remains in memory until the next successful reload. (`backend/rag.py:411`) |

The dominant philosophy: **never let the cache or optional models break a successful answer**. Every external dependency is wrapped, every optional feature degrades, and the request returns *something* useful even on partial failure.

---

## 9. Notable Patterns & Design Decisions

1. **Hybrid dense + sparse retrieval with reranking.** Three-stage (BM25+FAISS → ensemble → cross-encoder) is the textbook recipe for RAG; the twist is **MMR inside the dense retriever** to diversify before the ensemble combines them.

2. **Parent–child indexing.** Lets the embedder score fine-grained passages while the LLM sees full opportunities. Avoids the classic "answer was cut off mid-sentence" failure mode.

3. **`config_hash` for cache scoping.** The cache is keyed on `(prompt_embedding, config_hash, epoch)`. The config hash is a SHA1 of `provider|think|rerank|ollama_url|llama_url`, so switching backends never returns a stale answer.

4. **Epoch-based cache invalidation.** Instead of TTL-only eviction, `bump_epoch()` is called after every corpus reload, instantly invalidating every cache entry. The TTL acts as a backstop for stale backends that haven't reloaded.

5. **SSE-with-metadata convention.** `[[METADATA]]{json}\n` is emitted once before the body chunks. Cheap to parse (just look for the literal prefix), survives reverse proxies, and decouples metadata from text body so the client can render UI affordances (tool badges, opportunity cards) before the answer finishes streaming.

6. **Greedy template switch.** Even when `fast_router` says opportunity-mode, the prompt template is downgraded to chat-mode if `cleaned_info.strip()` is empty (`backend/rag.py:595`). This prevents the LLM from being told "answer using only this empty context" and producing nonsense.

7. **Three-tier document loader.** DB → file → live scrape keeps the cold-start path fast (DB hit is microseconds) and the truly-cold path resilient (live scrape always works given network access).

8. **Lazy init + lifespan hook.** `initialize()` is called from FastAPI's `lifespan` (`backend/main.py:61`), but `stream_response` calls it lazily too (`backend/rag.py:488`). Either path works; tests can construct a pipeline and call `stream_response` without spinning up the API server.

9. **Windows DLL shim.** Lines 17–24 add `torch/lib` to `os.add_dll_directory` so PyTorch's CUDA DLLs resolve on Windows. Without this, every import crashes with `OSError: [WinError 126]`.

10. **Pure-stdlib cache.** `SemanticAnswerCache` has zero non-stdlib deps, so it's testable in isolation (`tests/test_answer_cache.py`) without spinning up the full LangChain stack.

11. **History summarization for prefill cost.** A user with 50 turns of long scholarship answers would otherwise saturate the 8K context window. `compress_history_for_opportunities` reduces each assistant message to a one-line title list, leaving room for fresh context.

12. **Reasoning-mode opt-in.** The `think` flag is propagated through to both `ChatOllama(reasoning=think_mode)` and `strip_thinking_tags` post-processing. Users can flip reasoning on/off per request without restarting the server.

---

## Cross-references

- Agent routing: see `docs/agent_router.md`
- Semantic answer cache: see `docs/answer_cache.md`
- HTTP entry points: see `backend/main.py` (FastAPI lifespan, `/api/v1/chat`, `/api/v1/chat/stream`, `/api/v1/scrape?reindex=true`)
- Settings: `config.py` — `settings.model.embedding_model`, `settings.model.ollama_base_url`, `settings.model.llamacpp_server_url`, `settings.model.semantic_cache_*`