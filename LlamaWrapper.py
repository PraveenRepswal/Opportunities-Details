from main import OpportunitiesCorners
# from test import OpportunitiesForYouth, OpportunitiesCorners
from ollama import chat, Client
import streamlit as st
import icecream as ic
import os
import asyncio


sitemap_url = 'https://opportunitiescorners.com/post-sitemap.xml'
days_back=30
threshold=0.7

@st.cache_data
def get_data():
    return asyncio.run(OpportunitiesCorners(sitemap_url, days_back, threshold).getting_data())



if "messages" not in st.session_state:
    st.session_state.messages = []


# 1) A helper to split any list into roughly equal‐sized chunks
def chunk_list(lst, chunk_size):
    """Yield successive chunk_size‐sized chunks from lst."""
    for i in range(0, len(lst), chunk_size):
        yield lst[i : i + chunk_size]

# 2) When you fetch your data (list of dicts), choose a chunk size
data = get_data()              # → e.g. a list of 200 dicts
CHUNK_SIZE = 10                # tune this so serialized JSON ≲ ~2000 tokens

# 3) Pre-serialize each dict to JSON (once) for efficiency
import json
json_chunks = [json.dumps(item, ensure_ascii=False) for item in data]

# 4) Split into batches of JSON strings
batches = list(chunk_list(json_chunks, CHUNK_SIZE))

@st.cache_resource
def ollama_client():
    return Client()

# 5) When the user asks a question, iterate through each batch,
#    send only that batch plus the user’s prompt, and collect partial answers.
def answer_question_in_batches(user_question):
    client = ollama_client()
    partials = []
    for i, batch in enumerate(batches, start=1):
        # build a mini system prompt for this batch
        system_content = (
            "You are a helpful assistant. Use ONLY the following data (in JSON):\n\n"
            + "[\n" + ",\n".join(batch) + "\n]"
        )
        messages = [
            {"role": "system",   "content": system_content},
            {"role": "user",     "content": user_question},
        ]

        # call the LLM on this small slice
        resp = client.chat(model="llama3.2", messages=messages, stream=False)
        text = resp.message["content"]
        # resp is one string; append it
        partials.append(text)

        st.write(f"Processed batch {i}/{len(batches)}")  

    # stitch together all the partial answers
    return "\n\n".join(partials)





# data = get_data()

# # if os.name == 'nt':
# #     os.system('cls')
# # # For macOS and Linux
# # else:
# #     os.system('clear') 

# ic.ic(type(data))
# # ic.ic(data)
# # ic.ic("Data from 1st: ",len(data[0]), "Data form 2nd:", len(data[1]), "Total len:", len(data))


# st.title("Test app")

# #History

# if "messages" not in st.session_state:
#     st.session_state.messages = [{'role': 'system', 'content': f'You are a helpful assistant that helps the user by giving answers related to the given data. Only give the answer from this data only. You have to atleast provide important details like eligibility, deadline, duration, location, have application fees or not, location along with other details only from the given data and do not provide the details if not present but do not give wrong information. Here is the given data:\n\n {data}'}]


# if "messages" not in st.session_state:
#     st.session_state.messages = [{'role': 'system', 'content': 'You are a helpful assistant that helps the user by giving answers. Only give the answer correctly.'}]

# @st.cache_resource
# def ollama_client():
#     return Client()

# def AI():

#     client = ollama_client()

#     # Response = chat(model = "gemma3", messages = st.session_state["messages"], stream = True)

#     # for chunk in Response:
#     #     yield chunk["message"]["content"]

#     for chunk in client.chat(model="llama3.2", messages=st.session_state["messages"], stream=True):
#         yield chunk["message"]["content"]


prompt = st.chat_input("Enter your message here")
button = st.button("Stop", key="stop")


# st.write("Raw length:", len(OpportunitiesCorners(sitemap_url, days_back, threshold).dump_links()))
# st.write("After dedupe:", len(OpportunitiesCorners(sitemap_url, days_back, threshold).process()))

# opportunities = st.expander("OpportunitiesCorner")
# with opportunities:
#     st.write(data[0])
# another = st.expander("OpportunitiesForYouth")
# with another:
#     st.write(data[1])

for message in st.session_state.messages:
    if message["role"] == "system":
        continue 
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if prompt:
    # ic.ic("Data from 1st: ",len(data[0]), "Data form 2nd:", len(data[1]))
    
    st.session_state["messages"].append({"role": "user", "content": prompt})

    with st.chat_message("user"):
        st.write(prompt)

    with st.chat_message("assistant"):
        message = st.write(answer_question_in_batches(prompt))

        if button:
            st.session_state["messages"].append({"role": "assistant", "content": message})
            st.stop()
        st.session_state["messages"].append({"role": "assistant", "content": message})

        message_test = st.expander("Message Data")
        with message_test:
            st.write(st.session_state["messages"])
