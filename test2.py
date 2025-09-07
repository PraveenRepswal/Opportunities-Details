from main import OpportunitiesCorners
from ollama import Client
import asyncio
from sentence_transformers import SentenceTransformer, util

sitemap_url = 'https://opportunitiescorners.com/post-sitemap.xml'
days_back = 30
threshold = 0.7

def get_data():
    return asyncio.run(
        OpportunitiesCorners(sitemap_url, days_back, threshold).getting_data()
    )

data = get_data()

model = SentenceTransformer('all-MiniLM-L6-v2')
data_embeddings = model.encode(data, convert_to_tensor=True)

def similarity_search(query, top_k=1):
    query_emb = model.encode(query, convert_to_tensor=True)
    hits = util.semantic_search(query_emb, data_embeddings, top_k=top_k)[0]
    return hits

def ollama_client():
    return Client()

def stream_assistant(messages):
    client = ollama_client()
    for chunk in client.chat(
        model="llama3.2",
        messages=messages,
        stream=True
    ):
        yield chunk["message"]["content"]

# Conversation loop
messages = []

while True:
    user_input = input("You: ")
    if user_input.lower() in {"exit", "quit"}:
        break

    # Get the most relevant context for the user input
    hits = similarity_search(user_input, top_k=1)
    # hits is a list of dicts with 'corpus_id' and 'score'
    context_idx = hits[0]['corpus_id']
    context = data[context_idx]

    # Regenerate the system prompt with the new context
    system_prompt = (
        "You are a helpful assistant. Only answer using the context below. "
        "Include eligibility, deadline, duration, location, fees, etc. if they exist.\n\n"
        f"{context}"
    )

    # For each turn, start with the system prompt, then the conversation history
    current_messages = [{"role": "system", "content": system_prompt}] + messages
    current_messages.append({"role": "user", "content": user_input})

    response = ""
    for chunk in stream_assistant(current_messages):
        print(chunk, end="", flush=True)
        response += chunk
    print()
    messages.append({"role": "user", "content": user_input})
    messages.append({"role": "assistant", "content": response})