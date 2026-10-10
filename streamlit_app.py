import html
import json
import os
import sys
import uuid
import requests
import streamlit as st
import streamlit.components.v1 as components
from config import settings
from backend.text_utils import clean_opportunity_title, strip_thinking_tags

# Parse CLI debug flag (e.g. streamlit run streamlit_app.py -- --debug) or environment variable
CLI_DEBUG_FLAG: bool = (
    "--debug" in sys.argv
    or "-d" in sys.argv
    or os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")
    or settings.debug
)

DEFAULT_API_URL = f"http://{settings.api.host}:{settings.api.port}"


@st.cache_data(ttl=300, show_spinner=False)
def fetch_opportunities_cached(base_url: str, query: str = "", limit: int = 500):
    """Cached lightweight opportunity fetcher without heavy raw content payload."""
    params = {"limit": limit, "offset": 0, "include_content": "false"}
    if query.strip():
        params["query"] = query.strip()
    try:
        resp = requests.get(f"{base_url}/api/v1/opportunities", params=params, timeout=15)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


@st.cache_data(ttl=15, show_spinner=False)
def check_backend_health_cached(base_url: str):
    """Cached lightweight health check to prevent spamming backend on every rerun."""
    try:
        resp = requests.get(f"{base_url}/api/v1/health", timeout=2)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


@st.cache_data(ttl=10, show_spinner=False)
def fetch_sessions_cached(base_url: str):
    """Cached session list to prevent querying DB on every rerun during chat."""
    try:
        resp = requests.get(f"{base_url}/api/v1/sessions", timeout=3)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return []


st.set_page_config(page_title="Opportunity Details RAG Platform", page_icon="🎓", layout="wide")

