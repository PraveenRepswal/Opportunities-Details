from main import OpportunitiesCorners
from ollama import Client
import streamlit as st
import asyncio
from sentence_transformers import SentenceTransformer, util

# Config
sitemap_url = 'https://opportunitiescorners.com/post-sitemap.xml'
days_back = 30
threshold = 0.7

# 1) Load & cache your opportunity data
@st.cache_data
def get_data():
    return asyncio.run(
        OpportunitiesCorners(sitemap_url, days_back, threshold)
          .getting_data()
    )

data = get_data()

# 2) Embed them once
model = SentenceTransformer('all-MiniLM-L6-v2')
data_embeddings = model.encode(data, convert_to_tensor=True)

# 3) Streamlit UI
st.title("Test app")

# 4) Ollama client (cached)
@st.cache_resource
def ollama_client():
    return Client()

def stream_assistant():
    client = ollama_client()
    for chunk in client.chat(
        model="llama3.2",
        messages=st.session_state.messages,
        stream=True
    ):
        # each chunk is a dict: {"message": {"content": "..."}}
        yield chunk["message"]["content"]

# 5) Initialize the chat history with the top-1 context
if "messages" not in st.session_state:
    # dummy search to get the very first context
    dummy_emb = model.encode("", convert_to_tensor=True)
    top_hit = util.semantic_search(dummy_emb, data_embeddings, top_k=1)[0][0]
    context_text = top_hit["text"]
    
    st.session_state.messages = [{
        "role": "system",
        "content": (
            "You are a helpful assistant. Only answer using the context below. "
            "Include eligibility, deadline, duration, location, fees, etc. if they exist.\n\n"
            f"{context_text}"
        )
    }]

# 6) Accept user input
prompt = st.chat_input("Enter your message here")
if prompt:
    # a) record & display the user's message
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.write(prompt)

    # b) run semantic search *for this* prompt, then update the system context
    prompt_emb = model.encode(prompt, convert_to_tensor=True)
    hit = util.semantic_search(prompt_emb, data_embeddings, top_k=1)[0][0]
    new_context = hit["text"]
    st.session_state.messages[0]["content"] = (
        "You are a helpful assistant. Only answer using the context below.\n\n"
        f"{new_context}"
    )

    # c) stream & display the assistant’s reply
    full_reply = ""
    with st.chat_message("assistant"):
        for piece in stream_assistant():
            st.write(piece)        # write each chunk as it arrives
            full_reply += piece    # accumulate

    # d) save it in history
    st.session_state.messages.append({
        "role": "assistant",
        "content": full_reply
    })
