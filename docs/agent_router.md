# `backend/agent.py` — LangGraph Query Router & Agent Tools

> **File:** `backend/agent.py` (238 lines)
> **Purpose:** Decide whether a user query should trigger local RAG tools (hybrid retrieval + optional live web search) or be answered as a pure LLM chat. Provides a sub-millisecond regex classifier (`fast_router`) and a LangChain-tool-calling agent (`OpportunityAgentGraph`).

---

## 1. Purpose & Overview

`agent.py` is the **decision layer** between the user prompt and the retrieval pipeline. It has two responsibilities:

1. **Route classification (`fast_router`)** — instantly categorize the prompt as either `DIRECT_CHAT` (small talk, identity questions) or `AGENT_TOOLS` (anything that looks like it might be about scholarships / internships / fellowships / etc.). Runs in ~10 µs as pure Python with no LLM call.

2. **Tool-calling agent (`OpportunityAgentGraph`)** — when `fast_router` says `AGENT_TOOLS`, this class lets the LLM itself decide which retrieval tool to call. It binds two LangChain `@tool`-decorated functions (`search_local_opportunities_tool`, `search_live_web_tool`) to the chat model and parses the resulting `tool_calls` field. If the local search returns nothing, it falls back to the live web search.

The module is **called from exactly one place**: `backend/rag.py:549` (`fast_router`) and `backend/rag.py:557` (`OpportunityAgentGraph(self).run_agent_workflow(...)`).

---

## 2. Architecture

```
            user prompt
                 │
                 ▼
        ┌──────────────────────┐
        │   fast_router()      │  ← 0.01 ms, pure Python
        │   (Layer 1+2+3)      │
        └──────────────────────┘
            │              │
   DIRECT_CHAT          AGENT_TOOLS
   (skip agent)              │
                             ▼
                  ┌──────────────────────────────────┐
                  │  OpportunityAgentGraph           │
                  │  .run_agent_workflow(prompt)     │
                  └──────────────────────────────────┘
                             │
                             ▼
                  1. llm = rag_pipeline.get_llm(...)
                  2. bind_tools([search_local_opportunities_tool,
                  │              search_live_web_tool])
                  3. ai_msg = await llm.ainvoke([{role:user, content:prompt}])
                  4. tool_calls[0]?
                  │
        ┌────────────┴────────────┐
   "search_local"           "search_live_web"
        │                       │
        ▼                       ▼
  EnsembleRetriever       search_live_web()
  (BM25 + FAISS-MMR)        ├─ ddgs.DDGS().text(...)
  + reranker                └─ HTTP POST html.duckduckgo.com (bs4 parse)
        │
        ▼ no hits?
  search_live_web() (fallback)

        ▼
  return {context_text, used_tools, has_tool_call, retrieved_docs}
```

---

## 3. Key Classes & Functions

### 3.1 `class RouteTarget(Enum)` — `backend/agent.py:26`

```python
class RouteTarget(Enum):
    DIRECT_CHAT = "direct_chat"
    AGENT_TOOLS = "agent_tools"
```

Only two outcomes. Any prompt that isn't an *exact* casual greeting is treated as an opportunity query (default-AGENT_TOOLS bias).

### 3.2 `DOMAIN_KEYWORDS` — `backend/agent.py:30`

A frozenset-style `dict` (effectively a set since values are all `1`-ish placeholders; iteration order is preserved in modern Python). Includes obvious terms plus deliberately misspelled variants to catch typos:

```
opportunity, opportunities, opportunites, opurtunity,
scholarship, scholarships, scholorship,
internship, internships, intern, interns,
graduate, graduates, undergraduate, undergraduates,
fellowship, fellowships, fellow, fellows,
grant, grants, funding,
deadline, apply, application,
phd, masters, bachelors, days, link, url,
unsw, labx, daad,            ← institution-specific accelerators
program, programs, programme, programmes,
university, universities, job, jobs,
postdoc, degree, courses, course, residency
```