# Custom CSS to perfectly align chat_input and Moonshine voice recorder at bottom of screen
st.markdown("""
<style>
:root {
    --chat-bar-width: min(720px, calc(100vw - 220px));
    --voice-bar-width: 115px;
    --bar-gap: 10px;
    --bar-height: 48px;
    --bar-bottom: 20px;
    --bar-bg: #262730;
    --bar-border: 1px solid rgba(255, 255, 255, 0.15);
    --bar-radius: 12px;
    --bar-shadow: 0 2px 8px rgba(0, 0, 0, 0.25);
}

/* Position chat input box at bottom of viewport */
div[data-testid="stChatInput"] {
    position: fixed !important;
    bottom: var(--bar-bottom) !important;
    left: 50% !important;
    transform: translateX(calc(-50% - (var(--voice-bar-width) + var(--bar-gap)) / 2)) !important;
    width: var(--chat-bar-width) !important;
    z-index: 9999 !important;
    margin: 0 !important;
    padding: 0 !important;
    box-sizing: border-box !important;
}

div[data-testid="stChatInput"] > div {
    height: var(--bar-height) !important;
    min-height: var(--bar-height) !important;
    max-height: var(--bar-height) !important;
    border-radius: var(--bar-radius) !important;
    border: var(--bar-border) !important;
    background-color: var(--bar-bg) !important;
    box-shadow: var(--bar-shadow) !important;
    padding: 0 8px 0 16px !important;
    box-sizing: border-box !important;
    display: flex !important;
    flex-direction: column !important;
    justify-content: center !important;
    transition: border-color 0.2s ease, box-shadow 0.2s ease !important;
}

div[data-testid="stChatInput"] > div:focus-within {
    border-color: #ff4b4b !important;
    box-shadow: 0 0 0 1px #ff4b4b, 0 2px 10px rgba(255, 75, 75, 0.2) !important;
}

div[data-testid="stChatInput"] > div > div {
    width: 100% !important;
    height: 100% !important;
    display: flex !important;
    flex-direction: row !important;
    align-items: center !important;
    justify-content: space-between !important;
    gap: 8px !important;
    margin: 0 !important;
    padding: 0 !important;
}

/* Hide empty file-upload (Dt) and empty recording (Lt) wrappers when not active */
div[data-testid="stChatInput"] > div > div > div:empty,
div[data-testid="stChatInput"] > div > div > div:not(:has(textarea)):not(:has(button)) {
    display: none !important;
    width: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
    flex: 0 0 0 !important;
}

/* Textarea wrapper (Tt) */
div[data-testid="stChatInput"] > div > div > div:has(textarea),
div[data-testid="stChatInput"] [data-baseweb="textarea"],
div[data-testid="stChatInput"] [data-baseweb="base-input"] {
    flex: 1 !important;
    height: 100% !important;
    display: flex !important;
    align-items: center !important;
    background: transparent !important;
    border: none !important;
    padding: 0 !important;
    margin: 0 !important;
    box-shadow: none !important;
    min-width: 0 !important;
}

div[data-testid="stChatInput"] textarea,
textarea[data-testid="stChatInputTextArea"] {
    height: 24px !important;
    min-height: 24px !important;
    max-height: 24px !important;
    line-height: 24px !important;
    font-size: 15px !important;
    font-family: inherit !important;
    padding: 0 !important;
    margin: 0 !important;
    border: none !important;
    outline: none !important;
    background: transparent !important;
    color: #f0f2f6 !important;
    width: 100% !important;
    resize: none !important;
    box-sizing: border-box !important;
    vertical-align: middle !important;
    display: block !important;
}

div[data-testid="stChatInput"] textarea::placeholder,
textarea[data-testid="stChatInputTextArea"]::placeholder {
    color: rgba(250, 250, 250, 0.5) !important;
    line-height: 24px !important;
}

/* Submit button wrapper (Ut) */
div[data-testid="stChatInput"] > div > div > div:has(button) {
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    height: 100% !important;
    margin: 0 !important;
    padding: 0 !important;
    flex-shrink: 0 !important;
}

div[data-testid="stChatInputSubmitButton"],
button[data-testid="stChatInputSubmitButton"] {
    height: 32px !important;
    width: 32px !important;
    min-width: 32px !important;
    min-height: 32px !important;
    border-radius: 8px !important;
    background-color: rgba(255, 255, 255, 0.08) !important;
    border: none !important;
    color: #f0f2f6 !important;
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
    cursor: pointer !important;
    padding: 0 !important;
    margin: 0 !important;
    transition: background-color 0.2s ease !important;
    align-self: center !important;
    box-sizing: border-box !important;
}

button[data-testid="stChatInputSubmitButton"]:hover:not(:disabled) {
    background-color: #ff4b4b !important;
    color: #ffffff !important;
}

div[data-testid="stChatInput"] .stChatInputInstructions,
div[data-testid="stChatInput"] #stChatInputInstructions {
    display: none !important;
}

/* Position voice recorder microphone capsule directly beside chat input with exact matching dimensions */
div[data-testid="stAudioInput"] {
    position: fixed !important;
    bottom: var(--bar-bottom) !important;
    left: calc(50% + (var(--chat-bar-width) / 2) - ((var(--voice-bar-width) + var(--bar-gap)) / 2) + var(--bar-gap)) !important;
    transform: none !important;
    width: var(--voice-bar-width) !important;
    min-width: var(--voice-bar-width) !important;
    max-width: var(--voice-bar-width) !important;
    height: var(--bar-height) !important;
    min-height: var(--bar-height) !important;
    max-height: var(--bar-height) !important;
    z-index: 10000 !important;
    margin: 0 !important;
    padding: 0 !important;
    box-sizing: border-box !important;
}

div[data-testid="stAudioInput"] label,
div[data-testid="stAudioInput"] [data-testid="stWidgetLabel"] {
    display: none !important;
    margin: 0 !important;
    padding: 0 !important;
    height: 0 !important;
    width: 0 !important;
    overflow: hidden !important;
}

div[data-testid="stAudioInput"] > div,
div[data-testid="stAudioInput"] > div:last-child {
    height: var(--bar-height) !important;
    min-height: var(--bar-height) !important;
    max-height: var(--bar-height) !important;
    width: 100% !important;
    border-radius: var(--bar-radius) !important;
    border: var(--bar-border) !important;
    background-color: var(--bar-bg) !important;
    box-shadow: var(--bar-shadow) !important;
    display: flex !important;
    flex-direction: row !important;
    align-items: center !important;
    justify-content: center !important;
    padding: 0 12px !important;
    margin: 0 !important;
    box-sizing: border-box !important;
    gap: 6px !important;
    transition: border-color 0.2s ease, box-shadow 0.2s ease, background-color 0.2s ease !important;
    cursor: pointer !important;
}

div[data-testid="stAudioInput"] > div:hover,
div[data-testid="stAudioInput"] > div:last-child:hover {
    border-color: rgba(255, 255, 255, 0.3) !important;
    background-color: #2c2d38 !important;
}

/* Hide empty toolbar/menu containers and wavesurfer in compact pill mode */
div[data-testid="stAudioInput"] [data-testid="stAudioInputWaveSurfer"],
div[data-testid="stAudioInput"] > div:last-child > div:first-child:not(:has(button[data-testid="stAudioInputActionButton"])),
div[data-testid="stAudioInput"] > div:last-child > div:empty {
    display: none !important;
    width: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
    flex: 0 0 0 !important;
}

div[data-testid="stAudioInput"] button,
div[data-testid="stAudioInput"] [data-testid="stAudioInputActionButton"] {
    border: none !important;
    background: transparent !important;
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
    padding: 0 !important;
    margin: 0 !important;
    cursor: pointer !important;
    box-shadow: none !important;
    min-height: auto !important;
    height: 24px !important;
    width: 24px !important;
    min-width: 24px !important;
}

div[data-testid="stAudioInput"] button:hover {
    background-color: rgba(255, 255, 255, 0.1) !important;
    border-radius: 6px !important;
}

div[data-testid="stAudioInput"] button svg {
    fill: #f0f2f6 !important;
    color: #f0f2f6 !important;
    width: 18px !important;
    height: 18px !important;
}

div[data-testid="stAudioInput"] [data-testid="stAudioInputWaveformTimeCode"],
div[data-testid="stAudioInput"] span {
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace !important;
    font-size: 14px !important;
    font-weight: 500 !important;
    color: #e0e0e0 !important;
    background: transparent !important;
    margin: 0 !important;
    padding: 0 !important;
    line-height: 24px !important;
    height: 24px !important;
    display: inline-flex !important;
    align-items: center !important;
    letter-spacing: 0.5px !important;
    user-select: none !important;
}

div[data-testid="stAudioInput"] * {
    color: #e0e0e0 !important;
}

/* Prevent chat content from being hidden behind fixed bottom bar */
div[data-testid="stChatMessageContainer"],
.main .block-container {
    padding-bottom: 100px !important;
}

/* Hide iframe created by auto-scroll helper without stopping JS execution */
iframe[title="streamlit.components.v1.html"] {
    height: 0 !important;
    width: 0 !important;
    opacity: 0 !important;
    position: absolute !important;
    pointer-events: none !important;
    border: none !important;
    margin: 0 !important;
    padding: 0 !important;
}
</style>
""", unsafe_allow_html=True)

