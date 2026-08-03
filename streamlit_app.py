import json
import re
import uuid
import requests
import streamlit as st
from config import settings

DEFAULT_API_URL = f"http://{settings.api.host}:{settings.api.port}"

st.set_page_config(page_title="Opportunity Details RAG Platform", page_icon="🎓", layout="wide")

# Custom CSS to perfectly center and pin chat_input fixed at the bottom of the screen
st.markdown("""
<style>
/* Perfectly center chat input box at bottom of viewport */
div[data-testid="stChatInput"] {
    position: fixed !important;
    bottom: 20px !important;
    left: 55% !important;
    transform: translateX(-50%) !important;
    width: 65% !important;
    max-width: 900px !important;
    z-index: 9999 !important;
}
</style>
""", unsafe_allow_html=True)

st.title("🎓 Opportunity Details RAG Platform")

# Sidebar Settings
st.sidebar.title("Configuration")
api_url = st.sidebar.text_input("Backend API Base URL", value=DEFAULT_API_URL).rstrip("/")

# Health check & sessions fetch
backend_online = False
sessions_list = []
docs_count = 0
device_type = "cpu"

try:
    health_resp = requests.get(f"{api_url}/api/v1/health", timeout=3)
    if health_resp.status_code == 200:
        backend_online = True
        health_data = health_resp.json()
        docs_count = health_data.get("docs_count", 0)
        device_type = health_data.get("device", "cpu")
        st.sidebar.success(f"Backend Connected ({docs_count} docs indexed, {device_type})")
        
        # Fetch sessions from API v1
        sess_resp = requests.get(f"{api_url}/api/v1/sessions", timeout=3)
        if sess_resp.status_code == 200:
            sessions_list = sess_resp.json()
    else:
        st.sidebar.warning("Backend returning non-200 status.")
except Exception:
    st.sidebar.error("Cannot connect to FastAPI backend. Is it running?")

# Sidebar Scraper Trigger Control
st.sidebar.markdown("---")
st.sidebar.title("🕸️ Background Scraper")

col_sc1, col_sc2 = st.sidebar.columns([1, 1])
with col_sc1:
    if st.button("Run Scraper", help="Trigger web scrapers to fetch latest opportunities"):
        try:
            scrape_resp = requests.post(f"{api_url}/api/v1/scrape", json={"days_back": 30, "score_threshold": 0.7}, timeout=5)
            if scrape_resp.status_code == 200:
                st.sidebar.info("Scraper background job started!")
            else:
                st.sidebar.error("Failed to start scraper.")
        except Exception as e:
            st.sidebar.error(f"Scraper request error: {e}")

with col_sc2:
    if st.button("Check Status"):
        try:
            status_resp = requests.get(f"{api_url}/api/v1/scrape/status", timeout=3)
            if status_resp.status_code == 200:
                s_data = status_resp.json()
                st.sidebar.info(f"Status: {s_data.get('status')}\nItems: {s_data.get('items_scraped')}")
        except Exception:
            st.sidebar.warning("Status check failed.")

def clean_pill_text(val: str) -> str:
    """Clean markdown bold/italic tags and extra quotes from pill text, returning empty if placeholder/blank."""
    if not val:
        return ""
    cleaned = val.replace("**", "").replace("*", "").replace("`", "").strip()
    if cleaned.lower() in ("", "n/a", "none", "null", "unknown", "not specified"):
        return ""
    return cleaned

