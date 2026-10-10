from __future__ import annotations

import logging
import os
import re
import warnings
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

# Suppress runtime warnings if a legacy duckduckgo_search package is encountered
warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*renamed to.*")
warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*duckduckgo.*")

# Try importing ddgs (modern package) or duckduckgo_search (legacy package)
try:
    from ddgs import DDGS
    DDG_AVAILABLE = True
except ImportError:
    try:
        from duckduckgo_search import DDGS  # type: ignore[import-not-found]  # pyright: ignore[reportMissingImports]
        DDG_AVAILABLE = True
    except ImportError:
        DDG_AVAILABLE = False

import httpx

from backend.text_utils import clean_opportunity_text, fix_mojibake

logger = logging.getLogger(__name__)

os.environ.setdefault("NEEDLE_TELEMETRY", "0")

# Check needle availability
try:
    from needle import Needle, tool
    NEEDLE_AVAILABLE = True
except ImportError:
    NEEDLE_AVAILABLE = False
    Needle = None

    def tool(f):
        return f


@tool
def lookup_opportunity_details(opportunity_name: str):
    """Get full details, requirements, eligibility, deadline, or application instructions for a specific named scholarship, fellowship, conference, townhall, or internship program."""
    pass


@tool
def search_opportunities(scholarship_query: str):
    """Search database for scholarships, fellowships, internships, or programs by country, degree, or keyword."""
    pass


@tool
def search_web(web_query: str):
    """Search online on the web either when the user explicitly asks to search online, search the web, browse the internet, or look up live or recent external information."""
    pass


class NeedleDecision(BaseModel):
    tool_name: Optional[str] = None
    arguments: Dict[str, Any] = Field(default_factory=dict)
    confidence: Optional[float] = None
    reasoning: Optional[str] = None
    raw_response: Dict[str, Any] = Field(default_factory=dict)


class NeedleRouter:
    """Singleton CPU-based dispatcher wrapping Needle3 (98M)."""

    def __init__(self):
        self._agent: Optional[Any] = None
        if NEEDLE_AVAILABLE:
            try:
                self._agent = Needle(
                    tools=[search_opportunities, lookup_opportunity_details, search_web],
                    stateless=True,
                )
                logger.info("[NeedleRouter] Initialized Needle3 98M CPU agent successfully.")
            except Exception as e:
                logger.warning(f"[NeedleRouter] Failed to initialize Needle: {e}")
                self._agent = None

    @property
    def is_available(self) -> bool:
        return self._agent is not None

    def route(self, prompt: str) -> NeedleDecision:
        """Route user query with sub-15ms CPU latency."""
        if not self._agent:
            return NeedleDecision()

        try:
            res = self._agent.complete(prompt)
            calls = res.get("function_calls", [])
            conf = res.get("confidence")
            reasoning = res.get("reasoning")

            if calls:
                first_call = calls[0]
                raw_args = dict(first_call.get("arguments", {}))
                # Disambiguated parameters normalization for backwards compatibility
                if "opportunity_name" in raw_args and "opportunity" not in raw_args:
                    raw_args["opportunity"] = raw_args["opportunity_name"]
                if "scholarship_query" in raw_args and "query" not in raw_args:
                    raw_args["query"] = raw_args["scholarship_query"]
                if "web_query" in raw_args and "query" not in raw_args:
                    raw_args["query"] = raw_args["web_query"]

                return NeedleDecision(
                    tool_name=first_call.get("name"),
                    arguments=raw_args,
                    confidence=conf,
                    reasoning=reasoning,
                    raw_response=res,
                )

            return NeedleDecision(
                confidence=conf,
                reasoning=reasoning,
                raw_response=res,
            )
        except Exception as e:
            logger.warning(f"[NeedleRouter] Inference error: {e}")
            return NeedleDecision()


# Global singleton
_router_instance: Optional[NeedleRouter] = None


def get_needle_router() -> NeedleRouter:
    global _router_instance
    if _router_instance is None:
        _router_instance = NeedleRouter()
    return _router_instance


def resolve_opportunity_deep_dive(
    opportunity_name: str,
) -> Optional[Dict[str, Any]]:
    """Retrieve full un-truncated document for a specific named opportunity (Deep Search)."""
    if not opportunity_name or not str(opportunity_name).strip():
        return None

    try:
        from backend.database import get_opportunity_by_title_or_query
        return get_opportunity_by_title_or_query(opportunity_name)
    except Exception as e:
        logger.warning(f"[NeedleRouter] Deep dive lookup error for '{opportunity_name}': {e}")
        return None


