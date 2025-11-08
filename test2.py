import asyncio
import datetime
import warnings
from functools import lru_cache
from pathlib import Path
from typing import List, Dict, Any

import icecream as ic
from langchain.docstore.document import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS

from langchain_core.prompts import ChatPromptTemplate, HumanMessagePromptTemplate, SystemMessagePromptTemplate

from langchain_core.output_parsers import StrOutputParser
from main import OpportunitiesCorners  
from langchain_ollama import ChatOllama

SITEMAP_URL = "https://opportunitiescorners.com/post-sitemap.xml"
DAYS_BACK = 30
SCORE_THRESHOLD = 0.2  # used by retriever
FETCH_K = 100
K_RETURN = 50
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
FAISS_INDEX_DIR = Path("faiss_index")
CURRENT_DATE_STR = datetime.date.today().strftime("%d/%B/%Y")


# @st.cache_data()
@lru_cache(maxsize=1)
def docs_preparation():
    docs: List[Document] = []
    try:
        raw_data = asyncio.run(
            OpportunitiesCorners(SITEMAP_URL, DAYS_BACK, SCORE_THRESHOLD).getting_data()
        )
        for item in raw_data:
            if isinstance(item, dict):
                docs.append(
                    Document(
                        page_content=item.get("content", ""),
                        metadata={"name": item.get("name", "")},
                    )
                )
    except Exception as exc:  # pragma: no cover - network fallback
        ic.ic("Failed to fetch remote opportunities; using fallback context.", exc)
        docs.append(
            Document(
                page_content=(
                    "This is fallback context used when live data is unavailable. "
                    "Answer casual questions naturally even if no specific opportunities are provided."
                ),
                metadata={"name": "fallback"},
            )
        )
    return docs
# ic(docs)

warnings.filterwarnings(
    "ignore",
    message="Relevance scores must be between 0 and 1",
    category=UserWarning,
)

docs = docs_preparation()
# ic.ic(type(docs))
# ic.ic(docs)
ic.ic("docs prepared")
# with open("Langchain docs.txt", "w") as f:
#     f.write(str(docs))

# Silence verbose warning that prints entire (Document, score) tuples


embeddings = HuggingFaceEmbeddings(model_name= EMBEDDING_MODEL)
vectorstore = FAISS.from_documents(docs, embeddings, normalize_L2 = True)

# vectorstore.save_local("faiss_index")

# new_vectorstore  = FAISS.load_local("faiss_index", embeddings)

# st.session_state["vectorstore"] = vectorstore

llm = ChatOllama(model="gemma3")

# st.title("Test app")

# query = st.chat_input("Write your question")
while True:
    query = input("Write your question: ")
    if query == "exit":
        break
    else:
        # Build a retriever. Threshold mode returns all docs above score_threshold (up to k from fetch_k candidates).
        # queryembeddings = embeddings.embed_query(query)
        retriver = vectorstore.as_retriever(
            search_type="similarity_score_threshold",
            search_kwargs={"score_threshold": 0.2, "k": 50, "fetch_k": 100},
        )
        # retriver = vectorstore.as_retriever(
        #     search_kwargs={"k": 3}
        # )

        # Get docs with the raw query text (do NOT pass embeddings here)
        # retrieved_docs = retriver.invoke(query)
        # ic.ic(f"Retrieved {len(retrieved_docs)} documents above threshold")

        # Turn Document objects into readable context for the LLM
        # def _fmt(doc: Document) -> str:
        #     name = (doc.metadata or {}).get("name", "")
        #     snippet = (doc.page_content or "").strip().replace("\n", " ")
        #     if len(snippet) > 500:
        #         snippet = snippet[:500] + "..."
        #     return f"Title: {name}\nContent: {snippet}"

        # if not retrieved_docs:
        #     ic.ic("No relevant documents found at current threshold; consider lowering score_threshold or using plain 'similarity' mode with higher k.")

        relevant_info = retriver.invoke(query)

        def doc2str(docs_):
            parts = []
            for doc in docs_:
                title = (doc.metadata or {}).get("name", "")
                snippet = (doc.page_content or "").strip().replace("\n", " ")
                # if len(snippet) > 600:
                #     snippet = snippet[:600] + "..."
                parts.append(f"Title: {title}\nContent: {snippet}")
            return "\n\n".join(parts)

        ic.ic(f"Retrieved {len(relevant_info)} docs with threshold")

        if not relevant_info:
            ic.ic("No docs passed threshold; falling back to plain similarity (k=10)")
            relevant_info = vectorstore.similarity_search(query, k=10)
            ic.ic(f"Similarity returned {len(relevant_info)} docs")

        cleaned_info = doc2str(relevant_info)
        # ic.ic(cleaned_info)

        # Build prompt and chain inside the branch
        system_template = (
        "You are Anna, a helpful AI assistant created by Pankaj. Always refer to yourself as Anna and never claim to be any other system or company.\n"
        "The current date is {current_date}.\n"
        "When the user chats casually and their question does not require information from the context, reply in a warm, playful, slightly flirty human tone and avoid mentioning the context.\n"
        "When the user asks about opportunities or anything that depends on factual details or is relavent to the context, use ONLY the information in the provided context to answer.\n"
        "If the user asks for factual details that are not in the context, reply exactly: \"I don't know based on the provided context.\" Do not use this fallback for casual chit-chat.\n"
        "If the user asks about multiple programs, list each program with brief important information like deadline, eligibility, host country, benefits, and perks.\n"
        "Never say that you are a language model, never refer to your training data, and never invent details.\n"
        "Do not include programs whose deadlines have passed the current date unless the user explicitly asks."
        )
        system_msg = SystemMessagePromptTemplate.from_template(system_template)
        human_msg = HumanMessagePromptTemplate.from_template(
            "\nContext:\n{relevant_info}\n\nQuestion:\n{query}\n\nFollow these rules in your response:\n"
            "1. Begin by introducing yourself as Anna.\n"
            "2. For casual questions that do not need the context, respond in a warm, playful, slightly flirty tone and do not mention the context.\n"
        )
        prompt = ChatPromptTemplate.from_messages([system_msg, human_msg])

    # System message and human message templates (these wrapper helpers are common)
    # system_msg = SystemMessagePromptTemplate.from_template(system_template)
    # human_msg = HumanMessagePromptTemplate.from_template("Question: {query}")

    # prompt = ChatPromptTemplate.from_messages([system_msg, human_msg])

        input_data = {
            "relevant_info": cleaned_info,
            "query": query,
            "current_date": CURRENT_DATE_STR
        }

        # DEBUG: show rendered prompt so we know exactly what LLM sees
        rendered = prompt.invoke(input_data)
        # tokenizer = gm.text.Gemma3Tokenizer()


        try:
            print("\n----- RENDERED PROMPT (to LLM) -----")
            print(rendered.to_string())
            # ic.ic(len(tokenizer.encode(rendered.to_string())))
            print("----- END PROMPT -----\n")
        except Exception:
            pass

        chain = prompt | llm | StrOutputParser()
        response = chain.invoke(input_data)
        ic.ic(response)