Note `"unsw"`, `"labx"`, `"daad"` — these high-traffic institution names are pre-loaded as routing triggers so queries like `"daad eligibility"` route correctly without going through every regex.

### 3.3 `CASUAL_GREETINGS` — `backend/agent.py:41`

```
hi, hello, hey, greetings, good morning, good evening,
thanks, thank you, bye, goodbye,
who are you, what is your name, how are you,
how are you doing, what are you saying,
what do you do, what can you do, help
```

These are matched **as exact cleaned-text strings** (after `re.sub(r"[^\w\s]", "", prompt.lower()).strip()`). A casual greeting like `"hi!"` becomes `"hi"` after punctuation stripping and hits DIRECT_CHAT.

### 3.4 `fast_router(prompt: str) -> RouteTarget` — `backend/agent.py:49`

Three-layer classifier. Runs in microseconds because there is no model call.

```python
def fast_router(prompt: str) -> RouteTarget:
    clean_text = re.sub(r"[^\w\s]", "", prompt.lower()).strip()
    words = set(clean_text.split())

    # Layer 1: Domain keyword OR substring override (highest priority)
    if words.intersection(DOMAIN_KEYWORDS) or any(
        k in clean_text for k in ("scholar", "intern", "opportunit", "fellow")
    ):
        return RouteTarget.AGENT_TOOLS

    # Layer 2: Exact casual greeting
    if clean_text in CASUAL_GREETINGS:
        return RouteTarget.DIRECT_CHAT

    # Layer 3: Default safety net → AGENT_TOOLS
    return RouteTarget.AGENT_TOOLS
```

**Layer 1** uses both exact word-set intersection AND substring containment. The substring check is there because common misspellings / fragments won't be in the keyword set:

- `"scholar"` matches `"scholarship"`, `"scholarly"`, `"scholars"`, even `"scholorship"`.
- `"opportunit"` matches both `"opportunities"` (en-GB spelling) and `"opportunites"` (typo).
- `"intern"` catches `"internship"`, `"internships"`, `"internal"`, etc.

**Layer 3 default** is *intentionally* `AGENT_TOOLS`. Better to spend retrieval cycles on a question that's actually chat than to mis-route a legitimate opportunity question to chat mode and have the LLM say *"I don't have information about that"*. The retrieval path is harmless when nothing matches.

### 3.5 `search_live_web(query, max_results=5) -> str` — `backend/agent.py:66`

Real-time web search with two implementations, chosen at module-import time:

```python
try:
    try:
        from ddgs import DDGS          # new package name
    except ImportError:
        from duckduckgo_search import DDGS   # legacy package name
    DDG_AVAILABLE = True
except ImportError:
    DDG_AVAILABLE = False
```

| Order | Path | Notes |
| ----- | ---- | ----- |
| 1 | `DDGS().text(query, max_results=max_results)` | Official `ddgs` (or `duckduckgo_search`) client. Returns a list of dicts with `title`, `body`, `href`. |
| 2 | `httpx.post("https://html.duckduckgo.com/html/", data={"q": query}, headers={…}, timeout=5.0)` + BeautifulSoup parse for `a.result__snippet` | Anonymous HTML scrape; works without any SDK. |
| Both fail | Returns `"No live web results found for the query."` | The agent still receives this string as context, so the LLM can acknowledge the absence. |

The output format is the same in both paths:

```
[1] Title
URL: https://…
Summary: snippet body

[2] Title
URL: …
Summary: …
```

These results are wrapped as:

```
LIVE WEB SEARCH RESULTS:
[1] Title …
```

…and injected into `state.context_text` for the LLM prompt.

### 3.6 `class OpportunityAgentState(BaseModel)` — `backend/agent.py:102`

Pydantic state container used during the agent workflow. Not a true LangGraph `StateGraph` (despite the import probe at line 8) — it's a plain Pydantic model passed through the run.

