from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import os
import re
import sys
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncGenerator, Dict, List, Optional
from uuid import uuid4

if TYPE_CHECKING:
    from langchain_community.chat_models import ChatLlamaCpp
    from langchain_community.retrievers import BM25Retriever
    from langchain_community.vectorstores import FAISS
    from langchain_community.vectorstores.utils import DistanceStrategy
    from langchain_core.documents import Document
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import ChatPromptTemplate, HumanMessagePromptTemplate, SystemMessagePromptTemplate
    from langchain_classic.retrievers import EnsembleRetriever
    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_ollama import ChatOllama
    from sentence_transformers import CrossEncoder
else:
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
        class Document:
            def __init__(self, page_content="", metadata=None):
                self.page_content = page_content
                self.metadata = metadata or {}
        BM25Retriever = None
        FAISS = None
        DistanceStrategy = None
        StrOutputParser = None
        ChatPromptTemplate = None
        HumanMessagePromptTemplate = None
        SystemMessagePromptTemplate = None
        EnsembleRetriever = None
        ChatOllama = None

    try:
        from langchain_community.chat_models import ChatLlamaCpp
    except Exception as e:
        print(f"[RAG] Warning: ChatLlamaCpp could not be imported ({e}).")
        ChatLlamaCpp = None

    try:
        from langchain_huggingface import HuggingFaceEmbeddings
    except Exception as e:
        print(f"[RAG] Warning: HuggingFaceEmbeddings could not be imported ({e}).")
        HuggingFaceEmbeddings = None

    try:
        from sentence_transformers import CrossEncoder
    except Exception as e:
        print(f"[RAG] Warning: CrossEncoder could not be imported ({e}).")
        CrossEncoder = None

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

from backend.answer_cache import SemanticAnswerCache
from backend.text_utils import clean_opportunity_text, fix_mojibake, normalize_text  # noqa: F401 (re-exported)
from config import settings
from scraper import CombinedScraper

# Constants
DAYS_BACK = settings.scraper.days_back
SCORE_THRESHOLD = settings.scraper.score_threshold
EMBEDDING_MODEL = settings.model.embedding_model
CURRENT_DATE_STR = datetime.date.today().strftime("%d/%B/%Y")
MODEL_PATH = settings.model.resolved_main_model_path


IGNORED_TITLES = {"about us", "our services", "contact us", "privacy policy", "terms of service", "our team"}


def _item_to_document(item: Dict[str, Any], doc_id: Optional[str] = None) -> Document:
    """Build a parent Document from a DB row or a scraped item (same shape either way)."""
    from backend.schemas import OpportunityProfile
    from backend.metadata_extractor import format_profile_for_vectorstore

    title = item.get("name") or item.get("title") or ""
    url = item.get("url") or item.get("link") or ""
    meta_dict = item.get("metadata") or {}
    prof_dict = item.get("profile") or {}

    doc_meta = {
        "name": title,
        "id": doc_id or str(item.get("id") or uuid4()),
        "url": url,
        "source": item.get("source") or item.get("portal") or "",
        "deadline": item.get("deadline") or prof_dict.get("deadline") or meta_dict.get("deadline"),
        "organization": prof_dict.get("organization") or meta_dict.get("organization"),
        "location": prof_dict.get("location") or meta_dict.get("location"),
        "type": prof_dict.get("type") or meta_dict.get("type"),
        "eligibility": prof_dict.get("eligibility"),
        "benefits": prof_dict.get("benefits"),
        "summary": prof_dict.get("summary"),
    }

    page_text = fix_mojibake(item.get("content") or "")
    if prof_dict.get("summary"):
        try:
            page_text = format_profile_for_vectorstore(OpportunityProfile(**prof_dict), url=url)
        except Exception:
            pass
    return Document(page_content=page_text, metadata=doc_meta)


def _documents_from_items(items: List[Dict[str, Any]]) -> List[Document]:
    docs: List[Document] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = (item.get("name") or item.get("title") or "").strip().lower()
        if title in IGNORED_TITLES:
            continue
        docs.append(_item_to_document(item))
    return docs


