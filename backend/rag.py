import asyncio
import datetime
import hashlib
import json
import os
import re
import sys
import unicodedata
import warnings
from pathlib import Path
from typing import AsyncGenerator, Dict, List, Optional, Any
from uuid import uuid4

# Suppress deprecation warnings
warnings.filterwarnings("ignore", category=DeprecationWarning, message=".*langchain-community.*")

# Add PyTorch CUDA DLL path on Windows to avoid DLL loading issues for PyTorch / Transformers
if sys.platform == "win32":
    try:
        torch_lib_dir = os.path.join(sys.prefix, "Lib", "site-packages", "torch", "lib")
        if os.path.exists(torch_lib_dir):
            os.add_dll_directory(torch_lib_dir)
    except Exception:
        pass

try:
    import torch
    DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
except Exception as torch_err:
    print(f"[RAG] Warning: torch could not be imported ({torch_err}). Defaulting device to cpu.")
    torch = None
    DEVICE = "cpu"

try:
    from langchain_community.retrievers import BM25Retriever
    from langchain_community.vectorstores import FAISS
    from langchain_community.vectorstores.utils import DistanceStrategy
    from langchain_core.documents import Document
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import ChatPromptTemplate, HumanMessagePromptTemplate, SystemMessagePromptTemplate
    from langchain_classic.retrievers import EnsembleRetriever
    from langchain_ollama import ChatOllama
except Exception as e:
    print(f"[RAG] Warning: Core LangChain components import exception ({e}). Using minimal fallbacks.")
    class Document:  # type: ignore
        def __init__(self, page_content="", metadata=None):
            self.page_content = page_content
            self.metadata = metadata or {}
    BM25Retriever = Any  # type: ignore
    FAISS = Any  # type: ignore
    DistanceStrategy = Any  # type: ignore
    StrOutputParser = Any  # type: ignore
    ChatPromptTemplate = Any  # type: ignore
    HumanMessagePromptTemplate = Any  # type: ignore
    SystemMessagePromptTemplate = Any  # type: ignore
    EnsembleRetriever = Any  # type: ignore
    ChatOllama = Any  # type: ignore

try:
    from langchain_community.chat_models import ChatLlamaCpp
except Exception as e:
    print(f"[RAG] Warning: ChatLlamaCpp could not be imported ({e}).")
    ChatLlamaCpp = Any  # type: ignore

try:
    from langchain_huggingface import HuggingFaceEmbeddings
except Exception as e:
    print(f"[RAG] Warning: HuggingFaceEmbeddings could not be imported ({e}).")
    HuggingFaceEmbeddings = Any  # type: ignore

try:
    from sentence_transformers import CrossEncoder
except Exception as e:
    print(f"[RAG] Warning: CrossEncoder could not be imported ({e}).")
    CrossEncoder = Any  # type: ignore

from backend.answer_cache import SemanticAnswerCache
from config import settings
from scraper import CombinedScraper

# Constants
DAYS_BACK = settings.scraper.days_back
SCORE_THRESHOLD = settings.scraper.score_threshold
EMBEDDING_MODEL = settings.model.embedding_model
CURRENT_DATE_STR = datetime.date.today().strftime("%d/%B/%Y")
MODEL_PATH = settings.model.resolved_main_model_path