```python
class OpportunityAgentState(BaseModel):
    prompt: str
    history: List[Dict[str, Any]] = Field(default_factory=list)
    retrieved_docs: List[Any] = Field(default_factory=list)
    context_text: str = ""
    used_tools: List[str] = Field(default_factory=list)
    final_response: str = ""
```

> **Note:** The class is named `OpportunityAgentGraph` even though the run is a sequential Python function, not a LangGraph `StateGraph`. The `langgraph.graph` import at line 8 is a probe (`LANGGRAPH_AVAILABLE`) but `StateGraph`/`END` are never actually used. This is a **deliberate simplification**: native LangChain tool-calling gives the same outcome with less abstraction overhead.

### 3.7 `class OpportunityAgentGraph` — `backend/agent.py:113`

The tool-calling agent. Holds a reference to the parent `RAGPipeline` so it can call into its retrievers and reranker.

```python
class OpportunityAgentGraph:
    def __init__(self, rag_pipeline):
        self.rag_pipeline = rag_pipeline
```

#### 3.7.1 `search_local_opportunities(query: str) -> List[Any]` — `backend/agent.py:119`

Tool #1 implementation. Performs the actual hybrid retrieval + reranking.

Flow:
1. **Reuse** the cached `self.rag_pipeline.ensemble_retriever` if it was set by the previous `stream_response` call (`backend/agent.py:125`).
2. If not, build it on demand (mirroring `backend/rag.py:532`).
3. `matched_chunks = ensemble.invoke(query)` — returns child Documents.
4. `parent_docs = self.rag_pipeline.resolve_parent_docs(matched_chunks)` — swap children back to full parents.
5. If reranker exists → `rerank_docs(query, parent_docs, self.rag_pipeline.reranker, top_k=5)`.
6. Else → `parent_docs[:5]`.

Errors are caught and an empty list is returned (logged as `[Agent Tool] Local RAG retrieval error: …`).

#### 3.7.2 `run_agent_workflow(...) -> Dict[str, Any]` — `backend/agent.py:155`

The main entry point. Async because `llm_with_tools.ainvoke(...)` is async.

```python
async def run_agent_workflow(
    self,
    prompt: str,
    history: Optional[List[Dict[str, Any]]] = None,
    provider: str = "Ollama",
    think: bool = False,
    ollama_base_url: Optional[str] = None,
    llamacpp_server_url: Optional[str] = None,
) -> Dict[str, Any]:
```

Step-by-step:

1. **Instantiate LLM** via `self.rag_pipeline.get_llm(...)` so the same provider/think/URL knobs are honored (`backend/agent.py:168`).

2. **Define tool wrappers** (lines 176–186):

   ```python
   @tool
   def search_local_opportunities_tool(query: str) -> str:
       """PRIMARY DEFAULT TOOL: Search local database containing all scraped recent scholarships, fellowships, internships, grants and similar."""
       return query

   @tool
   def search_live_web_tool(query: str) -> str:
       """FALLBACK TOOL: Search the live web ONLY if local database search yields no results or if live web search is explicitly requested."""
       return query
   ```

   The tool **docstrings** are critical — they're what the LLM reads to decide which tool to call. The `return query` body is a placeholder; the **actual** execution happens below in step 4.

3. **Bind & invoke** (`backend/agent.py:193–203`):

   ```python
   llm_with_tools = llm.bind_tools(tools)
   ai_msg = await llm_with_tools.ainvoke([{"role": "user", "content": prompt}])
   tool_calls = getattr(ai_msg, "tool_calls", []) or []
   if tool_calls:
       has_tool_call = True
       first_call = tool_calls[0]
       selected_tool_name = first_call.get("name", "")
       args = first_call.get("args", {})
       tool_query = args.get("query", prompt)
   ```

   The LLM decides whether to call a tool, which one, and with what argument. If the LLM doesn't emit `tool_calls` (or `bind_tools` itself fails) we fall through to the keyword fallback.

