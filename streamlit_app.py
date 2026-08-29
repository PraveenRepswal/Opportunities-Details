import json
import os
import sys
import uuid
import requests
import streamlit as st
import streamlit.components.v1 as components
from config import settings

# Parse CLI debug flag (e.g. streamlit run streamlit_app.py -- --debug) or environment variable
CLI_DEBUG_FLAG: bool = (
    "--debug" in sys.argv
    or "-d" in sys.argv
    or os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")
    or settings.debug
)

DEFAULT_API_URL = f"http://{settings.api.host}:{settings.api.port}"

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

try:
    health_resp = requests.get(f"{api_url}/api/v1/health", timeout=3)
    if health_resp.status_code == 200:
        backend_online = True
        health_data = health_resp.json()
        docs_count = health_data.get("docs_count", 0)
        device_type = health_data.get("device", "cpu")
        
        # Fetch sessions from API v1
        sess_resp = requests.get(f"{api_url}/api/v1/sessions", timeout=3)
        if sess_resp.status_code == 200:
            sessions_list = sess_resp.json()
except Exception:
    pass

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
        key="session_select_box",
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
            except Exception:
                pass
        st.session_state.active_session_id = new_sid
        st.session_state["session_select_box"] = new_sid
        st.rerun()

if st.sidebar.button("🗑️ Delete Current Chat", use_container_width=True):
    if backend_online:
        try:
            requests.delete(f"{api_url}/api/v1/sessions/{st.session_state.active_session_id}", timeout=3)
        except Exception:
            pass
    new_sid = str(uuid.uuid4())
    st.session_state.active_session_id = new_sid
    st.session_state["session_select_box"] = new_sid
    st.rerun()

# --- SIDEBAR: 2. MODEL & RAG OPTIONS ---
st.sidebar.markdown("---")
st.sidebar.title("Model & RAG Options")
think = st.sidebar.toggle("Think Mode", value=False)
rerank = st.sidebar.toggle("Rerank Retrieved Docs", value=True)
selected_provider = st.sidebar.selectbox("Select Model Provider", options=["Ollama", "LLamaCPP"])
debug_mode = CLI_DEBUG_FLAG
if debug_mode:
    st.sidebar.caption("🐞 *Debug Mode active via CLI flag (`--debug`)*")

# --- SIDEBAR: 3. BACKGROUND SCRAPER ---
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

# --- SIDEBAR: 4. CONFIGURATION ---
st.sidebar.markdown("---")
st.sidebar.title("Configuration")
api_url_input = st.sidebar.text_input("Backend API Base URL", value=api_url, key="backend_api_url").rstrip("/")
if api_url_input != api_url:
    st.session_state["backend_api_url"] = api_url_input
    st.rerun()

if backend_online:
    st.sidebar.success(f"Backend Connected ({docs_count} docs indexed, {device_type})")
else:
    st.sidebar.error("Cannot connect to FastAPI backend. Is it running?")

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



    def render_debug_inspector(meta: dict) -> None:
        """Render interactive debug panel with prompt templates, variables, and retrieved docs."""
        if not meta:
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
    prompt = voice_prompt or text_prompt

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
