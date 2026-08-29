import re
from enum import Enum
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

# Try importing langgraph or define a lightweight state graph fallback
try:
    from langgraph.graph import StateGraph, END  # noqa: F401 -- StateGraph import doubles as availability probe
    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False
    END = "__end__"

# Try importing ddgs / duckduckgo_search or provide httpx fallback
try:
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS
    DDG_AVAILABLE = True
except ImportError:
    DDG_AVAILABLE = False

import httpx

class RouteTarget(Enum):
    DIRECT_CHAT = "direct_chat"
    AGENT_TOOLS = "agent_tools"

DOMAIN_KEYWORDS = {
    "opportunity", "opportunities", "opportunites", "opurtunity",
    "scholarship", "scholarships", "scholorship", "internship", "internships",
    "intern", "interns", "graduate", "graduates", "undergraduate", "undergraduates",
    "fellowship", "fellowships", "fellow", "fellows", "grant", "grants",
    "funding", "deadline", "apply", "application", "phd", "masters", "bachelors",
    "days", "link", "url", "unsw", "labx", "daad", "program", "programs",
    "programme", "programmes", "university", "universities", "job", "jobs",
    "postdoc", "degree", "courses", "course", "residency"
}

CASUAL_GREETINGS = {
    "hi", "hello", "hey", "greetings", "good morning", "good evening",
    "thanks", "thank you", "bye", "goodbye", "who are you", "what is your name",
    "how are you", "how are you doing", "what are you saying", "what do you do",
    "what can you do", "help"
}


def fast_router(prompt: str) -> RouteTarget:
    """Level 1 Fast Python Router (0.01 ms execution) to route casual vs tool queries."""
    clean_text = re.sub(r"[^\w\s]", "", prompt.lower()).strip()
    words = set(clean_text.split())

    # Layer 1: Domain Keyword or Substring Override (Highest Priority)
    if words.intersection(DOMAIN_KEYWORDS) or any(k in clean_text for k in ("scholar", "intern", "opportunit", "fellow")):
        return RouteTarget.AGENT_TOOLS

    # Layer 2: Exact Casual Greeting / Conversational Check
    if clean_text in CASUAL_GREETINGS:
        return RouteTarget.DIRECT_CHAT

    # Layer 3: Default Safety Net -> AGENT_TOOLS (let vector search evaluate relevance)
    return RouteTarget.AGENT_TOOLS


def search_live_web(query: str, max_results: int = 5) -> str:
    """Real-time lightweight web search fallback using DuckDuckGo or HTTP fallback."""
    results_text = []
    
    if DDG_AVAILABLE:
        try:
            with DDGS() as ddgs:
                ddg_results = list(ddgs.text(query, max_results=max_results))
                for idx, res in enumerate(ddg_results, 1):
                    title = res.get("title", "No Title")
                    snippet = res.get("body", "")
                    href = res.get("href", "")
                    results_text.append(f"[{idx}] {title}\nURL: {href}\nSummary: {snippet}")
        except Exception as e:
            print(f"[Agent Tool] DDG search error: {e}")

    # Fallback to direct HTTP search if DDG failed or returned empty
    if not results_text:
        try:
            url = "https://html.duckduckgo.com/html/"
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            resp = httpx.post(url, data={"q": query}, headers=headers, timeout=5.0)
            if resp.status_code == 200:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(resp.text, "html.parser")
                results = soup.find_all("a", class_="result__snippet", limit=max_results)
                for idx, r in enumerate(results, 1):
                    results_text.append(f"[{idx}] Web Search Result\nSummary: {r.get_text(strip=True)}")
        except Exception as e:
            print(f"[Agent Tool] HTTP search fallback error: {e}")

    if results_text:
        return "\n\n".join(results_text)
    return "No live web results found for the query."


class OpportunityAgentState(BaseModel):
    prompt: str
    history: List[Dict[str, Any]] = Field(default_factory=list)
    retrieved_docs: List[Any] = Field(default_factory=list)
    context_text: str = ""
    used_tools: List[str] = Field(default_factory=list)
    final_response: str = ""


from langchain_core.tools import tool

