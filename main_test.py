import streamlit as st
import asyncio
import icecream as ic
from main import OpportunitiesCorners
from ollama import Client
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np
import re
from rapidfuzz import fuzz
import dateparser
from datetime import datetime

# Configuration
sitemap_url = 'https://opportunitiescorners.com/post-sitemap.xml'
days_back = 30
threshold = 0.7

# Initialize session state
if "messages" not in st.session_state:
    st.session_state.messages = []

if "documents" not in st.session_state:
    st.session_state.documents = []

if "tfidf_vectorizer" not in st.session_state:
    st.session_state.tfidf_vectorizer = None

if "tfidf_matrix" not in st.session_state:
    st.session_state.tfidf_matrix = None

@st.cache_data
def get_data():
    """Fetch and process opportunities data"""
    return asyncio.run(OpportunitiesCorners(sitemap_url, days_back, threshold).getting_data())

def extract_deadline(content):
    """Extract deadline from content"""
    deadline_patterns = [
        r"deadline:?\s*([^\n\r\.\;]{5,30})",
        r"apply by:?\s*([^\n\r\.\;]{5,30})",
        r"closing date:?\s*([^\n\r\.\;]{5,30})",
        r"due date:?\s*([^\n\r\.\;]{5,30})"
    ]
    
    # Check for rolling deadlines
    if re.search(r"rolling basis|ongoing|no deadline", content.lower()):
        return {"text": "Rolling basis", "date": None, "is_rolling": True}
    
    for pattern in deadline_patterns:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            deadline_text = match.group(1).strip()
            try:
                parsed_date = dateparser.parse(deadline_text)
                return {
                    "text": deadline_text,
                    "date": parsed_date,
                    "is_rolling": False
                }
            except:
                return {
                    "text": deadline_text,
                    "date": None,
                    "is_rolling": False
                }
    
    return {"text": None, "date": None, "is_rolling": False}

def preprocess_documents(raw_data):
    """Convert raw data to document format with metadata"""
    documents = []
    for i, item in enumerate(raw_data):
        # Use the name field from the dictionary instead of extracting from content
        title = item['name'].title() if item['name'] else "Opportunity"
        deadline_info = extract_deadline(item['content'])
        
        doc = {
            'doc_id': i,
            'title': title,
            'url': item['url'],
            'content': item['content'],
            'deadline': deadline_info,
            # Create searchable text (title + content for better matching)
            'searchable_text': f"{title} {item['content']}"
        }
        documents.append(doc)
    
    return documents

def build_tfidf_index(documents):
    """Build TF-IDF index for lexical search"""
    texts = [doc['searchable_text'] for doc in documents]
    
    vectorizer = TfidfVectorizer(
        ngram_range=(1, 2),  # Use unigrams and bigrams
        max_features=10000,
        stop_words='english',
        lowercase=True,
        min_df=1,
        max_df=0.8
    )
    
    tfidf_matrix = vectorizer.fit_transform(texts)
    return vectorizer, tfidf_matrix

def search_by_title(query, documents, top_k=5):
    """Search by title using fuzzy matching"""
    results = []
    for doc in documents:
        score = fuzz.partial_ratio(query.lower(), doc['title'].lower()) / 100.0
        if score > 0.3:  # Threshold for title matching
            results.append({
                'doc': doc,
                'score': score,
                'match_type': 'title'
            })
    
    # Sort by score and return top_k
    results.sort(key=lambda x: x['score'], reverse=True)
    return results[:top_k]

def search_by_content(query, documents, vectorizer, tfidf_matrix, top_k=5):
    """Search by content using TF-IDF"""
    query_vec = vectorizer.transform([query])
    similarities = cosine_similarity(query_vec, tfidf_matrix).flatten()
    
    # Get top indices
    top_indices = np.argsort(similarities)[::-1][:top_k]
    
    results = []
    for idx in top_indices:
        if similarities[idx] > 0.01:  # Minimum similarity threshold
            results.append({
                'doc': documents[idx],
                'score': similarities[idx],
                'match_type': 'content'
            })
    
    return results

def filter_by_deadline(documents, date_str):
    """Filter documents by deadline"""
    try:
        target_date = dateparser.parse(date_str)
        if not target_date:
            return []
        
        filtered = []
        for doc in documents:
            deadline_info = doc['deadline']
            if deadline_info['date'] and deadline_info['date'] <= target_date:
                filtered.append({
                    'doc': doc,
                    'score': 1.0,
                    'match_type': 'deadline_filter'
                })
        
        # Sort by deadline date
        filtered.sort(key=lambda x: x['doc']['deadline']['date'] or datetime.min)
        return filtered
        
    except Exception as e:
        st.error(f"Error parsing date: {e}")
        return []

def detect_query_intent(query):
    """Detect what type of query this is"""
    query_lower = query.lower()
    
    # List all opportunities intent
    if any(phrase in query_lower for phrase in ['list all', 'show all', 'all opportunities', 'list the name of all']):
        return {'type': 'list_all'}
    
    # Deadline filter intent
    deadline_patterns = [
        r"deadline.*before\s+([^\s]+(?:\s+\d{4})?)",
        r"due.*before\s+([^\s]+(?:\s+\d{4})?)",
        r"apply.*before\s+([^\s]+(?:\s+\d{4})?)"
    ]
    
    for pattern in deadline_patterns:
        match = re.search(pattern, query_lower)
        if match:
            return {'type': 'deadline_filter', 'date': match.group(1)}
    
    # Title search intent
    if any(phrase in query_lower for phrase in ['tell me about', 'what is', 'details about', 'information about']):
        # Extract the opportunity name
        for phrase in ['tell me about', 'what is', 'details about', 'information about']:
            if phrase in query_lower:
                title_query = query_lower.replace(phrase, '').strip()
                return {'type': 'title_search', 'title': title_query}
    
    # Default to content search
    return {'type': 'content_search'}