def format_deep_dive_dossier(opp: Dict[str, Any]) -> str:
    """Format full un-truncated parent opportunity into a structured dossier for the LLM."""
    title = opp.get("title") or "Opportunity"
    url = opp.get("url") or ""
    deadline = opp.get("deadline") or ""
    content = clean_opportunity_text(opp.get("content") or "")

    parts = [
        f"=== FULL APPLICATION DOSSIER: {title.upper()} ===",
        f"Official Link: {url}" if url else "",
        f"Application Deadline: {deadline}" if deadline else "",
        "\n--- COMPLETE DETAILS, ELIGIBILITY & APPLICATION INSTRUCTIONS ---",
        content,
    ]
    return "\n".join(p for p in parts if p)


def format_curated_roundup(opp: Dict[str, Any]) -> str:
    """Format a curated Top-N roundup compilation into a clean, comprehensive markdown list."""
    title = (opp.get("title") or "Curated Opportunities").strip()
    url = (opp.get("url") or "").strip()
    raw_content = opp.get("content") or ""

    text = raw_content.replace("Qu\ufffdbec", "Québec").replace("qu\ufffdbec", "québec")
    text = fix_mojibake(text)
    parts = re.split(r"(\d+#\s*)", text)

    formatted_items = []
    for i in range(1, len(parts), 2):
        num_str = parts[i].replace("#", "").strip()
        body = parts[i + 1].strip()

        deadline_m = re.search(r"-\s*DEADLINE\s*:\s*([^-\n]+?)(?=\s*-\s*[A-Z]+|\s*$)", body, re.IGNORECASE)
        level_m = re.search(r"-\s*LEVEL\s*:\s*([^-\n]+?)(?=\s*-\s*[A-Z]+|\s*$)", body, re.IGNORECASE)
        type_m = re.search(r"-\s*TYPE\s*:\s*([^-\n]+?)(?=\s*-\s*[A-Z]+|\s*$)", body, re.IGNORECASE)
        funding_m = re.search(r"-\s*FUNDING\s*:\s*([^-\n]+?)(?=\s*-\s*[A-Z]+|\s*$)", body, re.IGNORECASE)

        clean_body = re.sub(r"-\s*(?:DEADLINE|LEVEL|TYPE|FUNDING|HOW TO APPLY)\s*:[^-\n]*", "", body).strip()

        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", clean_body) if s.strip()]
        if sentences:
            first_sent = sentences[0]
            delim_match = re.match(r"^([^:\-–—]{5,80})\s*[:\-–—]\s*(.+)$", first_sent)
            if delim_match:
                item_title = delim_match.group(1).strip()
                desc = delim_match.group(2).strip() + (" " + " ".join(sentences[1:]) if len(sentences) > 1 else "")
            else:
                verb_match = re.match(r"^(.*?)(?:\s+(?:offers?|provides?|enables?|is|are|aims?|invites?)\b.*)", first_sent, re.IGNORECASE)
                if verb_match and len(verb_match.group(1).strip()) > 8:
                    item_title = verb_match.group(1).strip()
                    desc = clean_body[len(item_title) :].strip()
                else:
                    item_title = first_sent
                    desc = " ".join(sentences[1:]) if len(sentences) > 1 else ""
        else:
            item_title = f"Opportunity #{num_str}"
            desc = clean_body

        funding = funding_m.group(1).strip().strip(".") if funding_m else ""
        level = (level_m.group(1).strip() if level_m else (type_m.group(1).strip() if type_m else "")).strip().strip(".")
        deadline = deadline_m.group(1).strip().strip(".") if deadline_m else ""

        lines = [f"### {num_str}. {item_title}"]
        if funding:
            lines.append(f"- **Funding:** {funding}")
        if level:
            lines.append(f"- **Level / Type:** {level}")
        if deadline:
            lines.append(f"- **Deadline:** {deadline}")
        if desc:
            lines.append(f"- **Details:** {desc}")
        if url:
            lines.append(f"- **Official Details & Application:** [{item_title}]({url})")

        formatted_items.append("\n".join(lines))

    if not formatted_items:
        return f"### {title.title()}\n\n{text[:1500]}\n\n---\n*Source: [{title.title()}]({url})*"

    header = f"Here is the complete verified list of **{title.title()}**:\n\n"
    footer = f"\n\n---\n*Source: [{title.title()}]({url})*"
    return header + "\n\n".join(formatted_items) + footer