def fetch_opportunity_documents() -> List[Document]:
    """Fetch opportunities from SQLite database, scraped_data.txt, or CombinedScraper."""
    docs: List[Document] = []
    ignored_titles = {"about us", "our services", "contact us", "privacy policy", "terms of service", "our team"}

    # 1. Try fetching from SQLite database first
    try:
        from backend.database import list_opportunities, upsert_opportunities
        db_res = list_opportunities(limit=1000)
        items = db_res.get("items", [])
        if items:
            for item in items:
                title = item.get("title", "")
                if title and title.strip().lower() in ignored_titles:
                    continue
                docs.append(
                    Document(
                        page_content=unicodedata.normalize("NFKC", item.get("content", "")),
                        metadata={"name": title, "id": str(item.get("id", uuid4())), "url": item.get("url", "")},
                    )
                )
            if docs:
                print(f"[RAG] Loaded {len(docs)} documents from SQLite opportunities table.")
                return docs
    except Exception as exc:
        print(f"[RAG] DB fetch error: {exc}")

    # 2. Try loading from scraped_data.txt file
    try:
        data_file = Path("scraped_data.txt")
        if data_file.is_file():
            with open(data_file, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
            if isinstance(raw_data, list) and raw_data:
                # Store in SQLite for future queries
                try:
                    upsert_opportunities(raw_data)
                except Exception:
                    pass

                for item in raw_data:
                    if isinstance(item, dict):
                        name = item.get("name") or item.get("title") or ""
                        if name and name.strip().lower() in ignored_titles:
                            continue
                        docs.append(
                            Document(
                                page_content=unicodedata.normalize("NFKC", item.get("content", "")),
                                metadata={"name": name, "id": str(uuid4())},
                            )
                        )
                if docs:
                    print(f"[RAG] Loaded {len(docs)} documents from scraped_data.txt.")
                    return docs
    except Exception as exc:
        print(f"[RAG] File fetch error: {exc}")

    # 3. Fallback to scraping live
    try:
        scraper = CombinedScraper(days_back=DAYS_BACK, threshold=SCORE_THRESHOLD)

        async def _run_with_enrichment():
            data = await scraper.run_all_scrapers()
            await scraper.await_enrichment()
            return data

        raw_data = asyncio.run(_run_with_enrichment())
        if isinstance(raw_data, list):
            for item in raw_data:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("title") or ""
                    if name and name.strip().lower() in ignored_titles:
                        continue
                    docs.append(
                        Document(
                            page_content=unicodedata.normalize("NFKC", item.get("content", "")),
                            metadata={"name": name, "id": str(uuid4())},
                        )
                    )
    except Exception as exc:
        print(f"[RAG] Failed to fetch remote opportunities: {exc}")

    if not docs:
        docs.append(
            Document(
                page_content=(
                    "This is fallback context used when no recent opportunities meet the selection criteria. "
                    "Answer casual questions naturally even if no specific opportunities are provided."
                ),
                metadata={"name": "fallback", "id": str(uuid4())},
            )
        )
    return docs


def rerank_docs(query: str, docs: List[Document], reranker: CrossEncoder, top_k: int = 5) -> List[Document]:
    """Rerank retrieved document candidates using CrossEncoder."""
    if not docs:
        return []
    pairs = [(query, doc.page_content) for doc in docs]
    scores = reranker.predict(pairs)
    scored_docs = list(zip(docs, scores))
    scored_docs.sort(key=lambda x: x[1], reverse=True)
    return [doc for doc, _ in scored_docs[:top_k]]


def format_context_snippets(docs: List[Document], max_chars: int = 3500) -> str:
    """Format document content into concise context text preserving full details and deadlines."""
    seen_titles = set()
    chunks = []
    for doc in docs:
        title = (doc.metadata or {}).get("name", "Unknown").strip() or "Unknown"
        if title in seen_titles:
            continue
        snippet = (doc.page_content or "").strip()
        if not snippet:
            continue
        normalized = " ".join(snippet.split())
        chunks.append(f"Title: {title}\nContent: {normalized[:max_chars]}")
        seen_titles.add(title)
    return "\n\n".join(chunks)


def compress_history_for_opportunities(history: List[Dict[str, Any]], max_items: int = 6) -> str:
    """Compress past assistant opportunity responses to titles list to save ~2,000 prefill tokens while preserving memory."""
    if not history:
        return "(no prior history)"

    recent = history[-max_items:]
    history_lines = []
    for m in recent:
        role = m.get("role", "user")
        content = m.get("content", "").strip()

        if role == "user":
            history_lines.append(f"User: {content}")
        else:
            meta = m.get("metadata") or {}
            cards = meta.get("opportunity_cards") or []
            if cards:
                titles = [c.get("title", "") for c in cards if isinstance(c, dict) and c.get("title")]
                if titles:
                    history_lines.append(f"Assistant: Provided details for [{', '.join(titles)}]")
                    continue

            extracted_titles = []
            for line in content.split("\n"):
                line_clean = line.strip()
                if line_clean.startswith("#") or re.match(r"^\d+\.", line_clean) or (line_clean.startswith("**") and line_clean.endswith("**")):
                    clean_t = line_clean.lstrip("#0123456789. *").rstrip("*").strip()
                    if len(clean_t) > 3 and "deadline" not in clean_t.lower() and "location" not in clean_t.lower():
                        extracted_titles.append(clean_t)

            if extracted_titles:
                history_lines.append(f"Assistant: Provided details for [{', '.join(extracted_titles[:5])}]")
            else:
                first_line = content.split("\n")[0][:120].strip()
                history_lines.append(f"Assistant: {first_line if first_line else 'Provided opportunity information.'}")

    return "\n".join(history_lines)


def strip_thinking_tags(text: str) -> str:
    """Strip out <think>...</think> XML blocks from response."""
    if "</think>" in text and "<think>" not in text:
        text = text.split("</think>")[-1]
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


FAISS_STORE_DIR = Path("faiss_store")


def create_child_chunks(docs: List[Document], chunk_size: int = 500, chunk_overlap: int = 80) -> List[Document]:
    """Split parent documents into child chunks (500 chars, 80 overlap) for vector indexing."""
    child_docs: List[Document] = []
    for doc in docs:
        parent_id = (doc.metadata or {}).get("id") or str(uuid4())
        content = (doc.page_content or "").strip()
        if not content:
            continue
        step = chunk_size - chunk_overlap
        if step <= 0:
            step = chunk_size
        for i in range(0, max(1, len(content)), step):
            sub_text = content[i : i + chunk_size]
            if sub_text.strip():
                meta = dict(doc.metadata or {})
                meta["parent_id"] = parent_id
                child_docs.append(Document(page_content=sub_text, metadata=meta))
    return child_docs


class RAGPipeline:
    """Core RAG Pipeline managing vector indices, retrievers, models, and generation."""

    def __init__(self):
        self.docs: List[Document] = []
        self.parent_docstore: Dict[str, Document] = {}
        self.vectorstore: Optional[FAISS] = None
        self.bm25_retriever: Optional[BM25Retriever] = None
        self.reranker: Optional[CrossEncoder] = None
        self.embeddings: Optional[HuggingFaceEmbeddings] = None
        self.is_initialized: bool = False
        # Semantic answer cache: lives in the same SQLite file/volume as the app DB.
        from backend.database import DB_PATH

        self.answer_cache = SemanticAnswerCache(
            db_path=DB_PATH,
            threshold=settings.model.semantic_cache_similarity_threshold,
            ttl_hours=settings.model.semantic_cache_ttl_hours,
            max_entries=settings.model.semantic_cache_max_entries,
        )

    def _build_parent_docstore(self):
        """Build in-memory docstore mapping parent_id to full Parent Document."""
        self.parent_docstore.clear()
        for doc in self.docs:
            parent_id = (doc.metadata or {}).get("id")
            if parent_id:
                self.parent_docstore[parent_id] = doc

    def resolve_parent_docs(self, child_docs: List[Document]) -> List[Document]:
        """Resolve retrieved child chunks back to full Parent Documents for context generation."""
        parent_docs = []
        seen_ids = set()
        for child in child_docs:
            pid = (child.metadata or {}).get("parent_id")
            if pid and pid in self.parent_docstore:
                if pid not in seen_ids:
                    parent_docs.append(self.parent_docstore[pid])
                    seen_ids.add(pid)
            else:
                name = (child.metadata or {}).get("name")
                if name and name not in seen_ids:
                    parent_docs.append(child)
                    seen_ids.add(name)
        return parent_docs

    def initialize(self):
        """Warm up scrapers, embeddings, persistent FAISS vectorstore, and reranker."""
        print(f"[RAG] Initializing pipeline on device: {DEVICE}")
        if torch and torch.cuda.is_available():
            torch.cuda.empty_cache()

        self.docs = fetch_opportunity_documents()
        self._build_parent_docstore()
        child_docs = create_child_chunks(self.docs, chunk_size=500, chunk_overlap=80)

        if HuggingFaceEmbeddings is not None and FAISS is not None:
            try:
                self.embeddings = HuggingFaceEmbeddings(
                    model_name=EMBEDDING_MODEL,
                    model_kwargs={"device": DEVICE},
                    encode_kwargs={"normalize_embeddings": True},
                )
                if FAISS_STORE_DIR.exists() and (FAISS_STORE_DIR / "index.faiss").is_file():
                    try:
                        self.vectorstore = FAISS.load_local(
                            str(FAISS_STORE_DIR),
                            self.embeddings,
                            allow_dangerous_deserialization=True,
                        )
                        print(f"[RAG] Successfully loaded persistent FAISS index from {FAISS_STORE_DIR}/")
                    except Exception as e:
                        print(f"[RAG] FAISS disk load error ({e}), rebuilding index...")
                        self.vectorstore = FAISS.from_documents(
                            child_docs,
                            self.embeddings,
                            distance_strategy=DistanceStrategy.COSINE,
                        )
                        FAISS_STORE_DIR.mkdir(parents=True, exist_ok=True)
                        self.vectorstore.save_local(str(FAISS_STORE_DIR))
                else:
                    self.vectorstore = FAISS.from_documents(
                        child_docs,
                        self.embeddings,
                        distance_strategy=DistanceStrategy.COSINE,
                    )
                    FAISS_STORE_DIR.mkdir(parents=True, exist_ok=True)
                    self.vectorstore.save_local(str(FAISS_STORE_DIR))
                    print(f"[RAG] Created and saved new FAISS index with {len(child_docs)} child chunks to {FAISS_STORE_DIR}/")
            except Exception as e:
                print(f"[RAG] Vectorstore initialization warning: {e}")

        if BM25Retriever is not None:
            self.bm25_retriever = BM25Retriever.from_documents(child_docs if child_docs else self.docs)
            self.bm25_retriever.k = 3

        if CrossEncoder is not None:
            try:
                self.reranker = CrossEncoder("cross-encoder/ms-marco-MiniLM-L12-v2")
            except Exception as e:
                print(f"[RAG] CrossEncoder initialization warning: {e}")

        self.is_initialized = True
        print(f"[RAG] Pipeline initialized successfully with {len(self.docs)} parent docs and {len(child_docs)} child chunks.")

    def reload_documents(self):
        """Re-fetch documents, rebuild child chunks, and update FAISS index on disk."""
        print("[RAG] Reloading documents and updating persistent vectorstore...")
        try:
            self.answer_cache.bump_epoch()
            print(f"[RAG] Answer cache invalidated (epoch -> {self.answer_cache.epoch}).")
        except Exception as err:
            print(f"[RAG] Answer-cache epoch bump failed: {err}")
        self.docs = fetch_opportunity_documents()
        self._build_parent_docstore()
        child_docs = create_child_chunks(self.docs, chunk_size=500, chunk_overlap=80)

        if self.embeddings and self.docs and FAISS is not None:
            try:
                self.vectorstore = FAISS.from_documents(
                    child_docs,
                    self.embeddings,
                    distance_strategy=DistanceStrategy.COSINE,
                )
                FAISS_STORE_DIR.mkdir(parents=True, exist_ok=True)
                self.vectorstore.save_local(str(FAISS_STORE_DIR))
                print(f"[RAG] Vectorstore updated and saved to {FAISS_STORE_DIR}/ with {len(child_docs)} child chunks.")
            except Exception as err:
                print(f"[RAG] Vectorstore reload error: {err}")

        if BM25Retriever is not None:
            self.bm25_retriever = BM25Retriever.from_documents(child_docs if child_docs else self.docs)
            self.bm25_retriever.k = 3

    def get_llm(
        self,
        provider: str,
        think_mode: bool,
        ollama_base_url: Optional[str] = None,
        llamacpp_server_url: Optional[str] = None,
    ):
        """Instantiate LLM model based on provider, think mode, and server URLs."""
        ollama_url = ollama_base_url or settings.model.ollama_base_url
        llama_url = llamacpp_server_url or settings.model.llamacpp_server_url

        if provider == "Ollama":
            return ChatOllama(
                model="qwen3.5:4b",
                temperature=0.0,
                reasoning=think_mode,
                base_url=ollama_url,
            )
        else:
            # Check if external llama-server is running at llama_url
            use_external_server = False
            try:
                import urllib.request
                req = urllib.request.Request(f"{llama_url.rstrip('/')}/health", headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=1) as response:
                    if response.status in (200, 204):
                        use_external_server = True
            except Exception:
                try:
                    req = urllib.request.Request(f"{llama_url.rstrip('/')}/", headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=1) as response:
                        if response.status in (200, 404, 405):
                            use_external_server = True
                except Exception:
                    use_external_server = False

            if use_external_server:
                print(f"[RAG] Connecting to external llama.cpp server at {llama_url}")
                try:
                    from langchain_openai import ChatOpenAI
                    return ChatOpenAI(
                        base_url=f"{llama_url.rstrip('/')}/v1",
                        api_key="not-needed",
                        model="qwen3.5:4b",
                        temperature=0.0,
                        streaming=True,
                    )
                except Exception as e:
                    print(f"[RAG] Error initializing ChatOpenAI client: {e}, falling back to in-process LlamaCpp")

            print(f"[RAG] Running llama.cpp in-process fallback using model: {MODEL_PATH}")
            return ChatLlamaCpp(
                model_path=MODEL_PATH,
                temperature=0.0,
                n_gpu_layers=-1,
                n_ctx=8192,
                verbose=False,
            )

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
        """Stream response tokens for a given prompt and conversation history."""
        if not self.is_initialized:
            self.initialize()

        # --- Semantic answer cache (single-turn requests only) ---
        # Multi-turn answers depend on conversation context; debug bypasses so the
        # live pipeline stays inspectable.
        config_hash = hashlib.sha1(
            "|".join(
                [
                    provider,
                    str(think),
                    str(rerank),
                    ollama_base_url or settings.model.ollama_base_url,
                    llamacpp_server_url or settings.model.llamacpp_server_url,
                ]
            ).encode("utf-8")
        ).hexdigest()

        cacheable = (
            settings.model.semantic_cache_enabled
            and not history
            and not debug
            and self.embeddings is not None
        )
        query_vec: Optional[List[float]] = None
        if cacheable:
            try:
                query_vec = self.embeddings.embed_query(prompt)
            except Exception as exc:
                print(f"[RAG] Semantic-cache embed failed ({exc}); bypassing cache.")
                cacheable = False

        if cacheable:
            cached = self.answer_cache.lookup(prompt, query_vec, config_hash)
            if cached is not None:
                print(f"[RAG] Answer-cache HIT (similarity={cached['similarity']:.3f}).")
                cached_meta = dict(cached.get("metadata") or {})
                cached_meta["cache_hit"] = True
                yield f"[[METADATA]]{json.dumps(cached_meta)}\n"
                answer_text = cached["answer"]
                for i in range(0, len(answer_text), 32):
                    yield answer_text[i : i + 32]
                return

        if self.vectorstore is not None and self.bm25_retriever is not None:
            dense_retriever = self.vectorstore.as_retriever(
                search_type="mmr",
                search_kwargs={"k": 7, "fetch_k": 15, "lambda_mult": 0.5},
            )
            ensemble_retriever = EnsembleRetriever(
                retrievers=[self.bm25_retriever, dense_retriever],
                weights=[0.4, 0.6],
            )
        else:
            ensemble_retriever = self.bm25_retriever

        self.ensemble_retriever = ensemble_retriever

        # Level 1 Fast Python Router (0.01 ms execution)
        from backend.agent import fast_router, RouteTarget, OpportunityAgentGraph

        route = fast_router(prompt)
        is_opportunity_prompt = (route == RouteTarget.AGENT_TOOLS)

        cleaned_info = ""
        used_tools = []
        initial_docs = []

        if is_opportunity_prompt:
            agent_graph = OpportunityAgentGraph(self)
            agent_res = await agent_graph.run_agent_workflow(
                prompt=prompt,
                history=history,
                provider=provider,
                think=think,
                ollama_base_url=ollama_base_url,
                llamacpp_server_url=llamacpp_server_url,
            )
            cleaned_info = agent_res.get("context_text", "")
            used_tools = agent_res.get("used_tools", [])
            initial_docs = agent_res.get("retrieved_docs", [])
            has_tool_call = agent_res.get("has_tool_call", False)
            is_opportunity_prompt = is_opportunity_prompt and (has_tool_call or bool(cleaned_info.strip()))

        system_template_for_opportunities = (
            "You are Anna, a helpful assistant for people looking for scholarships and internships. "
            "The current date is {current_date}. This date is the actual current/today's date.\n\n"
            "INSTRUCTIONS:\n"
            "1. Answer the user's question based ONLY on the provided Context.\n"
            "2. If the Context doesn't contain the answer, say 'I don't have information about that in my current database.'\n"
            "3. Be concise, friendly, and encouraging.\n"
            "4. For EACH opportunity, strictly format it in this order:\n"
            "   a. Title / Heading (e.g. ### 1. Opportunity Name)\n"
            "   b. Description paragraph (key benefits, eligibility, duration, details)\n"
            "   c. AT THE VERY BOTTOM of that opportunity (after the description), add a single metadata line:\n"
            "      • Deadline: <Date> | Organization: <Name> | Location: <Location> | Type: <Funding/Category>\n"
        )

        system_template_without_opportunities = (
            "You are Anna, a helpful assistant. The current date is {current_date}. This date is the actual current/today's date.\n\n"
            "INSTRUCTIONS:\n"
            "1. Answer the user's question based on your general knowledge if the question is not related to scholarships/internships/opportunities.\n"
            "2. If the question is related to scholarships/internships/opportunities, strictly tell the user that you don't have the information available.\n"
            "3. Be concise, friendly, and playful.\n"
        )

        # Smart template switch: only use opportunity template if relevant context actually exists
        is_opportunity_prompt = is_opportunity_prompt and bool(cleaned_info.strip())

        system_template = system_template_for_opportunities if is_opportunity_prompt else system_template_without_opportunities
        system_msg = SystemMessagePromptTemplate.from_template(system_template)

        human_template_for_opportunities = (
            "Context:\n{relevant_info}\n\n"
            + "-" * 50 + "\n"
            + "Conversation history:\n{conversation_history_str}\n\n"
            + "-" * 50 + "\n"
            + "Question:\n{query}"
        )
        human_template_without_opportunities = (
            "Conversation history:\n{conversation_history_str}\n\n"
            + "-" * 50 + "\n"
            + "Question:\n{query}"
        )

        human_template = human_template_for_opportunities if is_opportunity_prompt else human_template_without_opportunities
        human_msg = HumanMessagePromptTemplate.from_template(human_template)
        chat_prompt = ChatPromptTemplate.from_messages([system_msg, human_msg])

        if is_opportunity_prompt:
            conversation_history_str = compress_history_for_opportunities(history)
        else:
            history_messages = history[-6:] if history else []
            conversation_history_str = (
                "\n".join([f"{m.get('role', 'user')}: {m.get('content', '')}" for m in history_messages])
                if history_messages
                else "(no prior history)"
            )

        input_data = {
            "query": prompt,
            "current_date": CURRENT_DATE_STR,
            "conversation_history_str": conversation_history_str,
        }
        if is_opportunity_prompt:
            input_data["relevant_info"] = cleaned_info

        llm = self.get_llm(
            provider=provider,
            think_mode=think,
            ollama_base_url=ollama_base_url,
            llamacpp_server_url=llamacpp_server_url,
        )
        chain = chat_prompt | llm | StrOutputParser()

        # Format exact System and Human prompts with variables filled in for debugging
        formatted_sys_prompt = system_template.format(current_date=CURRENT_DATE_STR)
        if is_opportunity_prompt:
            formatted_human_prompt = human_template_for_opportunities.format(
                relevant_info=cleaned_info,
                conversation_history_str=conversation_history_str,
                query=prompt
            )
        else:
            formatted_human_prompt = human_template_without_opportunities.format(
                conversation_history_str=conversation_history_str,
                query=prompt
            )

        # Send retrieved document titles metadata header event first as JSON line
        doc_names = []
        for d in initial_docs:
            if isinstance(d, str):
                doc_names.append(d)
            elif hasattr(d, "metadata"):
                doc_names.append(d.metadata.get("name", d.metadata.get("title", "Doc")))

        meta_info = {
            "is_opportunity": is_opportunity_prompt,
            "used_tools": used_tools,
            "initial_docs": doc_names,
            "debug": debug,
        }

        if debug:
            meta_info["debug_info"] = {
                "route_target": route.value if hasattr(route, "value") else str(route),
                "formatted_system_prompt": formatted_sys_prompt,
                "formatted_human_prompt": formatted_human_prompt,
                "filled_variables": input_data,
                "context_text_snippet": cleaned_info[:1500] + ("..." if len(cleaned_info) > 1500 else "")
            }

        yield f"[[METADATA]]{json.dumps(meta_info)}\n"

        generated_chunks: List[str] = []
        for chunk in chain.stream(input_data):
            chunk_str = chunk if isinstance(chunk, str) else getattr(chunk, "content", str(chunk))
            generated_chunks.append(chunk_str)
            yield chunk_str

        if cacheable and generated_chunks:
            try:
                self.answer_cache.store(prompt, query_vec, config_hash, "".join(generated_chunks), meta_info)
                print("[RAG] Answer-cache MISS -> response stored for future reuse.")
            except Exception as exc:
                print(f"[RAG] Answer-cache store failed: {exc}")