# Helper function to render styled HTML pill badges
def render_pill_badges(card: dict) -> str:
    """Build styled HTML pill badges for opportunity metadata."""
    pills = []
    dl = clean_pill_text(card.get("deadline", ""))
    org = clean_pill_text(card.get("organization", ""))
    loc = clean_pill_text(card.get("location", ""))
    tp = clean_pill_text(card.get("type", ""))

    if dl:
        pills.append(f'<span style="background-color:#f0e6ff; color:#6b21a8; padding:4px 12px; border-radius:14px; font-size:13px; font-weight:500; margin-right:6px; display:inline-block; margin-bottom:6px;">Deadline: {dl}</span>')
    if org:
        pills.append(f'<span style="background-color:#e0f2fe; color:#0369a1; padding:4px 12px; border-radius:14px; font-size:13px; font-weight:500; margin-right:6px; display:inline-block; margin-bottom:6px;">Organization: {org}</span>')
    if loc:
        pills.append(f'<span style="background-color:#dcfce7; color:#15803d; padding:4px 12px; border-radius:14px; font-size:13px; font-weight:500; margin-right:6px; display:inline-block; margin-bottom:6px;">Location: {loc}</span>')
    if tp:
        pills.append(f'<span style="background-color:#fef3c7; color:#b45309; padding:4px 12px; border-radius:14px; font-size:13px; font-weight:500; margin-right:6px; display:inline-block; margin-bottom:6px;">Type: {tp}</span>')

    if pills:
        return '<div style="margin-top:4px; margin-bottom:10px;">' + "".join(pills) + '</div>'
    return ""

def format_message_with_inline_pills(text: str) -> str:
    """Converts key-value bullet lines inline into styled HTML Pill Badges."""
    lines = text.split("\n")
    formatted_lines = []

    for line in lines:
        line_clean = line.strip()
        if "Deadline:" in line_clean or "Organization:" in line_clean or "Location:" in line_clean:
            parts = [p.strip().lstrip("•*- ").strip() for p in line_clean.split("|")]
            card_dict = {}
            for part in parts:
                if ":" in part:
                    k, v = part.split(":", 1)
                    key_norm = k.strip().lower()
                    val_clean = clean_pill_text(v)
                    if key_norm in ("deadline", "organization", "location", "type"):
                        card_dict[key_norm] = val_clean

            pills_html = render_pill_badges(card_dict)
            if pills_html:
                formatted_lines.append(pills_html)
            else:
                formatted_lines.append(line)
        else:
            formatted_lines.append(line)

    return "\n".join(formatted_lines)

# Main Navigation Tabs
tab_chat, tab_opps = st.tabs(["💬 Chat Assistant", "🔍 Explore Opportunities"])