class OpportunityAgentGraph:
    """LangGraph / Tool-Calling Agent state machine for opportunity queries."""

    def __init__(self, rag_pipeline):
        self.rag_pipeline = rag_pipeline

    def search_local_opportunities(self, query: str) -> List[Any]:
        """Tool 1: Searches local Parent-Child FAISS vector store & BM25 index."""
        if not self.rag_pipeline or not self.rag_pipeline.is_initialized:
            return []

        try:
            ensemble = getattr(self.rag_pipeline, "ensemble_retriever", None)
            if not ensemble:
                if self.rag_pipeline.vectorstore is not None and self.rag_pipeline.bm25_retriever is not None:
                    from langchain_classic.retrievers import EnsembleRetriever
                    dense_retriever = self.rag_pipeline.vectorstore.as_retriever(
                        search_type="mmr",
                        search_kwargs={"k": 7, "fetch_k": 15, "lambda_mult": 0.5},
                    )
                    ensemble = EnsembleRetriever(
                        retrievers=[self.rag_pipeline.bm25_retriever, dense_retriever],
                        weights=[0.4, 0.6],
                    )
                else:
                    ensemble = self.rag_pipeline.bm25_retriever
                self.rag_pipeline.ensemble_retriever = ensemble

            if not ensemble:
                return []

            matched_chunks = ensemble.invoke(query)
            parent_docs = self.rag_pipeline.resolve_parent_docs(matched_chunks)

            if parent_docs and self.rag_pipeline.reranker:
                from backend.rag import rerank_docs
                return rerank_docs(query, parent_docs, self.rag_pipeline.reranker, top_k=5)
            return parent_docs[:5]
        except Exception as e:
            print(f"[Agent Tool] Local RAG retrieval error: {e}")
            return []

    async def run_agent_workflow(
        self,
        prompt: str,
        history: Optional[List[Dict[str, Any]]] = None,
        provider: str = "Ollama",
        think: bool = False,
        ollama_base_url: Optional[str] = None,
        llamacpp_server_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Native Tool Calling Workflow: Evaluates LLM tool_calls decision before executing tools."""
        state = OpportunityAgentState(prompt=prompt, history=history or [])
        
        # 1. Instantiate LLM to evaluate native tool binding
        llm = self.rag_pipeline.get_llm(
            provider=provider,
            think_mode=think,
            ollama_base_url=ollama_base_url,
            llamacpp_server_url=llamacpp_server_url,
        )

        # 2. Define tool definitions for LLM tool binding
        @tool
        def search_local_opportunities_tool(query: str) -> str:
            """PRIMARY DEFAULT TOOL: Search local database containing all scraped recent scholarships, fellowships, internships, grants and similar."""
            return query

        @tool
        def search_live_web_tool(query: str) -> str:
            """FALLBACK TOOL: Search the live web ONLY if local database search yields no results or if live web search is explicitly requested."""
            return query

        tools = [search_local_opportunities_tool, search_live_web_tool]
        
        # 3. Bind tools and invoke LLM for intent evaluation
        has_tool_call = False
        selected_tool_name = ""
        tool_query = prompt

        try:
            llm_with_tools = llm.bind_tools(tools)
            ai_msg = await llm_with_tools.ainvoke([{"role": "user", "content": prompt}])
            
            tool_calls = getattr(ai_msg, "tool_calls", []) or []
            if tool_calls:
                has_tool_call = True
                first_call = tool_calls[0]
                selected_tool_name = first_call.get("name", "")
                args = first_call.get("args", {})
                tool_query = args.get("query", prompt)
        except Exception as err:
            print(f"[Agent Tool Calling] Fallback to keyword intent due to bind_tools error: {err}")
            # Keyword fallback if model doesn't support native tool binding
            clean_text = prompt.lower()
            if any(k in clean_text for k in ("scholar", "intern", "opportunit", "fellow", "grant", "funding", "phd", "masters")):
                has_tool_call = True
                selected_tool_name = "search_local_opportunities_tool"

        # 4. Execute tool if requested by LLM
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

        return {
            "context_text": state.context_text,
            "used_tools": state.used_tools,
            "has_tool_call": has_tool_call,
            "retrieved_docs": [
                (d.metadata.get("name") or d.metadata.get("title") or "Opportunity")
                for d in state.retrieved_docs
            ] if state.retrieved_docs else [],
        }
