import langchain
from langchain.docstore.document import Document
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from main import OpportunitiesCorners
import icecream as ic
import streamlit as st
from langchain_ollama import ChatOllama
import asyncio
from langchain_core.prompts import PromptTemplate

sitemap_url = 'https://opportunitiescorners.com/post-sitemap.xml'
days_back = 30
threshold = 0.7
docs = []

@st.cache_data()
def docs_preparation():
    raw_data = asyncio.run(OpportunitiesCorners(sitemap_url, days_back, threshold).getting_data())
    for i in raw_data:
        docs.append(Document(page_content=i["content"] if isinstance(i, dict) else "", metadata={ "name" : i["name"]}))
    return docs
# ic(docs)

docs = docs_preparation()
ic.ic("docs prepared")

embeddings = HuggingFaceEmbeddings(model_name= "sentence-transformers/all-MiniLM-L6-v2")
vectorstore = FAISS.from_documents(docs, embeddings)

st.session_state["vectorstore"] = vectorstore

llm = ChatOllama(
    model="gemma3"
)

st.title("Test app")

query = st.chat_input("Write your question")
if query:
    queryembeddings = embeddings.embed_query(query)
    st.expander("Query Embeddings").write(queryembeddings)
    # docs = vectorstore.similarity_search(query, k = 3)
    vectorstore.as_retriever()
    retriver = st.session_state["vectorstore"].as_retriever(search_kwargs={"k": 3})
    relevant_info = retriver.get_relevant_documents(queryembeddings)


    prompt = PromptTemplate(
        [
            ("system", "You are a helpful assistant. Only answer using the context below. If you don't know the answer, just say that you don't know, don't try to make up an answer.\n\nContext: {relevant_info}"),
            ("user", "Question: {query}")
        
        ]
    )
   
   
   
   
# messages = [
#         {"role": "system", "content": "You are a helpful assistant. Only answer using the context below. If you don't know the answer, just say that you don't know, don't try to make up an answer."},
#         {"role": "user", "content": f"Context: {relevant_info}\n\nQuestion: {query}"}]

    chain = prompt | llm
    response = chain.invoke({"relevant_info": relevant_info, "query": query})

    st.write(response)