# Canonical Opportunity Domain Keywords (exact-word match)
OPPORTUNITY_KEYWORDS = {
    "opportunity", "opportunities", "scholarship", "scholarships",
    "internship", "internships", "intern", "interns",
    "graduate", "graduates", "undergraduate", "undergraduates",
    "fellowship", "fellowships", "fellow", "fellows", "grant", "grants",
    "funding", "deadline", "apply", "application", "phd", "master", "masters", "bachelors",
    "daad", "program", "programs", "programme", "programmes",
    "university", "universities", "job", "jobs", "postdoc", "degree", "residency",
}

# Substring roots (catch plurals, typos and derived forms)
OPPORTUNITY_ROOTS = (
    "scholar", "intern", "fellow", "grant", "funding",
    "daad", "postdoc", "master", "phd", "opportunit", "degre"
)

CASUAL_GREETINGS = {
    "hi", "hello", "hey", "greetings", "good morning", "good evening",
    "thanks", "thank you", "bye", "goodbye", "who are you", "what is your name",
    "how are you", "how are you doing", "what are you saying", "what do you do",
    "what can you do", "help",
}

WEB_EXPLICIT_TRIGGERS = re.compile(
    r"\b(?:web\s*search|search\s+(?:the\s+)?web|search\s+online|online\s+search|browse\s+(?:the\s+)?web|look\s*up\s+online|google(?:\s+it)?|search\s+(?:the\s+)?internet)\b",
    re.IGNORECASE,
)

NEGATION_TRIGGERS = re.compile(
    r"\b(?:don'?t\s+want|not\s+looking\s+for|tell\s+me\s+a\s+joke|write\s+a\s+poem)\b",
    re.IGNORECASE,
)

DEEP_DIVE_TRIGGERS = re.compile(
    r"\b(?:detailed?\s+(?:steps?|process|guide|instructions?|requirements?|breakdown)|how\s+to\s+apply\s+(?:for|to)\s+(?:in\s+detail)?|(?:full|all)\s+(?:details?|dossier|application|document)|essay\s+requirements?|application\s+procedure|step\s*by\s*step)\b",
    re.IGNORECASE,
)

EXTERNAL_INFO_TRIGGERS = re.compile(
    r"\b(?:recent|latest|news|current|today|yesterday|start\s*date|commencement|acceptance\s*rate|interview\s*dates?|selection\s*results?)\b",
    re.IGNORECASE,
)


def has_opportunity_intent(text: str) -> bool:
    """Whether a text mentions opportunity terms (exact words or roots)."""
    clean_lower = text.lower().strip()
    words = set(re.findall(r"\b\w+\b", clean_lower))
    if words.intersection(OPPORTUNITY_KEYWORDS):
        return True
    return any(root in clean_lower for root in OPPORTUNITY_ROOTS)


def is_casual_chat(prompt: str) -> bool:
    """True for pure greetings / persona chit-chat that never needs any tool."""
    clean_text = re.sub(r"[^\w\s]", "", prompt.lower()).strip()
    return clean_text in CASUAL_GREETINGS and not has_opportunity_intent(clean_text)


def is_simple_opportunity_query(prompt: str) -> bool:
    """Check if prompt is a simple, unambiguous opportunity query eligible for fast bypass.

    Returns True ONLY if it has opportunity intent and does NOT contain an explicit web
    request, external info triggers (start date, acceptance rate, news), a negation / conversational
    override, or a deep-dive trigger. Everything else is left to the Needle router or fallback.
    """
    clean_lower = prompt.lower().strip()
    if (
        WEB_EXPLICIT_TRIGGERS.search(clean_lower)
        or EXTERNAL_INFO_TRIGGERS.search(clean_lower)
        or NEGATION_TRIGGERS.search(clean_lower)
        or DEEP_DIVE_TRIGGERS.search(clean_lower)
    ):
        return False
    return has_opportunity_intent(clean_lower)


def parse_web_snippets_from_text(text: str) -> List[Dict[str, str]]:
    """Parse structured web snippet dictionaries from search_live_web output."""
    snippets = []
    blocks = text.split("\n\n")
    for b in blocks:
        lines = [line.strip() for line in b.split("\n") if line.strip()]
        if not lines:
            continue
        title = re.sub(r"^\[\d+\]\s*", "", lines[0])
        url = ""
        summary = ""
        for line in lines[1:]:
            if line.startswith("URL:"):
                url = line[4:].strip()
            elif line.startswith("Summary:"):
                summary = line[8:].strip()
            elif not summary:
                summary = line
        if title and title != "No live web results found for the query.":
            snippets.append({"title": title, "url": url, "snippet": summary})
    return snippets