st.title("🎓 Opportunity Details AI")

# Backend API Base URL and session state initialization
api_url = st.session_state.get("backend_api_url", DEFAULT_API_URL).rstrip("/")

# Health check & sessions fetch
backend_online = False
sessions_list = []
docs_count = 0
device_type = "cpu"

health_data = check_backend_health_cached(api_url)
if health_data:
    backend_online = True
    docs_count = health_data.get("docs_count", 0)
    device_type = health_data.get("device", "cpu")
    sessions_list = fetch_sessions_cached(api_url)

# --- SIDEBAR: 1. CHAT SESSIONS ---
st.sidebar.title("Chat Sessions")

if "active_session_id" not in st.session_state:
    if sessions_list:
        st.session_state.active_session_id = sessions_list[0]["session_id"]
    else:
        st.session_state.active_session_id = str(uuid.uuid4())

# Build clean session map
if sessions_list:
    session_map = {s["session_id"]: f"{s['title']} ({s['session_id'][:6]})" for s in sessions_list}
    if st.session_state.active_session_id not in session_map:
        session_map[st.session_state.active_session_id] = "Current Active Session"
else:
    session_map = {
        st.session_state.active_session_id: "Current Active Session"
    }

session_keys = list(session_map.keys())
current_index = session_keys.index(st.session_state.active_session_id) if st.session_state.active_session_id in session_keys else 0

