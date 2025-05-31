from main import OpportunitiesCorners
from ollama import ChatResponse, chat
import streamlit as st
import icecream as ic
import os


sitemap_url = 'https://opportunitiescorners.com/post-sitemap.xml'
days_back=30
threshold=0.7

@st.cache_data
def get_data():
    return OpportunitiesCorners(sitemap_url, days_back, threshold).getting_data()

data = get_data()

if os.name == 'nt':
    os.system('cls')
# For macOS and Linux
else:
    os.system('clear') 

ic.ic(type(data))
# ic.ic(data)
ic.ic("Data from 1st: ",len(data[0]), "Data form 2nd:", len(data[1]), "Total len:", len(data))


st.title("Test app")

#History

if "messages" not in st.session_state:
    st.session_state.messages = [{'role': 'system', 'content': f''}]

SYSTEM = """
You are an extraction engine and strictly follow these rules: 
- Your ONLY job is to output the exact requested information,and if the relevant information is not found then reply nothing else.  
- If the chunk contains no relevant data, you MUST return a zero-length response (i.e. an empty string).  
- DO NOT apologize. DO NOT explain. DO NOT say “there is no…” or suggest other actions if no relevant information is found.  
- Your response must be strictly from the the data the user asked for, or literally nothing.
- If you didn't find relevant information then either reply nothing(that means an empty string) or reply with only a single word 'None'.
"""

def AI(chunk_text: str, user_query: str) -> str:

    prompt = (
        f"User asks: {user_query}\n\n"
        f"Here’s one opportunity:\n{chunk_text}\n\n"
        f"— "
    )
    resp = chat(
        model="llama3.2",
        messages=[
            {"role": "system",  "content": SYSTEM},
            {"role": "user",    "content": prompt}
        ],
        stream=False
    )
    return resp["message"]["content"]

def extract_from_data_chunks(data_chunks, user_query):

    # replies = []
    replies = ""
    processed_chunks = 0
    for entry in data_chunks:
        chunk_text = entry["content"]
        answer = AI(chunk_text, user_query)
        if not answer or answer == "None":
            continue
        else:
            # replies.append(answer)
            replies += answer + "\n\n"
        processed_chunks += 1
        if processed_chunks % 10 == 0:
            ic.ic(f"Processed {processed_chunks} chunks")
    # return "\n\n".join(replies)
    return replies.strip()  

prompt = st.chat_input("Enter your message here")
button = st.button("Stop", key="stop")

st.write("After dedupe:", len(OpportunitiesCorners(sitemap_url, days_back, threshold).process()))


if prompt:
    # ic.ic("Data from 1st: ",len(data[0]), "Data form 2nd:", len(data[1]))
    
    st.session_state["messages"].append({"role": "user", "content": prompt})

    with st.chat_message("user"):
        st.write(prompt)

    with st.chat_message("assistant"):
        message = extract_from_data_chunks(data, prompt)
        st.write(message)

        if button:
            st.session_state["messages"].append({"role": "assistant", "content": message})
            st.stop()
        st.session_state["messages"].append({"role": "assistant", "content": message})