def clean_web_query(q: str) -> str:
    """Strip conversational filler prefixes (e.g. 'tell me about the...') so search engines get concise keywords."""
    cleaned = re.sub(
        r"^(?:please\s+)?(?:can\s+you\s+)?(?:could\s+you\s+)?(?:tell\s+me\s+(?:about|all\s+about)|what\s+is|what\s+are|who\s+is|who\s+are|search\s+(?:for|about|the\s+web\s+for|online\s+for)?|give\s+me\s+info\s+(?:on|about)?|look\s+up|find\s+out\s+about|web\s*search(?:\s+for)?)\s+(?:the\s+|an?\s+)?",
        "",
        q.strip(),
        flags=re.IGNORECASE,
    ).strip()
    return cleaned if cleaned else q.strip()


def search_live_web(query: str, max_results: int = 5) -> str:
    """Real-time lightweight web search fallback using DuckDuckGo or HTTP fallback."""
    query = clean_web_query(query)
    results_text = []

    if DDG_AVAILABLE:
        try:
            with DDGS(timeout=10) as ddgs:
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
            resp = httpx.post(url, data={"q": query}, headers=headers, timeout=8.0)
            if resp.status_code == 200:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(resp.text, "html.parser")
                results = soup.find_all("div", class_="result", limit=max_results)
                if results:
                    for idx, r in enumerate(results, 1):
                        t_tag = r.find("a", class_="result__a")
                        title = t_tag.get_text(strip=True) if t_tag else "Web Search Result"
                        href = t_tag.get("href", "") if t_tag else ""
                        snippet_tag = r.find("a", class_="result__snippet")
                        snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""
                        results_text.append(f"[{idx}] {title}\nURL: {href}\nSummary: {snippet}")
                else:
                    snippet_tags = soup.find_all("a", class_="result__snippet", limit=max_results)
                    for idx, r in enumerate(snippet_tags, 1):
                        results_text.append(f"[{idx}] Web Search Result\nURL: \nSummary: {r.get_text(strip=True)}")
        except Exception as e:
            print(f"[Agent Tool] HTTP search fallback error: {e}")

    if results_text:
        return "\n\n".join(results_text)
    return "No live web results found for the query."


def _doc_names(docs: List[Any]) -> List[str]:
    return [(d.metadata.get("name") or d.metadata.get("title") or "Opportunity") for d in docs]


def _tool_result(
    tool: str,
    context: str,
    docs: Optional[List[str]] = None,
    tool_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "context_text": context,
        "used_tools": [tool],
        "has_tool_call": True,
        "retrieved_docs": docs or [],
        "tool_data": tool_data or {},
    }


_NO_RESULT: Dict[str, Any] = {
    "context_text": "",
    "used_tools": [],
    "has_tool_call": False,
    "retrieved_docs": [],
    "tool_data": {},
}