# --- TAB 1: CHAT ASSISTANT ---
with tab_chat:
    # Sidebar Session State Management
    st.sidebar.markdown("---")
    st.sidebar.title("Chat Sessions")

    if "active_session_id" not in st.session_state:
        if sessions_list:
            st.session_state.active_session_id = sessions_list[0]["session_id"]
        else:
            st.session_state.active_session_id = str(uuid.uuid4())

    col1, col2 = st.sidebar.columns([3, 1])
    with col1:
        if sessions_list:
            session_map = {s["session_id"]: f"{s['title']} ({s['session_id'][:6]})" for s in sessions_list}
            if st.session_state.active_session_id not in session_map:
                session_map[st.session_state.active_session_id] = "Current Active Session"
            
            selected_sid = st.selectbox(
                "Select Session",
                options=list(session_map.keys()),
                format_func=lambda x: session_map.get(x, x),
                index=list(session_map.keys()).index(st.session_state.active_session_id) if st.session_state.active_session_id in session_map else 0,
                key="session_select_box"
            )
            if selected_sid != st.session_state.active_session_id:
                st.session_state.active_session_id = selected_sid
                st.rerun()

    with col2:
        if st.button("➕", help="New Chat"):
            new_sid = str(uuid.uuid4())
            try:
                requests.post(f"{api_url}/api/v1/sessions", json={"title": "New Chat"}, timeout=3)
            except Exception:
                pass
            st.session_state.active_session_id = new_sid
            st.session_state["session_select_box"] = new_sid
            st.rerun()

    if st.sidebar.button("🗑️ Delete Current Chat"):
        try:
            requests.delete(f"{api_url}/api/v1/sessions/{st.session_state.active_session_id}", timeout=3)
        except Exception:
            pass
        new_sid = str(uuid.uuid4())
        st.session_state.active_session_id = new_sid
        st.session_state["session_select_box"] = new_sid
        st.rerun()

    st.sidebar.title("Model & RAG Options")
    think = st.sidebar.toggle("Think Mode", value=False)
    rerank = st.sidebar.toggle("Rerank Retrieved Docs", value=True)
    debug_mode = st.sidebar.toggle("🐞 Debug Mode", value=False)
    selected_provider = st.sidebar.selectbox("Select Model Provider", options=["Ollama", "LLamaCPP"])

    def render_debug_inspector(meta: dict) -> None:
        """Render interactive debug panel with prompt templates, variables, and retrieved docs."""
        if not meta:
            return

        dbg = meta.get("debug_info") or {}
        with st.expander("🐞 Debug Inspector (Prompts, Variables & Retrieval)", expanded=False):
            st.caption(f"**Route Target:** `{dbg.get('route_target', 'N/A')}` | **Opportunity Prompt:** `{meta.get('is_opportunity')}` | **Used Tools:** `{meta.get('used_tools', [])}`")

            t1, t2, t3, t4 = st.tabs(["💬 System Prompt", "✉️ Human Prompt", "📄 Retrieved Docs", "📊 Variables & Context"])

            with t1:
                st.markdown("**Final Formatted System Prompt (Sent to LLM):**")
                st.code(dbg.get("formatted_system_prompt", "N/A"), language="markdown")

            with t2:
                st.markdown("**Final Formatted Human Prompt (with Context & History):**")
                st.code(dbg.get("formatted_human_prompt", "N/A"), language="markdown")

            with t3:
                docs = meta.get("initial_docs", [])
                st.markdown(f"**Retrieved Document Titles ({len(docs)} items):**")
                if docs:
                    for idx, d in enumerate(docs, 1):
                        st.markdown(f"{idx}. `{d}`")
                else:
                    st.write("No documents retrieved.")

            with t4:
                st.markdown("**Filled Template Variables Dictionary:**")
                st.json(dbg.get("filled_variables", {}))
                st.markdown("**RAG Context Snippet:**")
                st.code(dbg.get("context_text_snippet", "No RAG context used."))

    # Fetch message history for active session from API v1
    messages = []
    if backend_online and st.session_state.active_session_id:
        try:
            msg_resp = requests.get(f"{api_url}/api/v1/sessions/{st.session_state.active_session_id}/messages", timeout=3)
            if msg_resp.status_code == 200:
                messages = msg_resp.json()
        except Exception:
            messages = []

    # Container for chat message history
    chat_container = st.container()
    with chat_container:
        for message in messages:
            with st.chat_message(message["role"]):
                if message["role"] == "assistant":
                    formatted_html = format_message_with_inline_pills(message["content"])
                    st.markdown(formatted_html, unsafe_allow_html=True)
                else:
                    st.markdown(message["content"])

                meta = message.get("metadata") or {}
                if meta.get("debug") or meta.get("debug_info"):
                    render_debug_inspector(meta)
                elif meta.get("is_opportunity"):
                    docs = meta.get("initial_docs", [])
                    if docs:
                        with st.expander("Retrieved Document Titles", expanded=False):
                            for d in docs:
                                st.markdown(f"- **{d}**")

    # Fixed bottom chat input at root level
    if prompt := st.chat_input("Ask about scholarships, internships, or opportunities..."):
        with chat_container:
            with st.chat_message("user"):
                st.markdown(prompt)

            history_payload = [{"role": m["role"], "content": m["content"], "metadata": m.get("metadata")} for m in messages[-6:]]

            # Stream assistant response from FastAPI backend v1 API
            with st.chat_message("assistant"):
                payload = {
                    "prompt": prompt,
                    "session_id": st.session_state.active_session_id,
                    "conversation_history": history_payload,
                    "provider": selected_provider,
                    "think": think,
                    "rerank": rerank,
                    "debug": debug_mode,
                }

                thinking_status = None
                thinking_text_placeholder = None
                thinking_completed = False
                response_placeholder = st.empty()
                full_raw_response = ""
                metadata_received = None

                try:
                    with requests.post(f"{api_url}/api/v1/chat/stream", json=payload, stream=True, timeout=120) as resp:
                        if resp.status_code != 200:
                            st.error(f"API returned error status {resp.status_code}: {resp.text}")
                        else:
                            for chunk in resp.iter_content(chunk_size=None, decode_unicode=True):
                                if not chunk:
                                    continue

                                if "[[METADATA]]" in chunk:
                                    lines = chunk.split("\n")
                                    for line in lines:
                                        if line.startswith("[[METADATA]]"):
                                            meta_str = line.replace("[[METADATA]]", "").strip()
                                            try:
                                                metadata_received = json.loads(meta_str)
                                            except Exception:
                                                pass
                                        else:
                                            full_raw_response += line
                                    continue

                                full_raw_response += chunk

                                display_text = full_raw_response

                                if "<think>" in display_text:
                                    think_start = display_text.find("<think>") + len("<think>")
                                    think_end = display_text.find("</think>")

                                    if think_end != -1:
                                        thinking_content = display_text[think_start:think_end]
                                        response_content = display_text[think_end + len("</think>") :]
                                        if not thinking_completed:
                                            thinking_completed = True
                                            if thinking_status is not None:
                                                thinking_status.update(label="Thinking complete!", state="complete", expanded=False)
                                    else:
                                        thinking_content = display_text[think_start:]
                                        response_content = ""
                                else:
                                    thinking_content = ""
                                    response_content = display_text

                                if thinking_content:
                                    if thinking_status is None:
                                        thinking_status = st.status("Thinking...", expanded=True)
                                    with thinking_status:
                                        if thinking_text_placeholder is None:
                                            thinking_text_placeholder = st.empty()
                                        thinking_text_placeholder.markdown(thinking_content)

                                if response_content:
                                    formatted_live = format_message_with_inline_pills(response_content)
                                    response_placeholder.markdown(formatted_live, unsafe_allow_html=True)

                    if thinking_status is not None and not thinking_completed:
                        thinking_status.update(label="Thinking finished", state="complete", expanded=False)

                    if metadata_received:
                        if metadata_received.get("debug") or metadata_received.get("debug_info"):
                            render_debug_inspector(metadata_received)
                        elif metadata_received.get("is_opportunity"):
                            docs = metadata_received.get("initial_docs", [])
                            if docs:
                                with st.expander("Retrieved Document Titles", expanded=False):
                                    for d in docs:
                                        st.markdown(f"- **{d}**")

                    st.rerun()

                except Exception as e:
                    st.error(f"Failed to communicate with API backend: {e}")

