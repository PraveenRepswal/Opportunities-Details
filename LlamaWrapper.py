from main import dump_links
from ollama import ChatResponse, chat
import streamlit as st
import torch

sitemap_url = 'https://opportunitiescorners.com/post-sitemap.xml'


@st.cache_data
def get_data():
    return dump_links(sitemap_url, days_back=30)

data = get_data()   

st.title("Test app")

#History

if "messages" not in st.session_state:
    st.session_state.messages = [{'role': 'system', 'content': f'You are a helpful assistant that helps the user by giving answers related to {data}'}]



def AI():

    # if torch.cuda.is_available():
    #     # Set the global PyTorch device to GPU
    #     device = torch.device("cuda")
    #     #torch.set_default_tensor_type("torch.cuda.FloatTensor")
    # else:
    #     # Use CPU if no GPU available
    #     device = torch.device("cpu")

    Response = chat(model = "gemma3", messages = st.session_state["messages"], stream = True)

    for chunk in Response:
        yield chunk["message"]["content"]


prompt = st.chat_input("Enter your message here")
if prompt:
    
    st.session_state["messages"].append({"role": "user", "content": prompt})

    with st.chat_message("user"):
        st.write(prompt)

    with st.chat_message("assistant"):
        message = st.write_stream(AI())
        st.session_state["messages"].append({"role": "assistant", "content": message})
