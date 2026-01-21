import asyncio
import datetime
from pathlib import Path
from typing import List
import torch

import streamlit as st
# import icecream as ic
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_core.prompts import ChatPromptTemplate, HumanMessagePromptTemplate, SystemMessagePromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_ollama import ChatOllama
from sentence_transformers import CrossEncoder
# from langchain_text_splitters import RecursiveCharacterTextSplitter
# from langchain_community.document_compressors import FlashrankRerank
import unicodedata
from scraper import CombinedScraper
from langchain_community.vectorstores.utils import DistanceStrategy
import json

# Constants
DAYS_BACK = 30
SCORE_THRESHOLD = 0.2
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
CURRENT_DATE_STR = datetime.date.today().strftime("%d/%B/%Y")

st.set_page_config(page_title="Opportunity Chatbot", page_icon="🎓")
st.title("🎓 Opportunity Chatbot (Anna)")

if "messages" not in st.session_state:
    st.session_state.messages = []

if "count" not in st.session_state:
    st.session_state.count = 0

# Cache the data preparation (Scraping)
@st.cache_resource(show_spinner="Fetching opportunities...")
def get_docs():
    docs: List[Document] = []
    try:
        # Run the scraper
        scraper = CombinedScraper(days_back=DAYS_BACK, threshold=SCORE_THRESHOLD)
        # We need a new event loop for asyncio.run if we are in a thread where loop is already running?
        # Streamlit runs in a separate thread, so asyncio.run should be fine usually.
        raw_data = asyncio.run(scraper.run_all_scrapers())
        if isinstance(raw_data, str) and Path(raw_data).is_file():
            with open(raw_data, 'r', encoding='utf-8') as f:
                raw_data = json.load(f)
        faltu = ["about us", "our services", "contact us", "privacy policy", "terms of service", "our team"]
        for item in raw_data:
            if isinstance(item, dict):
                name = item.get("name", "")
                if name and name.strip().lower() in faltu:
                    continue
                docs.append(
                    Document(
                        page_content=unicodedata.normalize("NFKC", item.get("content", "")),
                        metadata={"name": name},
                    )
                )
    except Exception as exc:
        st.error(f"Failed to fetch remote opportunities: {exc}")
        docs.append(
            Document(
                page_content=(
                    "This is fallback context used when live data is unavailable. "
                    "Answer casual questions naturally even if no specific opportunities are provided."
                ),
                metadata={"name": "fallback"},
            )
        )
    
    if not docs:
        st.warning("Fetched zero documents; using fallback context.")
        docs.append(
            Document(
                page_content=(
                    "This is fallback context used when no recent opportunities meet the selection criteria. "
                    "Answer casual questions naturally even if no specific opportunities are provided."
                ),
                metadata={"name": "fallback"},
            )
        )
    return docs

st.sidebar.title("Chat History")
think = st.sidebar.toggle("Think Mode", value=False)

with st.sidebar.expander("Conversation Log", expanded=True):
    st.write(st.session_state.messages)

# Cache the Vector Store and Models
@st.cache_resource(show_spinner="Initializing AI models...")
def get_vectorstore_and_models(_docs, think_mode: bool):
    import torch
    
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    
    embeddings = HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL, 
        model_kwargs={"device": "cuda"},
        encode_kwargs={"normalize_embeddings": True}
    )

    # splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=120)
    # chunked_docs = splitter.split_documents(_docs)
    
    # Use COSINE strategy
    vectorstore = FAISS.from_documents(
        # chunked_docs,
        _docs, 
        embeddings, 
        distance_strategy=DistanceStrategy.COSINE
    )
    
    reranker = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
    llm_kwargs = {"model": "qwen3:4b", "temperature": 0.0}
    if not think_mode:
        llm_kwargs["reasoning"] = True
    else:
        llm_kwargs["reasoning"] = False
    llm = ChatOllama(**llm_kwargs)
    
    return vectorstore, reranker, llm

# Initialize (build once and reuse across reruns)   
with st.spinner("Initializing AI models..."):
    docs = get_docs()

# if "pipeline" not in st.session_state:
#     with st.spinner("Initializing AI models..."):
#         vectorstore, reranker, llm = get_vectorstore_and_models(docs)
#         st.session_state["pipeline"] = (vectorstore, reranker, llm)
# else:
#     vectorstore, reranker, llm = st.session_state["pipeline"]

