# from main import OpportunitiesCorners
from test import OpportunitiesForYouth, OpportunitiesCorners
from ollama import ChatResponse, chat
import streamlit as st
import icecream as ic
import os


# sitemap_url = 'https://opportunitiescorners.com/post-sitemap.xml'
days_back=30
threshold=0.7

# @st.cache_data
def get_data():
    return OpportunitiesCorners(days_back, threshold).process(), OpportunitiesForYouth(days_back, threshold).process()

data = get_data()
os.system("clear")   
ic.ic(type(data))
ic.ic("Data from 1st: ",len(data[0]), "Data form 2nd:", len(data[1]))


oc = OpportunitiesCorners(days_back, threshold)
ic.ic("Using sitemap_url:", oc.sitemap_url)
raw = oc.dump_links()
ic.ic("Raw count:", len(raw))

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
button = st.button("Stop", key="stop")


st.write("Raw length:", len(OpportunitiesCorners(days_back, threshold).dump_links()))
st.write("After dedupe:", len(OpportunitiesCorners(days_back, threshold).process()))

# opportunities = st.expander("OpportunitiesCorner")
# with opportunities:
#     st.write(data[0])
# another = st.expander("OpportunitiesForYouth")
# with another:
#     st.write(data[1])


if prompt:
    ic.ic("Data from 1st: ",len(data[0]), "Data form 2nd:", len(data[1]))
    
    st.session_state["messages"].append({"role": "user", "content": prompt})

    with st.chat_message("user"):
        st.write(prompt)

    with st.chat_message("assistant"):
        message = st.write_stream(AI())

        if button:
            st.session_state["messages"].append({"role": "assistant", "content": message})
            st.stop()
        st.session_state["messages"].append({"role": "assistant", "content": message})