col1, col2 = st.sidebar.columns([4, 1])
with col1:
    selected_sid = st.selectbox(
        "Select Session",
        options=session_keys,
        format_func=lambda x: session_map.get(x, x),
        index=current_index,
        label_visibility="collapsed"
    )
    if selected_sid != st.session_state.active_session_id:
        st.session_state.active_session_id = selected_sid
        st.rerun()

with col2:
    if st.button("➕", help="New Chat", use_container_width=True):
        new_sid = str(uuid.uuid4())
        if backend_online:
            try:
                requests.post(f"{api_url}/api/v1/sessions", json={"title": "New Chat"}, timeout=3)
                fetch_sessions_cached.clear()
            except Exception:
                pass
        st.session_state.active_session_id = new_sid
        st.rerun()

if st.sidebar.button("🗑️ Delete Current Chat", use_container_width=True):
    cur_sid = st.session_state.active_session_id
    if backend_online:
        try:
            requests.delete(f"{api_url}/api/v1/sessions/{cur_sid}", timeout=3)
            fetch_sessions_cached.clear()
        except Exception:
            pass
    remaining_keys = [k for k in session_keys if k != cur_sid]
    st.session_state.active_session_id = remaining_keys[0] if remaining_keys else str(uuid.uuid4())
    st.rerun()

# --- SIDEBAR: 2. MODEL & RAG OPTIONS ---
st.sidebar.markdown("---")
st.sidebar.title("Model & RAG Options")
think = st.sidebar.toggle("Think Mode", value=False)
provider_options = ["Ollama", "LLamaCPP"]
model_type_val = getattr(settings.model, "model_type", None) or getattr(settings.model, "main_model", "")
default_provider_idx = 1 if any(k in str(model_type_val).lower() for k in ("llamacpp", "llama_cpp", "gguf")) else 0
selected_provider = st.sidebar.selectbox("Select Model Provider", options=provider_options, index=default_provider_idx)
debug_mode = CLI_DEBUG_FLAG

# Default rerank setting is OFF (False)
rerank = False

# --- SIDEBAR: 3. DEBUG OPTIONS (Only visible when CLI --debug is active) ---
if debug_mode:
    st.sidebar.markdown("---")
    st.sidebar.title("🐞 Debug Options")
    st.sidebar.caption("🐞 *Debug Mode active via CLI flag (`--debug`)*")
    if backend_online:
        st.sidebar.success(f"Backend Connected ({docs_count} docs indexed, {device_type})")
    rerank = st.sidebar.toggle("Rerank Documents", value=False, help="Enable CrossEncoder re-ranking of retrieved document chunks")

    st.sidebar.markdown("##### 🕸️ Background Scraper")
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

# --- SIDEBAR: 4. CONFIGURATION ---
st.sidebar.markdown("---")
st.sidebar.title("Configuration")
api_url_input = st.sidebar.text_input("Backend API Base URL", value=api_url, key="backend_api_url").rstrip("/")
if api_url_input != api_url:
    st.session_state["backend_api_url"] = api_url_input
    st.rerun()

if not backend_online:
    st.sidebar.error("Cannot connect to FastAPI backend. Is it running?")


# Main Navigation Tabs
tab_chat, tab_opps = st.tabs(["💬 Chat Assistant", "🔍 Explore Opportunities"])