4. **Keyword fallback** (`backend/agent.py:204–211`): if `bind_tools` raises (some llama.cpp builds don't support tool calling), or if the model just doesn't emit a tool call, check the prompt for substrings and force `search_local_opportunities_tool`.

   ```python
   if any(k in clean_text for k in ("scholar", "intern", "opportunit", "fellow",
                                     "grant", "funding", "phd", "masters")):
       has_tool_call = True
       selected_tool_name = "search_local_opportunities_tool"
   ```

5. **Execute the selected tool** (`backend/agent.py:213–228`):

   ```python
   if has_tool_call:
       if "web" in selected_tool_name:
           web_res = search_live_web(tool_query)
           state.used_tools.append("search_live_web")
           state.context_text = f"LIVE WEB SEARCH RESULTS:\n{web_res}"
       else:
           local_docs = self.search_local_opportunities(tool_query)
           state.used_tools.append("search_local_opportunities")
           if local_docs:
               state.retrieved_docs = local_docs
               from backend.rag import format_context_snippets
               state.context_text = format_context_snippets(local_docs, max_chars=3500)
           else:
               web_res = search_live_web(tool_query)
               state.used_tools.append("search_live_web")
               state.context_text = f"LIVE WEB SEARCH RESULTS:\n{web_res}"
   ```

   - If the LLM picked `search_live_web_tool` → run `search_live_web(tool_query)`.
   - If the LLM picked `search_local_opportunities_tool` → run hybrid retrieval. If empty, **gracefully degrade** to web search — never leave the agent with empty context.
   - Selection is done by `"web" in selected_tool_name` substring check (works for both `search_web_tool` and `search_live_web_tool`).

6. **Return shape** (`backend/agent.py:230–238`):

   ```python
   return {
       "context_text": state.context_text,
       "used_tools": state.used_tools,
       "has_tool_call": has_tool_call,
       "retrieved_docs": [(d.metadata.get("name") or d.metadata.get("title") or "Opportunity")
                          for d in state.retrieved_docs]
                       if state.retrieved_docs else [],
   }
   ```

   The caller (`backend/rag.py:566–569`) uses these to build the SSE metadata header and select the prompt template.

---

## 4. Flow / Lifecycle

A single request that hits the agent path travels through these stages:

```
1. user POST /api/v1/chat/stream  {prompt: "PhD in Germany with funding"}
2. backend/main.py:230 chat_stream
3. rag_pipeline.stream_response(prompt=…, history=…)
4. fast_router(prompt)
      │  "phd" + "germany" + "funding" → AGENT_TOOLS
      ▼
5. OpportunityAgentGraph(self).run_agent_workflow(…)
      │
      ├─► self.rag_pipeline.get_llm(provider, think, …)
      │
      ├─► @tool defs registered
      │
      ├─► llm.bind_tools([search_local_opportunities_tool, search_live_web_tool])
      ├─► ai_msg = await llm.ainvoke(...)
      │
      │    LLM chooses (typically): search_local_opportunities_tool(query="PhD Germany funding")
      │
      ├─► self.search_local_opportunities("PhD Germany funding")
      │     ├─► EnsembleRetriever (BM25 + FAISS-MMR)
      │     ├─► resolve_parent_docs(child_chunks) → parent_documents
      │     └─► rerank_docs(query, parent_documents, reranker, top_k=5)
      │
      ├─► format_context_snippets(local_docs, max_chars=3500) → context_text
      │
      └─► return {context_text, used_tools=["search_local_opportunities"],
                  has_tool_call=True, retrieved_docs=["DAAD …", "Heinrich …"]}
6. Back in stream_response:
      ├─► system_template_for_opportunities + human_template_for_opportunities
      ├─► yield [[METADATA]]{"used_tools": ["search_local_opportunities"], "initial_docs": […]}
      └─► stream LLM tokens
```

---

## 5. Dependencies

| Import | Used for | Why |
| ------ | -------- | --- |
| `re` | Punctuation stripping in `fast_router` | Line 1 |
| `enum.Enum` | `RouteTarget` | Line 2 |
| `typing.{List, Dict, Any, Optional}` | Static typing | Line 3 |
| `pydantic.{BaseModel, Field}` | `OpportunityAgentState` | Line 4 |
| `langgraph.graph.{StateGraph, END}` (optional) | Probes for `LANGGRAPH_AVAILABLE`; not actively used. The presence check doubles as a runtime capability flag. | Line 8 |
| `ddgs.DDGS` *or* `duckduckgo_search.DDGS` | Live web search SDK | Line 17–19 |
| `httpx` | HTTP fallback for live web search (`html.duckduckgo.com`) | Line 24 |
| `langchain_core.tools.tool` | `@tool` decorator for native tool-calling | Line 111 |

**Import-time fallbacks.** Both LangGraph and DDGS are wrapped in `try/except`. If either is missing, `LANGGRAPH_AVAILABLE` / `DDG_AVAILABLE` flips to `False` and the corresponding feature is bypassed (the agent falls back to keyword routing or HTTP web search respectively).

---

## 6. Models & External Services

| Component | Detail |
| --------- | ------ |
| Chat model | Inherited from `RAGPipeline.get_llm(...)` — typically Ollama `qwen3.5:4b` with `reasoning=think_mode`. The same LLM is used for both the tool-selection step and the final generation step. |
| Tool selection protocol | Native LangChain `bind_tools` (Qwen-style tool-calling format). |
| DuckDuckGo SDK | `ddgs` (new) or `duckduckgo_search` (legacy) — whichever is importable. |
| DuckDuckGo HTML endpoint | `https://html.duckduckgo.com/html/` — used as HTTP fallback when SDK is unavailable. |
| Reuse of RAG retrievers | All FAISS / BM25 / CrossEncoder access is proxied through the parent `RAGPipeline` instance. |

---

## 7. Notable Algorithms

### 7.1 Three-layer regex routing

See `fast_router` (section 3.4). The novelty is **layer 3 default-AGENT_TOOLS** — most routers bias toward chat for unknown queries, but here retrieval is the cheaper "we tried" path so the bias is reversed.

### 7.2 Substring keyword matching

The `any(k in clean_text for k in ("scholar", "intern", "opportunit", "fellow"))` substring check at line 55 is a clever misspelling tolerance layer — `clean_text` already lowercased and punctuation-stripped, so `"opportunites"` and `"scholorship"` both match before ever needing an embedding lookup.

### 7.3 Native tool-calling with graceful fallback

When `bind_tools` raises (some GGUF builds don't support tool calling), the agent **silently** switches to keyword-only routing rather than failing the request. This makes the system robust to backend capability differences — Ollama-native tool calling works, llama-server tool calling works, in-process llama.cpp falls back to keyword routing.

### 7.4 Empty-result → web fallback

If local retrieval returns zero documents, the agent **automatically retries** with `search_live_web`. The user perceives a single coherent answer; internally the model sees `LIVE WEB SEARCH RESULTS:` in its context instead of an empty context block.

### 7.5 Single-tool selection (first call wins)

When the LLM emits multiple `tool_calls`, only `tool_calls[0]` is honored. This avoids the complexity of multi-tool chaining for a v1 implementation; the system prompt guides the model to emit a single best tool choice.

---

## 8. Error Handling

| Failure | Behavior |
| ------- | -------- |
| `langgraph` import fails | `LANGGRAPH_AVAILABLE = False`, `END = "__end__"`. Not currently used, but the flag is there for future StateGraph-based refactors. (`backend/agent.py:11`) |
| `ddgs` / `duckduckgo_search` import fails | `DDG_AVAILABLE = False`, search falls back to HTTP. (`backend/agent.py:21`) |
| `DDGS().text(...)` raises | Caught, `results_text` stays empty, HTTP fallback attempted. (`backend/agent.py:80`) |
| HTTP fallback raises | Caught, returns `"No live web results found for the query."` (string returned as context so LLM can acknowledge absence). (`backend/agent.py:94`) |
| `bind_tools(...)` raises | Caught, falls back to keyword intent classification. (`backend/agent.py:204`) |
| LLM emits no `tool_calls` | `has_tool_call = False`, returns empty context — back in `stream_response` (`backend/rag.py:570`), this collapses to non-opportunity template. |
| `ensemble.invoke(query)` raises | Caught, returns `[]` — agent then falls back to live web search. (`backend/agent.py:151`) |
| BeautifulSoup import inside `search_live_web` fails (only if HTTP path taken) | Caught, returns `"No live web results found for the query."` (`backend/agent.py:94`) |

The agent is **exception-bounded at every step** so the request always returns *something* useful.

---

## 9. Notable Patterns & Design Decisions

1. **Two-tier decision making.** `fast_router` (Python regex, ~10 µs) decides *whether* to call tools; the LLM itself decides *which* tool to call. This avoids the cost of an LLM call for every greeting and the brittleness of regex for tool selection.

2. **Default-AGENT_TOOLS bias.** Preferable to default-CHAT bias because retrieval degrades gracefully (returns empty context → falls back to chat template) while chat-mode mis-routing produces a hard *"I don't have that info"* response.

3. **Native LangChain tool calling instead of LangGraph state machine.** Despite the class name `OpportunityAgentGraph` and the `langgraph.graph` import probe, the actual implementation is a sequential Python function with `@tool` decorators. This trades a bit of explicit state-machine clarity for **lower abstraction overhead** and easier reasoning about async behavior. The `LANGGRAPH_AVAILABLE` flag suggests a future StateGraph migration is possible without breaking the public API.

4. **Tool docstrings drive selection.** The body of each `@tool` function is irrelevant (just returns `query`); the **docstring** is the prompt the LLM reads. `"PRIMARY DEFAULT TOOL"` and `"FALLBACK TOOL"` are deliberate ordering cues.

5. **Graceful degradation from local to web search.** When local retrieval returns nothing, the agent retries with web search automatically. The user perceives one answer; the LLM sees a coherent context block.

6. **Cached ensemble.** The agent **reuses** `self.rag_pipeline.ensemble_retriever` (set by `stream_response` at `backend/rag.py:544`) instead of rebuilding it on every call. For cold-call paths where the attribute doesn't exist yet, it builds the ensemble on demand using the exact same weights (`[0.4, 0.6]`) and MMR params as the main pipeline.

7. **Python-side tool-name heuristic.** Selection is done by `if "web" in selected_tool_name` — robust to tool name variations like `search_live_web_tool` / `search_web_tool` / `web_search_tool`.

8. **Sub-millisecond routing.** The router doesn't touch embeddings or models — it uses pure Python set operations. For high-QPS scenarios this is the difference between 5 ms and 500 ms per request.

9. **Misspelling tolerance via substrings.** `"opportunit"` catches both the en-GB spelling (`opportunities`) and common typos (`opportunites`, `opurtunity`). Combined with the canonical `DOMAIN_KEYWORDS` set, this catches the long tail of user input variations.

10. **Clean separation of routing from execution.** `fast_router` returns a routing decision; `OpportunityAgentGraph` executes the decision. Either can be swapped (e.g., replace `fast_router` with a small classifier model) without touching the other.

---

## Cross-references

- Caller: `backend/rag.py` — `stream_response` calls `fast_router` (line 549) and `OpportunityAgentGraph(self).run_agent_workflow(...)` (line 557)
- Retrieval backends: see `docs/rag_engine.md` for FAISS + BM25 + CrossEncoder details
- Semantic cache: see `docs/answer_cache.md` — `fast_router` doesn't touch the cache directly, but `stream_response` does before calling the agent
- Test coverage: `tests/` does not currently have a dedicated test for `agent.py`; behavior is validated end-to-end through the FastAPI layer