def fetch_opportunity_documents() -> List[Document]:
    """Fetch opportunities from SQLite database or CombinedScraper."""
    # 1. Try fetching from SQLite database first
    try:
        from backend.database import list_opportunities
        items = list_opportunities(limit=1000, include_curated=False).get("items", [])
        docs = _documents_from_items(items)
        if docs:
            print(f"[RAG] Loaded {len(docs)} documents from SQLite opportunities table.")
            return docs
    except Exception as exc:
        print(f"[RAG] DB fetch error: {exc}")

    docs: List[Document] = []
    ignored_titles = IGNORED_TITLES

    # 2. Fallback to scraping live
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
                            page_content=fix_mojibake(item.get("content", "")),
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


def rerank_docs(query: str, docs: List[Document], reranker: CrossEncoder, top_k: int = 3) -> List[Document]:
    """Rerank retrieved document candidates using CrossEncoder."""
    if not docs:
        return []
    pairs = [(query, doc.page_content) for doc in docs]
    scores = reranker.predict(pairs)
    scored_docs = list(zip(docs, scores))
    scored_docs.sort(key=lambda x: x[1], reverse=True)
    return [doc for doc, _ in scored_docs[:top_k]]


def format_context_snippets(
    docs: List[Document],
    max_total_chars: int = 4000,
    max_chars_per_doc: int = 1400,
    max_chars: Optional[int] = None,
) -> str:
    """Format document content into a bounded, high-signal context budget (max ~1,000 tokens total)."""
    if max_chars is not None:
        max_total_chars = max_chars

    seen_titles = set()
    chunks = []
    total_chars = 0

    for idx, doc in enumerate(docs, 1):
        meta = doc.metadata or {}
        title = meta.get("name") or meta.get("title") or "Unknown Opportunity"
        title = title.strip()
        if title in seen_titles:
            continue

        raw = (doc.page_content or "").strip()
        if not raw:
            continue

        cleaned = clean_opportunity_text(raw)
        if not cleaned:
            continue

        # Extract structured metadata fields pinned at the top
        meta_parts = []
        dl = meta.get("deadline")
        org = meta.get("organization")
        loc = meta.get("location")
        tp = meta.get("type")
        url = meta.get("url")

        if dl:
            meta_parts.append(f"Deadline: {dl}")
        if org:
            meta_parts.append(f"Organization: {org}")
        if loc:
            meta_parts.append(f"Location: {loc}")
        if tp:
            meta_parts.append(f"Type: {tp}")

        meta_line = " | ".join(meta_parts) if meta_parts else "Not explicitly specified"
        url_line = f"\nLink: {url}" if url else ""
        header = f"[{idx}] {title}\nMetadata: {meta_line}{url_line}\nDescription: "

        # Remaining budget for this entire entry (header + description + delimiter)
        delimiter_len = 2 if chunks else 0
        available_for_entry = max_total_chars - total_chars - delimiter_len
        available_for_snippet = available_for_entry - len(header)

        if available_for_snippet <= 150:
            # Not enough budget remaining for a useful description
            break

        doc_limit = min(max_chars_per_doc, available_for_snippet)
        if len(cleaned) > doc_limit:
            snippet = cleaned[:doc_limit].rsplit(" ", 1)[0] + "..."
        else:
            snippet = cleaned

        entry = f"{header}{snippet}"
        chunks.append(entry)
        total_chars += len(entry) + delimiter_len
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
    """Strip out <think>...</think> XML blocks and [[THINK]] markers from response."""
    if "</think>" in text and "<think>" not in text:
        text = text.split("</think>")[-1]
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    if "<think>" in cleaned:
        cleaned = re.sub(r"<think>.*", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"\[\[THINK\]\].*?(?:\n|$)", "", cleaned)
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
        self.ensemble_retriever: Optional[Any] = None
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

    def _compute_corpus_fingerprint(self, child_docs: List[Document]) -> str:
        """Deterministic hash of parent documents and child chunks to detect corpus changes."""
        hasher = hashlib.sha256()
        hasher.update(f"{len(self.docs)}:{len(child_docs)}:".encode("utf-8"))
        for d in self.docs:
            m = d.metadata or {}
            doc_id = str(m.get("id", ""))
            doc_name = str(m.get("name", ""))
            hasher.update(f"{doc_id}:{doc_name}|".encode("utf-8"))
        return hasher.hexdigest()

    def _build_and_save_vectorstore(self, child_docs: List[Document]) -> None:
        """Build FAISS vectorstore from child documents and persist index + manifest to disk."""
        if not self.embeddings or not child_docs or FAISS is None:
            return
        self.vectorstore = FAISS.from_documents(
            child_docs,
            self.embeddings,
            distance_strategy=DistanceStrategy.COSINE,
        )
        FAISS_STORE_DIR.mkdir(parents=True, exist_ok=True)
        self.vectorstore.save_local(str(FAISS_STORE_DIR))

        manifest = {
            "doc_count": len(self.docs),
            "chunk_count": len(child_docs),
            "fingerprint": self._compute_corpus_fingerprint(child_docs),
            "embedding_model": EMBEDDING_MODEL,
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        manifest_path = FAISS_STORE_DIR / "manifest.json"
        try:
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f)
            print(f"[RAG] Saved FAISS index and manifest with {len(child_docs)} chunks to {FAISS_STORE_DIR}/")
        except Exception as e:
            print(f"[RAG] Warning: Failed to write FAISS manifest ({e})")

    def _build_ensemble_retriever(self) -> Optional[Any]:
        """Construct the hybrid BM25 + FAISS Dense EnsembleRetriever once."""
        if self.vectorstore is not None and self.bm25_retriever is not None and EnsembleRetriever is not None:
            dense_retriever = self.vectorstore.as_retriever(
                search_type="mmr",
                search_kwargs={"k": 3, "fetch_k": 10, "lambda_mult": 0.5},
            )
            self.ensemble_retriever = EnsembleRetriever(
                retrievers=[self.bm25_retriever, dense_retriever],
                weights=[0.4, 0.6],
            )
        else:
            self.ensemble_retriever = self.bm25_retriever
        return self.ensemble_retriever

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
                index_path = FAISS_STORE_DIR / "index.faiss"
                manifest_path = FAISS_STORE_DIR / "manifest.json"
                is_index_valid = False

                if index_path.is_file() and manifest_path.is_file():
                    try:
                        with open(manifest_path, "r", encoding="utf-8") as f:
                            manifest = json.load(f)
                        expected_fp = self._compute_corpus_fingerprint(child_docs)
                        if (
                            manifest.get("fingerprint") == expected_fp
                            and manifest.get("embedding_model") == EMBEDDING_MODEL
                        ):
                            is_index_valid = True
                        else:
                            print("[RAG] Persisted FAISS index is stale (corpus changed). Rebuilding...")
                    except Exception as e:
                        print(f"[RAG] Error reading FAISS manifest ({e}). Rebuilding...")

                if is_index_valid:
                    try:
                        self.vectorstore = FAISS.load_local(
                            str(FAISS_STORE_DIR),
                            self.embeddings,
                            allow_dangerous_deserialization=True,
                        )
                        print(f"[RAG] Successfully loaded verified FAISS index ({len(child_docs)} chunks) from {FAISS_STORE_DIR}/")
                    except Exception as e:
                        print(f"[RAG] FAISS disk load error ({e}), rebuilding index...")
                        self._build_and_save_vectorstore(child_docs)
                else:
                    self._build_and_save_vectorstore(child_docs)
            except Exception as e:
                print(f"[RAG] Vectorstore initialization warning: {e}")

        if BM25Retriever is not None:
            self.bm25_retriever = BM25Retriever.from_documents(child_docs if child_docs else self.docs)
            self.bm25_retriever.k = 4

        if CrossEncoder is not None:
            try:
                self.reranker = CrossEncoder("cross-encoder/ms-marco-MiniLM-L12-v2")
            except Exception as e:
                print(f"[RAG] CrossEncoder initialization warning: {e}")

        self._build_ensemble_retriever()
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
                self._build_and_save_vectorstore(child_docs)
            except Exception as err:
                print(f"[RAG] Vectorstore reload error: {err}")

        if BM25Retriever is not None:
            self.bm25_retriever = BM25Retriever.from_documents(child_docs if child_docs else self.docs)
            self.bm25_retriever.k = 3

        self._build_ensemble_retriever()

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
                reasoning="low" if think_mode else False,
                num_ctx=8192,
                num_predict=4096,
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
                    extra_b = {}
                    if not think_mode:
                        extra_b["reasoning_effort"] = "none"
                    else:
                        extra_b["reasoning_effort"] = "low"
                    return ChatOpenAI(
                        base_url=f"{llama_url.rstrip('/')}/v1",
                        api_key="not-needed",
                        model="qwen3.5:4b",
                        temperature=0.0,
                        streaming=True,
                        max_tokens=4096,
                        extra_body=extra_b,
                        timeout=180.0,
                    )
                except Exception as e:
                    print(f"[RAG] Error initializing ChatOpenAI client: {e}, falling back to in-process LlamaCpp")

            print(f"[RAG] Running llama.cpp in-process fallback using model: {MODEL_PATH}")
            return ChatLlamaCpp(
                model_path=MODEL_PATH,
                temperature=0.0,
                n_gpu_layers=-1,
                n_ctx=8192,
                max_tokens=4096,
                verbose=False,
            )

    async def stream_response(
        self,
        prompt: str,
        history: List[Dict[str, Any]],
        provider: str = "Ollama",
        think: bool = False,
        rerank: bool = False,
        debug: bool = False,
        ollama_base_url: Optional[str] = None,
        llamacpp_server_url: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """Stream response tokens for a given prompt and conversation history."""
        ollama_url = ollama_base_url or settings.model.ollama_base_url
        llama_url = llamacpp_server_url or settings.model.llamacpp_server_url

        if not self.is_initialized:
            self.initialize()

        # Level 0 Direct Curated Roundup Bypass (0ms execution, zero LLM cost, no hallucination)
        from backend.database import get_opportunity_by_title_or_query
        curated_match = get_opportunity_by_title_or_query(prompt)
        if curated_match and curated_match.get("is_curated") == 1:
            from backend.agent import format_curated_roundup
            formatted_answer = format_curated_roundup(curated_match)
            if formatted_answer:
                print(f"[RAG] Curated Roundup direct match HIT for '{prompt}'. Returning direct structured response.")
                meta_info = {
                    "initial_docs": [curated_match.get("title", "Curated Roundup")],
                    "is_opportunity": True,
                    "debug": debug,
                }
                if debug:
                    meta_info["debug_info"] = {
                        "route_target": "curated_roundup_direct",
                        "used_tools": ["curated_roundup_bypass"],
                    }
                yield f"[[METADATA]]{json.dumps(meta_info)}\n"
                chunk_sz = 64
                for i in range(0, len(formatted_answer), chunk_sz):
                    piece = formatted_answer[i : i + chunk_sz]
                    yield f"[[CONTENT]]{json.dumps(piece)}\n"
                return

        # Ensure retriever is ready (initialized once during startup/reload)
        if self.ensemble_retriever is None:
            self._build_ensemble_retriever()

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
                    piece = answer_text[i : i + 32]
                    yield f"[[CONTENT]]{json.dumps(piece)}\n"
                return

        # Agent resolves routing (casual chat -> no tools, otherwise local search / deep dive / web)
        from backend.agent import OpportunityAgentGraph

        agent_res = await OpportunityAgentGraph(self).run_agent_workflow(
            prompt=prompt,
            history=history,
            rerank=rerank,
        )
        cleaned_info = agent_res.get("context_text", "")
        used_tools = agent_res.get("used_tools", [])
        initial_docs = agent_res.get("retrieved_docs", [])
        tool_data = agent_res.get("tool_data", {})
        # Only use the opportunity template if relevant context actually exists
        is_opportunity_prompt = bool(cleaned_info.strip())

        think_instruction = "5. Keep internal thinking concise and proceed directly to provide the full response.\n" if think else ""

        system_template_for_opportunities = (
            "You are Anna, a helpful assistant for people looking for scholarships and internships. "
            "The current/today's date is {current_date}. \n\n"
            "INSTRUCTIONS:\n"
            "1. Answer the user's question based ONLY on the provided Context.\n"
            "2. If the Context doesn't contain the answer, say 'I don't have information about that in my current database.'\n"
            "3. Be concise, friendly, and encouraging.\n"
            "4. For EACH opportunity, format it clearly:\n"
            "   a. Title / Heading (e.g. ### 1. Opportunity Name)\n"
            "   b. Description (key benefits, eligibility, duration, organization, location, deadline, and application details)\n"
            + think_instruction
        )

        system_template_for_web = (
            "You are Anna, a helpful assistant. The current/today's date is {current_date}. \n\n"
            "INSTRUCTIONS:\n"
            "1. Answer the user's question directly, accurately, and concisely based on the provided Context / Web Search Results.\n"
            "2. If the Context contains the answer, summarize the facts clearly without inventing information.\n"
            "3. If the Context does not contain the answer, say 'I don't have information about that.'\n"
            "4. Be friendly and helpful.\n"
            + think_instruction
        )

        system_template_without_opportunities = (
            "You are Anna, a helpful assistant. The current date is {current_date}. This date is the actual current/today's date.\n\n"
            "INSTRUCTIONS:\n"
            "1. Answer the user's question based on your general knowledge if the question is not related to scholarships/internships/opportunities.\n"
            "2. If the question is related to scholarships/internships/opportunities, strictly tell the user that you don't have the information available.\n"
            "3. Be concise, friendly, and playful.\n"
            + think_instruction
        )

        if "search_live_web" in used_tools:
            system_template = system_template_for_web
        elif is_opportunity_prompt:
            system_template = system_template_for_opportunities
        else:
            system_template = system_template_without_opportunities
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
            "tool_data": tool_data,
            "debug": debug,
        }

        if debug:
            meta_info["debug_info"] = {
                "route_target": "agent_tools" if used_tools else "direct_chat",
                "formatted_system_prompt": formatted_sys_prompt,
                "formatted_human_prompt": formatted_human_prompt,
                "filled_variables": input_data,
                "context_text_snippet": cleaned_info[:1500] + ("..." if len(cleaned_info) > 1500 else "")
            }

        yield f"[[METADATA]]{json.dumps(meta_info)}\n"

        generated_chunks: List[str] = []
        try:
            # Check if external llama-server is running when provider is LLamaCPP
            use_external = False
            if provider != "Ollama":
                try:
                    import urllib.request
                    req = urllib.request.Request(f"{llama_url.rstrip('/')}/health", headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=1) as resp:
                        if resp.status in (200, 204):
                            use_external = True
                except Exception:
                    try:
                        req = urllib.request.Request(f"{llama_url.rstrip('/')}/", headers={"User-Agent": "Mozilla/5.0"})
                        with urllib.request.urlopen(req, timeout=1) as resp:
                            if resp.status in (200, 404, 405):
                                use_external = True
                    except Exception:
                        use_external = False

            if use_external and provider != "Ollama":
                from openai import AsyncOpenAI
                client = AsyncOpenAI(base_url=f"{llama_url.rstrip('/')}/v1", api_key="not-needed", timeout=180.0)
                openai_messages = [
                    {"role": "system", "content": formatted_sys_prompt},
                    {"role": "user", "content": formatted_human_prompt},
                ]
                budget = settings.model.reasoning_budget if think else 0
                extra_kwargs = {
                    "extra_body": {
                        "reasoning_budget": budget,
                        "chat_template_kwargs": {
                            "enable_thinking": bool(think),
                        },
                    }
                }
                if not think:
                    extra_kwargs["reasoning_effort"] = "none"
                else:
                    extra_kwargs["reasoning_effort"] = "low"

                stream = await client.chat.completions.create(
                    model="qwen3.5:4b",
                    messages=openai_messages,
                    stream=True,
                    max_tokens=4096,
                    temperature=0.0,
                    **extra_kwargs,
                )
                async for chunk in stream:
                    delta = chunk.choices[0].delta
                    reasoning = getattr(delta, "reasoning_content", None)
                    if reasoning and think:
                        yield f"[[THINK]]{json.dumps(reasoning)}\n"
                    if delta.content:
                        generated_chunks.append(delta.content)
                        yield f"[[CONTENT]]{json.dumps(delta.content)}\n"
            else:
                llm = self.get_llm(
                    provider=provider,
                    think_mode=think,
                    ollama_base_url=ollama_base_url,
                    llamacpp_server_url=llamacpp_server_url,
                )
                chain = chat_prompt | llm | StrOutputParser()
                in_think = False
                think_chars_emitted = 0
                max_think_chars = (settings.model.reasoning_budget or 1000) * 4
                buf = ""
                async for raw_chunk in chain.astream(input_data):
                    chunk_text = raw_chunk if isinstance(raw_chunk, str) else getattr(raw_chunk, "content", str(raw_chunk))
                    if not chunk_text:
                        continue
                    buf += chunk_text
                    while buf:
                        if not in_think:
                            if "<think>" in buf:
                                idx = buf.find("<think>")
                                normal_part = buf[:idx]
                                if normal_part:
                                    generated_chunks.append(normal_part)
                                    yield f"[[CONTENT]]{json.dumps(normal_part)}\n"
                                in_think = True
                                buf = buf[idx + len("<think>"):]
                            else:
                                possible_tag = False
                                for i in range(1, len("<think>")):
                                    if buf.endswith("<think>"[:i]):
                                        possible_tag = True
                                        normal_part = buf[:-i]
                                        if normal_part:
                                            generated_chunks.append(normal_part)
                                            yield f"[[CONTENT]]{json.dumps(normal_part)}\n"
                                        buf = buf[-i:]
                                        break
                                if not possible_tag:
                                    generated_chunks.append(buf)
                                    yield f"[[CONTENT]]{json.dumps(buf)}\n"
                                    buf = ""
                                break
                        else:
                            if "</think>" in buf:
                                idx = buf.find("</think>")
                                think_part = buf[:idx]
                                if think_part and think:
                                    think_chars_emitted += len(think_part)
                                    yield f"[[THINK]]{json.dumps(think_part)}\n"
                                in_think = False
                                buf = buf[idx + len("</think>"):]
                            else:
                                possible_tag = False
                                for i in range(1, len("</think>")):
                                    if buf.endswith("</think>"[:i]):
                                        possible_tag = True
                                        think_part = buf[:-i]
                                        if think_part and think:
                                            think_chars_emitted += len(think_part)
                                            yield f"[[THINK]]{json.dumps(think_part)}\n"
                                        buf = buf[-i:]
                                        break
                                if not possible_tag:
                                    if think:
                                        think_chars_emitted += len(buf)
                                        yield f"[[THINK]]{json.dumps(buf)}\n"
                                    buf = ""
                                if think_chars_emitted >= max_think_chars:
                                    in_think = False
                                break
                if buf:
                    if in_think:
                        if think:
                            yield f"[[THINK]]{json.dumps(buf)}\n"
                    else:
                        generated_chunks.append(buf)
                        yield f"[[CONTENT]]{json.dumps(buf)}\n"
        except Exception as exc:
            err_name = type(exc).__name__
            err_str = str(exc)
            if "ConnectError" in err_name or "10061" in err_str or "ConnectionRefused" in err_name:
                srv_target = f"Ollama at {ollama_url}" if provider == "Ollama" else f"llama-server at {llama_url}"
                msg = (
                    f"\n\n> ⚠️ **Inference Connection Failed**: Could not connect to **{srv_target}**.\n"
                    f"> - If using **Ollama**, make sure it is running (`ollama serve` or open the Ollama app).\n"
                    f"> - If using **llama.cpp**, make sure `llama-server` is running or switch the **Model Provider** in the sidebar."
                )
                print(f"[RAG] Connection failed to {srv_target}: {exc}")
                yield f"[[CONTENT]]{json.dumps(msg)}\n"
            else:
                print(f"[RAG] Generation error: {exc}")
                yield f"[[CONTENT]]{json.dumps(f'\\n\\n> ⚠️ **Inference Error**: {exc}')}\n"

        if not generated_chunks:
            fallback_msg = "\n\n> ⚠️ *The model finished thinking but produced no final output (token budget reached). Please try asking again or toggle off Think mode.*"
            generated_chunks.append(fallback_msg)
            yield f"[[CONTENT]]{json.dumps(fallback_msg)}\n"

        full_ans = "".join(generated_chunks)
        should_store = (
            cacheable
            and bool(full_ans.strip())
            and "search_live_web" not in used_tools
            and "don't have information about that" not in full_ans.lower()
            and "not have information about that" not in full_ans.lower()
            and "inference error" not in full_ans.lower()
            and "connection failed" not in full_ans.lower()
        )

        if should_store:
            try:
                self.answer_cache.store(prompt, query_vec, config_hash, full_ans, meta_info)
                print("[RAG] Answer-cache MISS -> response stored for future reuse.")
            except Exception as exc:
                print(f"[RAG] Answer-cache store failed: {exc}")