# --- TAB 2: EXPLORE OPPORTUNITIES ---
with tab_opps:
    st.subheader("📚 Scraped Opportunities Database")
    st.markdown("Browse and search opportunities indexed in the SQLite database via `/api/v1/opportunities` endpoint.")

    col_q, col_lim = st.columns([4, 1])
    with col_q:
        search_query = st.text_input("Search Opportunities by Keyword", value="", placeholder="e.g. Masters, Germany, Fully Funded, Fellowship...")
    with col_lim:
        page_size = st.selectbox("Page Size", options=[10, 20, 50], index=1)

    if backend_online:
        try:
            params = {"limit": page_size, "offset": 0}
            if search_query.strip():
                params["query"] = search_query.strip()
            
            opp_resp = requests.get(f"{api_url}/api/v1/opportunities", params=params, timeout=5)
            if opp_resp.status_code == 200:
                opp_data = opp_resp.json()
                total_items = opp_data.get("total", 0)
                items = opp_data.get("items", [])

                st.info(f"Found **{total_items}** opportunities matching your criteria.")

                for idx, item in enumerate(items, 1):
                    with st.expander(f"#{item['id']} - {item['title']}", expanded=False):
                        if item.get("url"):
                            st.markdown(f"🔗 **Source Link**: [{item['url']}]({item['url']})")
                        if item.get("source"):
                            st.caption(f"Source Portal: `{item['source']}` | Indexed At: `{item.get('created_at', '')}`")
                        st.markdown(item["content"][:1500] + ("..." if len(item["content"]) > 1500 else ""))
            else:
                st.error(f"Failed to fetch opportunities from API: {opp_resp.status_code}")
        except Exception as e:
            st.error(f"Error connecting to /api/v1/opportunities: {e}")
    else:
        st.warning("Backend API is currently offline. Start the backend service to browse opportunities.")