# --- TAB 1: CHAT ASSISTANT ---
with tab_chat:



    def render_tool_status_badge(used_tools: list, metadata: dict = None, target_container=None) -> None:
        """Render non-expandable static badge showing only the tool use words (no expander, no details, no empty box)."""
        if not used_tools:
            return
        target = target_container if target_container is not None else st
        seen_labels = set()
        badge_htmls = []
        for tool in used_tools:
            tool_str = str(tool).lower()
            label = None
            if "web" in tool_str:
                label = "🌐 Searched the web"
            elif any(k in tool_str for k in ("lookup", "deep", "curated")):
                label = "🔍 Deep Dived"

            if label and label not in seen_labels:
                seen_labels.add(label)
                badge_htmls.append(
                    f'<div style="display:inline-flex;align-items:center;padding:3px 10px;margin-right:6px;margin-bottom:8px;border-radius:14px;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12);font-size:0.82rem;font-weight:500;color:#9ca3af;user-select:none;">'
                    f'<span>{label}</span></div>'
                )

        if badge_htmls:
            target.markdown(
                f'<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:4px;">{"".join(badge_htmls)}</div>',
                unsafe_allow_html=True,
            )

    def render_debug_inspector(meta: dict) -> None:
        """Render interactive debug panel with prompt templates, variables, and retrieved docs."""
        if not debug_mode or not meta:
            return

        dbg = meta.get("debug_info") or {}
        with st.expander("🐞 Debug (Prompts, Variables & Retrieval)", expanded=False):
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

    @st.cache_data(ttl=60)
    def fetch_curated_chips(url: str):
        default_chips = [
            {"title": "✨ Top Scholarships Currently Open", "query": "Top Scholarships Currently Open"},
            {"title": "✨ Top 10 Fully Funded Scholarships", "query": "Top 10 Fully Funded Scholarships"},
        ]
        try:
            resp = requests.get(f"{url}/api/v1/opportunities/curated", timeout=2)
            if resp.status_code == 200:
                items = resp.json()
                if items:
                    fetched = [{"title": f"✨ {it.get('title', '').title()}", "query": it.get("title", "")} for it in items if it.get("title")]
                    seen = set()
                    unique_chips = []
                    for item in fetched:
                        norm = item["query"].lower().strip()
                        if norm not in seen:
                            seen.add(norm)
                            unique_chips.append(item)
                    if unique_chips:
                        return unique_chips[:2]
        except Exception:
            pass
        return default_chips

    chip_prompt = None

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
        if not messages:
            st.markdown("Search, query, and deep-dive into verified global scholarships, fellowships, and internships.")
            st.markdown("**🌟 Popular & Curated Opportunity Roundups:**")
            chips = fetch_curated_chips(api_url) if backend_online else [
                {"title": "✨ Top Scholarships Currently Open", "query": "Top Scholarships Currently Open"},
                {"title": "✨ Top 10 Fully Funded Scholarships", "query": "Top 10 Fully Funded Scholarships"},
            ]
            chip_cols = st.columns(len(chips))
            for idx, c in enumerate(chips):
                with chip_cols[idx]:
                    if st.button(c["title"], key=f"chip_welcome_{idx}", use_container_width=True):
                        chip_prompt = c["query"]

        for message in messages:
            with st.chat_message(message["role"]):
                meta = message.get("metadata") or {}
                if message["role"] == "assistant":
                    used_tools = meta.get("used_tools") or meta.get("debug_info", {}).get("used_tools", [])
                    if used_tools:
                        render_tool_status_badge(used_tools, metadata=meta)

                    think_trace = meta.get("thinking")
                    if think_trace and think:
                        with st.status("💭 Thinking Process", expanded=False):
                            st.markdown(think_trace)

                st.markdown(strip_thinking_tags(message["content"]))

                if debug_mode and (meta.get("debug") or meta.get("debug_info")):
                    render_debug_inspector(meta)
                elif meta.get("is_opportunity"):
                    docs = meta.get("initial_docs", [])
                    # if docs:
                    #     with st.expander("Retrieved Document Titles", expanded=False):
                    #         for d in docs:
                    #             st.markdown(f"- **{d}**")

        # Automatically scroll to the bottom/newest message on page load and refresh
        if messages:
            st.markdown('<div id="chat-bottom-anchor"></div>', unsafe_allow_html=True)
            components.html(
                """
                <script>
                    function doScroll() {
                        try {
                            const doc = window.parent.document;
                            const anchor = doc.getElementById('chat-bottom-anchor');
                            if (anchor) {
                                anchor.scrollIntoView({ behavior: 'auto', block: 'end' });
                            } else {
                                const msgs = doc.querySelectorAll('[data-testid="stChatMessage"]');
                                if (msgs.length > 0) {
                                    msgs[msgs.length - 1].scrollIntoView({ behavior: 'auto', block: 'end' });
                                }
                            }
                        } catch (e) {
                            // ignore cross-origin restrictions if embedded
                        }
                    }
                    doScroll();
                    setTimeout(doScroll, 50);
                    setTimeout(doScroll, 150);
                    setTimeout(doScroll, 350);
                    setTimeout(doScroll, 700);
                </script>
                """,
                height=0,
                width=0
            )

    # Integrated Bottom Chat Input + Permanent Moonshine Voice Recorder (Auto-Sends to LLM)
    voice_prompt = None
    if backend_online:
        rec_key = st.session_state.get("stt_rec_key", 0)
        audio_file = st.audio_input(
            "Record voice query",
            label_visibility="collapsed",
            key=f"moonshine_voice_recorder_{rec_key}"
        )
        if audio_file is not None:
            audio_bytes = audio_file.getvalue()
            audio_hash = f"{len(audio_bytes)}_{hash(audio_bytes[:64])}"
            if st.session_state.get("last_processed_audio_hash") != audio_hash:
                with st.spinner("🎙️ Transcribing voice with Moonshine STT..."):
                    try:
                        files = {"file": ("voice_query.wav", audio_bytes, "audio/wav")}
                        t_resp = requests.post(f"{api_url}/api/v1/transcribe", files=files, timeout=20)
                        if t_resp.status_code == 200:
                            t_data = t_resp.json()
                            trans_text = t_data.get("text", "").strip()
                            if trans_text:
                                st.session_state["last_processed_audio_hash"] = audio_hash
                                st.session_state["transcribed_query_text"] = trans_text
                                st.session_state["last_stt_info"] = t_data
                                # Reset widget key so timer resets back to 00:00 on next cycle
                                st.session_state["stt_rec_key"] = rec_key + 1
                                # Auto-send voice query directly to LLM
                                voice_prompt = trans_text
                            else:
                                st.warning("No speech detected in audio. Please speak clearly and try again.")
                                st.session_state["stt_rec_key"] = rec_key + 1
                        else:
                            st.error(f"STT API error ({t_resp.status_code}): {t_resp.text}")
                    except Exception as e:
                        st.error(f"Failed to reach STT endpoint: {e}")

    # Fixed bottom chat input at root level
    text_prompt = st.chat_input("Ask about scholarships, internships, or opportunities...")
    prompt = chip_prompt or voice_prompt or text_prompt

    if prompt:
        with chat_container:
            with st.chat_message("user"):
                if voice_prompt:
                    st.markdown(f"🎙️ **{prompt}**")
                else:
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
                full_thinking = ""
                tool_status_container = st.container()
                tools_badge_rendered = False
                response_placeholder = st.empty()
                full_raw_response = ""
                metadata_received = None

                try:
                    with requests.post(f"{api_url}/api/v1/chat/stream", json=payload, stream=True, timeout=(15, 180)) as resp:
                        if resp.status_code != 200:
                            st.error(f"API returned error status {resp.status_code}: {resp.text}")
                        else:
                            line_buf = ""
                            for chunk in resp.iter_content(chunk_size=None, decode_unicode=True):
                                if not chunk:
                                    continue
                                line_buf += chunk
                                while "\n" in line_buf:
                                    line, line_buf = line_buf.split("\n", 1)
                                    line_s = line.strip()
                                    if not line_s:
                                        continue

                                    if line_s.startswith("[[METADATA]]"):
                                        try:
                                            metadata_received = json.loads(line_s[len("[[METADATA]]"):].strip())
                                        except Exception:
                                            pass
                                        if metadata_received and not tools_badge_rendered:
                                            used_tools = metadata_received.get("used_tools") or metadata_received.get("debug_info", {}).get("used_tools", [])
                                            if used_tools:
                                                render_tool_status_badge(used_tools, metadata=metadata_received, target_container=tool_status_container)
                                                tools_badge_rendered = True
                                        continue

                                    if line_s.startswith("[[THINK]]"):
                                        if think:
                                            try:
                                                think_tok = json.loads(line_s[len("[[THINK]]"):].strip())
                                            except Exception:
                                                think_tok = line_s[len("[[THINK]]"):]
                                            full_thinking += think_tok
                                            if thinking_status is None:
                                                thinking_status = st.status("💭 Thinking Process...", expanded=False)
                                            with thinking_status:
                                                if thinking_text_placeholder is None:
                                                    thinking_text_placeholder = st.empty()
                                                thinking_text_placeholder.markdown(full_thinking)
                                        continue

                                    if line_s.startswith("[[CONTENT]]"):
                                        if thinking_status is not None and not thinking_completed:
                                            thinking_completed = True
                                            thinking_status.update(label="Thinking finished", state="complete", expanded=False)
                                        try:
                                            content_tok = json.loads(line_s[len("[[CONTENT]]"):].strip())
                                        except Exception:
                                            content_tok = line_s[len("[[CONTENT]]"):]
                                        full_raw_response += content_tok
                                        response_placeholder.markdown(full_raw_response)
                                        continue

                                    # Fallback for unexpected non-tagged line
                                    if thinking_status is not None and not thinking_completed:
                                        thinking_completed = True
                                        thinking_status.update(label="Thinking finished", state="complete", expanded=False)
                                    clean_chunk = strip_thinking_tags(line) if "<think>" in line else line
                                    if clean_chunk:
                                        full_raw_response += clean_chunk
                                        response_placeholder.markdown(full_raw_response)

                            if line_buf.strip():
                                line_s = line_buf.strip()
                                if line_s.startswith("[[CONTENT]]"):
                                    try:
                                        content_tok = json.loads(line_s[len("[[CONTENT]]"):].strip())
                                        full_raw_response += content_tok
                                    except Exception:
                                        pass
                                elif not line_s.startswith("[["):
                                    full_raw_response += line_s
                                response_placeholder.markdown(full_raw_response)

                    if thinking_status is not None and not thinking_completed:
                        thinking_status.update(label="Thinking finished", state="complete", expanded=False)

                    if metadata_received and not tools_badge_rendered:
                        used_tools = metadata_received.get("used_tools") or metadata_received.get("debug_info", {}).get("used_tools", [])
                        if used_tools:
                            render_tool_status_badge(used_tools, metadata=metadata_received, target_container=tool_status_container)
                            tools_badge_rendered = True

                    if metadata_received:
                        if debug_mode and (metadata_received.get("debug") or metadata_received.get("debug_info")):
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
    st.subheader("Explore Opportunities")

    search_query = st.text_input(
        "Search Opportunities by Keyword",
        value="",
        placeholder="e.g. Masters, Germany, Fully Funded, Fellowship, Physics...",
    )

    if backend_online:
        try:
            # Manage pagination / progressive load in session state
            if "explore_limit" not in st.session_state:
                st.session_state.explore_limit = 50

            last_search = st.session_state.get("last_explore_search", "")
            if search_query != last_search:
                st.session_state.last_explore_search = search_query
                st.session_state.explore_limit = 50

            opp_data = fetch_opportunities_cached(api_url, query=search_query, limit=500)
            if opp_data:
                total_items = opp_data.get("total", 0)
                items = opp_data.get("items", [])

                visible_limit = st.session_state.explore_limit
                displayed_items = items[:visible_limit]

                if not items:
                    st.info("No opportunities match your search criteria.")
                else:
                    if len(items) > len(displayed_items):
                        st.caption(
                            f"Showing **{len(displayed_items)}** of **{len(items)}** matching opportunities "
                        )
                    else:
                        st.caption(
                            f"Showing all **{len(items)}** matching opportunities (Total in Database: **{total_items}**)"
                        )

                    rows_parts = []
                    for item in displayed_items:
                        prof = item.get("profile") or {}
                        meta = item.get("metadata") or {}

                        raw_title = prof.get("title") or item.get("title") or "Untitled Opportunity"
                        title = html.escape(clean_opportunity_title(raw_title))
                        item_id = item.get("id")
                        url = item.get("url") or ""
                        source = html.escape(item.get("source") or "")

                        opp_type = html.escape(prof.get("type") or meta.get("type") or "Opportunity")
                        deadline = html.escape(prof.get("deadline") or meta.get("deadline") or item.get("deadline") or "Open / Unspecified")
                        org = html.escape((prof.get("organization") or meta.get("organization") or "").strip())
                        loc = html.escape((prof.get("location") or meta.get("location") or "").strip())
                        summary = html.escape((prof.get("summary") or "Details available in the full announcement.").strip())

                        benefits = html.escape((prof.get("benefits") or "").strip())
                        eligibility = html.escape((prof.get("eligibility") or "").strip())

                        title_markup = f'<a href="{url}" target="_blank" class="opp-title-link">{title} ↗</a>' if url else f'<span style="font-weight: 600; font-size: 1.05rem;">{title}</span>'

                        meta_badges = f'<span class="opp-badge-type">{opp_type}</span>'
                        if deadline and deadline.lower() not in ("open / unspecified", "unspecified", "not specified"):
                            meta_badges += f' <span class="opp-badge-deadline">📅 {deadline}</span>'

                        meta_info = []
                        if org:
                            meta_info.append(f'🏛️ <strong>{org}</strong>')
                        if loc:
                            meta_info.append(f'📍 {loc}')
                        if source:
                            meta_info.append(f'Portal: <code>{source}</code>')
                        meta_info_str = " &bull; ".join(meta_info)

                        extra_markup = ""
                        if benefits and benefits.lower() != "not specified":
                            extra_markup += f'<div class="opp-extra-detail"><strong>Benefits:</strong> {benefits}</div>'
                        if eligibility and eligibility.lower() != "not specified":
                            extra_markup += f'<div class="opp-extra-detail"><strong>Eligibility:</strong> {eligibility}</div>'

                        row = (
                            '<tr class="opp-table-row">'
                            '<td style="width: 100%;">'
                            f'<div style="display: flex; justify-content: space-between; align-items: baseline; flex-wrap: wrap; gap: 8px; margin-bottom: 6px;">'
                            f'<div>{title_markup}</div>'
                            f'<div>{meta_badges}</div>'
                            '</div>'
                            f'<div class="opp-meta-sub" style="margin-bottom: 8px;">{meta_info_str}</div>'
                            f'<div class="opp-summary-text">{summary}</div>'
                            f'{extra_markup}'
                            '</td>'
                            '</tr>'
                        )
                        rows_parts.append(row)

                    all_rows = "".join(rows_parts)
                    table_html = (
                        '<style>'
                        '.opp-table-container { width: 100%; overflow-x: auto; margin-top: 0.8rem; margin-bottom: 1.2rem; }'
                        '.opp-borderless-table { width: 100%; border-collapse: collapse; border: none !important; }'
                        '.opp-borderless-table tr { border: none !important; }'
                        '.opp-borderless-table td { border: none !important; padding: 16px 14px; vertical-align: top; font-size: 0.9rem; line-height: 1.55; }'
                        '.opp-table-row:nth-child(even) { background-color: rgba(128, 128, 128, 0.04); }'
                        '.opp-table-row:hover { background-color: rgba(128, 128, 128, 0.08); }'
                        '.opp-title-link { font-weight: 600; text-decoration: none; font-size: 1.05rem; color: #ff4b4b; }'
                        '.opp-title-link:hover { text-decoration: underline; }'
                        '.opp-badge-type { display: inline-block; padding: 3px 8px; border-radius: 6px; font-size: 0.78rem; font-weight: 600; background: rgba(255, 75, 75, 0.12); color: #ff4b4b; }'
                        '.opp-badge-deadline { display: inline-block; padding: 2px 7px; border-radius: 4px; font-size: 0.8rem; font-weight: 600; background: rgba(240, 136, 62, 0.15); color: #f0883e; }'
                        '.opp-meta-sub { opacity: 0.72; font-size: 0.82rem; }'
                        '.opp-summary-text { opacity: 0.92; font-size: 0.9rem; }'
                        '.opp-extra-detail { margin-top: 6px; font-size: 0.84rem; opacity: 0.85; }'
                        '</style>'
                        '<div class="opp-table-container">'
                        '<table class="opp-borderless-table">'
                        f'<tbody>{all_rows}</tbody>'
                        '</table>'
                        '</div>'
                    )
                    st.markdown(table_html, unsafe_allow_html=True)

                    # Progressive loading controls ("Load More")
                    if len(items) > visible_limit:
                        col_lm1, col_lm2, _ = st.columns([2, 2, 4])
                        with col_lm1:
                            if st.button("⬇️ Load 50 More", use_container_width=True):
                                st.session_state.explore_limit += 50
                                st.rerun()
                        with col_lm2:
                            if st.button(f"Show All ({len(items)})", use_container_width=True):
                                st.session_state.explore_limit = len(items)
                                st.rerun()
            else:
                st.error("Failed to fetch opportunities from API backend.")
        except Exception as e:
            st.error(f"Error connecting to opportunities endpoint: {e}")
    else:
        st.warning("Backend API is currently offline. Start the backend service to browse opportunities.")