class OpportunityAgentGraph:
    """Fast Tool-Calling Agent for opportunity queries.

    Routing order (first hit wins):
      0. Casual chat / negation  -> no tools
      1. Simple opportunity query -> local hybrid search (no model call)
      2. Needle3 CPU router       -> deep dive | local search | web
      3. Keyword fallback         -> deep dive | local search | web
    """

    def __init__(self, rag_pipeline):
        self.rag_pipeline = rag_pipeline

    def search_local_opportunities(self, query: str, rerank: bool = False) -> List[Any]:
        """Tool 1: Searches local Parent-Child FAISS vector store & BM25 index."""
        if not self.rag_pipeline or not self.rag_pipeline.is_initialized:
            return []

        try:
            ensemble = getattr(self.rag_pipeline, "ensemble_retriever", None)
            if not ensemble and hasattr(self.rag_pipeline, "_build_ensemble_retriever"):
                ensemble = self.rag_pipeline._build_ensemble_retriever()

            if not ensemble:
                return []

            matched_chunks = ensemble.invoke(query)
            parent_docs = self.rag_pipeline.resolve_parent_docs(matched_chunks)

            # Prioritize parents directly matching specific entities/locations mentioned in query
            stop_words = {"the", "list", "show", "tell", "about", "all", "available", "find", "for", "and", "in", "any", "some"}
            query_words = set(re.findall(r"\b\w{3,}\b", query.lower())) - stop_words

            def relevance_score(doc: Any) -> int:
                meta = getattr(doc, "metadata", {}) or {}
                title = (meta.get("name") or meta.get("title") or "").lower()
                loc = (meta.get("location") or "").lower()
                text = (getattr(doc, "page_content", "") or "").lower()
                score = 0
                for w in query_words:
                    if w in loc:
                        score += 5
                    if w in title:
                        score += 3
                    if w in text:
                        score += 1
                return score

            if parent_docs and query_words:
                parent_docs.sort(key=relevance_score, reverse=True)

            if parent_docs and rerank and getattr(self.rag_pipeline, "reranker", None):
                from backend.rag import rerank_docs
                return rerank_docs(query, parent_docs, self.rag_pipeline.reranker, top_k=6)
            return parent_docs[:6]
        except Exception as e:
            print(f"[Agent Tool] Local RAG retrieval error: {e}")
            return []

    # ---- tool wrappers: each returns a result dict, or None when it found nothing ----

    def _local_result(self, tool: str, query: str, rerank: bool = False) -> Optional[Dict[str, Any]]:
        docs = self.search_local_opportunities(query, rerank=rerank)
        if not docs:
            return None
        from backend.rag import format_context_snippets
        doc_names = _doc_names(docs)
        return _tool_result(
            tool,
            format_context_snippets(docs),
            doc_names,
            tool_data={"query": query, "local_docs": doc_names},
        )

    @staticmethod
    def _deep_result(target: str) -> Optional[Dict[str, Any]]:
        opp = resolve_opportunity_deep_dive(target)
        if not opp:
            return None
        title = opp.get("title") or target
        return _tool_result(
            "lookup_opportunity_details",
            format_deep_dive_dossier(opp),
            [title],
            tool_data={
                "deep_dive": {
                    "title": title,
                    "url": opp.get("url") or "",
                    "deadline": opp.get("deadline") or "",
                }
            },
        )

    @staticmethod
    def _web_result(query: str) -> Dict[str, Any]:
        raw_text = search_live_web(query)
        snippets = parse_web_snippets_from_text(raw_text)
        return _tool_result(
            "search_live_web",
            f"LIVE WEB SEARCH RESULTS:\n{raw_text}",
            tool_data={"query": query, "web_results": snippets},
        )

    def _needle_result(self, prompt: str, rerank: bool = False) -> Optional[Dict[str, Any]]:
        """Layer 2: CPU Needle3 router (sub-15ms, 0 MB GPU VRAM)."""
        try:
            router = get_needle_router()
            if not router.is_available:
                return None
            decision = router.route(prompt)
            args = decision.arguments
            if decision.tool_name == "lookup_opportunity_details":
                target = args.get("opportunity_name") or args.get("opportunity") or prompt
                deep_res = self._deep_result(target)
                if deep_res:
                    return deep_res
                if EXTERNAL_INFO_TRIGGERS.search(prompt):
                    return self._web_result(prompt)
                return None
            if decision.tool_name == "search_opportunities":
                target = args.get("scholarship_query") or args.get("query") or prompt
                if not has_opportunity_intent(prompt) and EXTERNAL_INFO_TRIGGERS.search(prompt):
                    return self._web_result(prompt)
                return self._local_result("search_opportunities", target, rerank=rerank)
            if decision.tool_name == "search_web":
                return self._web_result(args.get("web_query") or args.get("query") or prompt)
        except Exception as e:
            print(f"[NeedleRouter] Agent workflow routing error: {e}")
        return None

    async def run_agent_workflow(
        self,
        prompt: str,
        history: Optional[List[Dict[str, Any]]] = None,
        rerank: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Resolve the prompt into retrieval context using the cheapest tool that can answer it."""
        # 0. Nothing to retrieve for greetings or explicit "not that" requests
        if is_casual_chat(prompt) or NEGATION_TRIGGERS.search(prompt):
            return dict(_NO_RESULT)

        # 1. Explicit Web Search Override: user explicitly requested web / online search
        if WEB_EXPLICIT_TRIGGERS.search(prompt):
            return self._web_result(prompt)

        # 2. Fast bypass for simple, unambiguous opportunity queries
        if is_simple_opportunity_query(prompt):
            result = self._local_result("search_local_opportunities", prompt, rerank=rerank)
            if result:
                return result

        # 3. Needle3 tool routing
        result = self._needle_result(prompt, rerank=rerank)
        if result:
            return result

        # 4. Deterministic keyword fallback (Needle unavailable, no tool picked, or tool found nothing)
        intent = has_opportunity_intent(prompt)
        if DEEP_DIVE_TRIGGERS.search(prompt):
            result = self._deep_result(prompt)
        elif EXTERNAL_INFO_TRIGGERS.search(prompt):
            return self._web_result(prompt)
        elif intent:
            result = self._local_result("search_opportunities", prompt, rerank=rerank)
        if result:
            return result

        if intent or WEB_EXPLICIT_TRIGGERS.search(prompt) or EXTERNAL_INFO_TRIGGERS.search(prompt):
            return self._web_result(prompt)

        return dict(_NO_RESULT)