vectorstore, reranker, llm = get_vectorstore_and_models(docs, think)
# Helper function for reranking
def rerank_docs(query: str, docs, top_k=5):
    if not docs:
        return []
    pairs = [(query, doc.page_content) for doc in docs]
    scores = reranker.predict(pairs)
    scored_docs = list(zip(docs, scores))
    # filtered_docs = [(doc, score) for doc, score in scored_docs if score > 0]
    scored_docs.sort(key=lambda x: x[1], reverse=True)
    return [doc for doc, _ in scored_docs[:top_k]]


def format_context_snippets(docs, max_chars=1200):
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

# --- Made with AI ---
def strip_thinking_tags(text: str) -> str:
    import re
    if '</think>' in text and '<think>' not in text:
        text = text.split('</think>')[-1]
    # Remove thinking blocks (both opening and closing tags with content)
    cleaned = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE)
    # Clean up any extra whitespace left behind
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()

# chat history display
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])


if prompt := st.chat_input("Ask about scholarships, internships, or opportunities..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Generate response
    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            # 1. Retrieve
            device = "cuda" if torch.cuda.is_available() else "cpu"

            retriever = vectorstore.as_retriever(
                search_type="mmr",
                search_kwargs={"k": 7, "fetch_k": 15, "lambda_mult": 0.5},
                device=device
            )
            try:
                initial_docs = retriever.invoke(prompt)
            except Exception as e:
                st.warning(f"Retrieval error: {e}")
                initial_docs = []

            # Rerank
            if initial_docs:
                reranked_docs = rerank_docs(query=prompt, docs=initial_docs, top_k=5)
            else:
                reranked_docs = []

            st.expander("Reranked docs Titles", expanded=False).write(
                [doc.metadata.get("name", "Unknown") for doc in reranked_docs])
            # Format Context
            if not reranked_docs:
                cleaned_info = ""
            else:
                cleaned_info = format_context_snippets(reranked_docs)

            # Prompt
            system_template = (
                "You are Anna, a helpful assistant for students looking for scholarships and internships. "
                "The current date is {current_date}. This date is the actual current/today's date.\n\n"
                "INSTRUCTIONS:\n"
                "1. Answer the user's question based ONLY on the provided Context.\n"
                "2. If the Context doesn't contain the answer, say 'I don't have information about that in my current database.'\n"
                "3. Be concise, friendly, and encouraging.\n"
                "4. If listing opportunities, always include deadlines, duration, country, remote or onsite status, and key benefits.\n"
                "5. Do not hallucinate or make up information.\n"
                "For EVERY factual claim, strictly include:\n"
                "   Source: <Title>\n"
                "   First 25 words of Details: <First 25 words of content>\n"
                "Strictly follow the above instructions if the question is related to scholarships/internships/opportunities else you can answer from your general knowledge.\n"
                "if the question is not related to scholarships/internships/opportunities you can answer from your general knowledge.\n"
            )

            system_msg = SystemMessagePromptTemplate.from_template(system_template)
            human_template = (
                "Context:\n{relevant_info}\n\n"
                + "-" * 50 + "\n"
                + "Conversation history:\n{conversation_history_str}\n\n"
                + "-" * 50 + "\n"
                + "Question:\n{query}"
            )
            human_msg = HumanMessagePromptTemplate.from_template(human_template)
            chat_prompt = ChatPromptTemplate.from_messages([system_msg, human_msg])

            history_messages = st.session_state.messages[:-1][-6:]
            conversation_history_str = "\n".join(
                [f"{m['role']}: {m['content']}" for m in history_messages]
            ) if history_messages else "(no prior history)"

            input_data = {
                "relevant_info": cleaned_info,
                "query": prompt,
                "current_date": CURRENT_DATE_STR,
                "conversation_history_str": conversation_history_str,
            }

            # 5. Invoke Chain with Streaming
            chain = chat_prompt | llm | StrOutputParser()

            formatted_prompt = chat_prompt.format(**input_data)
            
            st.expander("System Prompt", expanded=False).markdown(system_template.format(current_date=CURRENT_DATE_STR))
            st.expander("Human Prompt Template", expanded=False).markdown(f"Full Prompt:\n{formatted_prompt}")

            response = st.write_stream(chain.stream(input_data))
            
    cleaned_response = strip_thinking_tags(response)
    st.session_state.messages.append({"role": "assistant", "content": cleaned_response})
    st.session_state.count += 1