def list_all_opportunities(documents):
    """Return all opportunities with basic info"""
    results = []
    for doc in documents:
        results.append({
            'doc': doc,
            'score': 1.0,
            'match_type': 'list_all'
        })
    return results

def perform_search(query, documents, vectorizer, tfidf_matrix, top_k=5):
    """Main search function that routes based on intent"""
    intent = detect_query_intent(query)
    
    if intent['type'] == 'list_all':
        return list_all_opportunities(documents)
    elif intent['type'] == 'deadline_filter':
        return filter_by_deadline(documents, intent['date'])
    elif intent['type'] == 'title_search':
        return search_by_title(intent['title'], documents, top_k)
    else:
        # Try title search first, then content search
        title_results = search_by_title(query, documents, top_k//2)
        content_results = search_by_content(query, documents, vectorizer, tfidf_matrix, top_k)
        
        # Combine and deduplicate
        all_results = title_results + content_results
        seen_docs = set()
        unique_results = []
        
        for result in all_results:
            doc_id = result['doc']['doc_id']
            if doc_id not in seen_docs:
                seen_docs.add(doc_id)
                unique_results.append(result)
        
        # Sort by score
        unique_results.sort(key=lambda x: x['score'], reverse=True)
        return unique_results[:top_k]

def format_context_for_llm(search_results, max_context_length=4000):
    """Format search results into context for LLM"""
    if not search_results:
        return "No relevant opportunities found."
    
    # Special handling for list_all intent
    if search_results and search_results[0]['match_type'] == 'list_all':
        context_parts = ["Here are all available opportunities:\n"]
        for i, result in enumerate(search_results):
            doc = result['doc']
            deadline_text = "Not specified"
            if doc['deadline']['is_rolling']:
                deadline_text = "Rolling basis"
            elif doc['deadline']['text']:
                deadline_text = doc['deadline']['text']
            
            # Just list names with basic info for "list all" queries
            context_parts.append(f"{i+1}. {doc['title']} (Deadline: {deadline_text})")
        
        return "\n".join(context_parts)
    
    # Regular context formatting for other search types
    context_parts = []
    current_length = 0
    
    for i, result in enumerate(search_results):
        doc = result['doc']
        deadline_text = "Not specified"
        if doc['deadline']['is_rolling']:
            deadline_text = "Rolling basis (ongoing applications)"
        elif doc['deadline']['text']:
            deadline_text = doc['deadline']['text']
        
        context_part = f"""
**Opportunity {i+1}: {doc['title']}**
- URL: {doc['url']}
- Deadline: {deadline_text}
- Details: {doc['content'][:1000]}...

"""
        
        if current_length + len(context_part) > max_context_length:
            break
            
        context_parts.append(context_part)
        current_length += len(context_part)
    
    return "\n".join(context_parts)

@st.cache_resource
def ollama_client():
    return Client()

def stream_assistant(messages):
    """Stream response from Ollama"""
    client = ollama_client()
    for chunk in client.chat(model="llama3.2", messages=messages, stream=True):
        yield chunk["message"]["content"]

# Main Streamlit App
st.title("Basic RAG - Opportunities Search")

# Load and process data
if not st.session_state.documents:
    with st.spinner("Loading opportunities data..."):
        raw_data = get_data()
        st.session_state.documents = preprocess_documents(raw_data)
        
        # Build search index
        vectorizer, tfidf_matrix = build_tfidf_index(st.session_state.documents)
        st.session_state.tfidf_vectorizer = vectorizer
        st.session_state.tfidf_matrix = tfidf_matrix
        
        st.success(f"Loaded {len(st.session_state.documents)} opportunities!")

# Display chat history
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# Chat input
if prompt := st.chat_input("Ask about opportunities (e.g., 'scholarships in Canada' or 'deadline before July 2025')"):
    # Add user message
    st.session_state.messages.append({"role": "user", "content": prompt})
    
    with st.chat_message("user"):
        st.markdown(prompt)
    
    # Perform search
    search_results = perform_search(
        prompt, 
        st.session_state.documents,
        st.session_state.tfidf_vectorizer,
        st.session_state.tfidf_matrix,
        top_k=5
    )
    
    # Format context
    context = format_context_for_llm(search_results)
    
    # Create system message with context
    system_message = {
        "role": "system",
        "content": f"""You are a helpful assistant that answers questions about opportunities and scholarships. 
        Use ONLY the information provided in the context below. Include specific details like deadlines, 
        eligibility, location, fees, etc. when available. If information is not in the context, say so.
        Always cite the opportunity name and provide the URL when relevant, if the url of direct opportunity is present then give that url instead of opportunities board url.

        Context:
        {context}"""
    }
    
    # Prepare messages for LLM (system + recent history)
    llm_messages = [system_message] + st.session_state.messages[-6:]  # Keep recent context
    
    # Generate response
    with st.chat_message("assistant"):
        response = st.write_stream(stream_assistant(llm_messages))
        st.session_state.messages.append({"role": "assistant", "content": response})

# Sidebar with search info
with st.sidebar:
    st.header("Search Info")
    if st.session_state.documents:
        st.write(f"📊 Total opportunities: {len(st.session_state.documents)}")
        
        # Show sample titles
        st.subheader("Sample Opportunities:")
        for i, doc in enumerate(st.session_state.documents[:5]):
            st.write(f"• {doc['title'][:50]}...")
        
        # Query examples
        st.subheader("Example Queries:")
        st.code("scholarships in Canada")
        st.code("tell me about Vancouver program")
        st.code("deadline before August 2025")
        st.code("fully funded opportunities")