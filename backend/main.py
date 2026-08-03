import asyncio
import json
import sys
from datetime import datetime
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional
from fastapi import FastAPI, HTTPException, APIRouter, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import uvicorn

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.schemas import (
    ChatRequest,
    ChatResponse,
    HealthResponse,
    SessionCreate,
    SessionResponse,
    MessageItem,
    OpportunityItem,
    OpportunitiesResponse,
    ScrapeRequest,
    ScrapeResponse,
)
from backend.rag import RAGPipeline, DEVICE, DAYS_BACK, strip_thinking_tags
from backend.database import (
    init_db,
    create_session,
    list_sessions,
    get_session_messages,
    add_message,
    delete_session,
    list_opportunities,
    get_opportunity_by_id,
    upsert_opportunities,
)
from scraper import CombinedScraper
from config import settings

# Global pipeline instance & scrape job state
rag_pipeline = RAGPipeline()
scrape_job_state = {
    "status": "idle",
    "message": "No scraping job has been executed yet.",
    "items_scraped": 0,
    "scraped_at": "",
    "error": None,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Warm up RAG pipeline and initialize database on app startup."""
    print("[API] Starting up FastAPI server...")
    try:
        init_db()
        rag_pipeline.initialize()
    except Exception as exc:
        print(f"[API] Error during startup initialization: {exc}")
    yield
    print("[API] Shutting down FastAPI server...")


app = FastAPI(
    title="Opportunity Chatbot REST API",
    description="FastAPI Backend for Opportunity Chatbot, RAG Pipeline & Web Scraper Service",
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS for all clients (mobile, web frontends, Streamlit, etc.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.api.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create API v1 Router
router_v1 = APIRouter(prefix="/api/v1", tags=["v1"])


# Helper function for background scraping execution
async def _execute_scrape_job(days_back: int, threshold: float, reindex: bool):
    global scrape_job_state
    scrape_job_state["status"] = "running"
    scrape_job_state["message"] = f"Scraping opportunities from portals (days_back={days_back}, threshold={threshold})..."
    scrape_job_state["error"] = None

    try:
        scraper = CombinedScraper(days_back=days_back, threshold=threshold)
        results = await scraper.run_all_scrapers()

        saved_count = 0
        if isinstance(results, list):
            saved_count = upsert_opportunities(results)

        if reindex:
            rag_pipeline.reload_documents()

        scrape_job_state["status"] = "completed"
        scrape_job_state["message"] = f"Successfully scraped {len(results)} items and stored in database."
        scrape_job_state["items_scraped"] = len(results)
        scrape_job_state["scraped_at"] = datetime.now().isoformat()
    except Exception as exc:
        print(f"[API] Scraping background task failed: {exc}")
        scrape_job_state["status"] = "failed"
        scrape_job_state["message"] = f"Scraping task failed: {str(exc)}"
        scrape_job_state["error"] = str(exc)


# --- HEALTH ENDPOINTS ---
@router_v1.get("/health", response_model=HealthResponse)
@app.get("/health", response_model=HealthResponse, include_in_schema=False)
async def health_check():
    """Health check endpoint returning system status and RAG metrics."""
    return HealthResponse(
        status="ok" if rag_pipeline.is_initialized else "initializing",
        docs_count=len(rag_pipeline.docs) if rag_pipeline.docs else 0,
        device=DEVICE,
        days_back=DAYS_BACK,
        ollama_base_url=settings.model.ollama_base_url,
        llamacpp_server_url=settings.model.llamacpp_server_url,
    )


# --- CHAT ENDPOINTS ---
@router_v1.post("/chat", response_model=ChatResponse)
@app.post("/chat", response_model=ChatResponse, include_in_schema=False)
async def chat_completion(request: ChatRequest):
    """Standard non-streaming REST endpoint for chat completions."""
    try:
        session_id = request.session_id or create_session()
        add_message(session_id=session_id, role="user", content=request.prompt)

        history = [m.model_dump() for m in request.conversation_history]
        raw_generator = rag_pipeline.stream_response(
            prompt=request.prompt,
            history=history,
            provider=request.provider,
            think=request.think,
            rerank=request.rerank,
            ollama_base_url=request.ollama_base_url,
            llamacpp_server_url=request.llamacpp_server_url,
        )

        full_response = ""
        metadata_dict = None

        async for chunk in raw_generator:
            chunk_str = chunk if isinstance(chunk, str) else str(chunk)
            if "[[METADATA]]" in chunk_str:
                for line in chunk_str.split("\n"):
                    if line.startswith("[[METADATA]]"):
                        try:
                            metadata_dict = json.loads(line.replace("[[METADATA]]", "").strip())
                        except Exception:
                            pass
            else:
                full_response += chunk_str

        clean_response = strip_thinking_tags(full_response)
        add_message(
            session_id=session_id,
            role="assistant",
            content=clean_response,
            metadata=metadata_dict,
        )

        return ChatResponse(
            session_id=session_id,
            response=clean_response,
            metadata=metadata_dict,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def extract_cards_from_response(text: str) -> List[dict]:
    """Extract opportunity card metadata from text metadata lines."""
    cards = []
    for line in text.split("\n"):
        line_clean = line.strip()
        if ("Deadline:" in line_clean or "Organization:" in line_clean or "Location:" in line_clean) and "|" in line_clean:
            item = {}
            for part in line_clean.split("|"):
                part = part.strip().lstrip("•*- ").strip()
                if ":" in part:
                    k, v = part.split(":", 1)
                    key_norm = k.strip().lower()
                    val_norm = v.strip().replace("**", "").replace("*", "").strip()
                    if key_norm in ("deadline", "organization", "location", "type", "title"):
                        item[key_norm] = val_norm
            if item:
                cards.append(item)
    return cards


@router_v1.post("/chat/stream")
@app.post("/chat/stream", include_in_schema=False)
@app.post("/api/chat/stream", include_in_schema=False)
async def chat_stream(request: ChatRequest):
    """Stream chat completion response chunk by chunk with automatic SQLite persistence."""
    try:
        session_id = request.session_id
        if session_id:
            add_message(session_id=session_id, role="user", content=request.prompt)

        history = [m.model_dump() for m in request.conversation_history]
        raw_generator = rag_pipeline.stream_response(
            prompt=request.prompt,
            history=history,
            provider=request.provider,
            think=request.think,
            rerank=request.rerank,
            debug=request.debug,
            ollama_base_url=request.ollama_base_url,
            llamacpp_server_url=request.llamacpp_server_url,
        )

        async def database_persisting_generator():
            full_response = ""
            metadata_dict = None
            async for chunk in raw_generator:
                chunk_str = chunk if isinstance(chunk, str) else str(chunk)
                if "[[METADATA]]" in chunk_str:
                    for line in chunk_str.split("\n"):
                        if line.startswith("[[METADATA]]"):
                            try:
                                metadata_dict = json.loads(line.replace("[[METADATA]]", "").strip())
                            except Exception:
                                pass
                else:
                    full_response += chunk_str

                yield chunk_str

            if session_id and full_response:
                clean_text = strip_thinking_tags(full_response)
                cards = extract_cards_from_response(full_response)
                if cards:
                    if metadata_dict is None:
                        metadata_dict = {}
                    metadata_dict["opportunity_cards"] = cards

                add_message(
                    session_id=session_id,
                    role="assistant",
                    content=clean_text,
                    metadata=metadata_dict,
                )

        return StreamingResponse(database_persisting_generator(), media_type="text/event-stream")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- OPPORTUNITIES ENDPOINTS ---
@router_v1.get("/opportunities", response_model=OpportunitiesResponse)
@app.get("/opportunities", response_model=OpportunitiesResponse, include_in_schema=False)
async def get_opportunities_list(
    query: Optional[str] = Query(None, description="Search keyword for opportunity title or content"),
    source: Optional[str] = Query(None, description="Filter by opportunity portal source"),
    limit: int = Query(20, ge=1, le=100, description="Page size limit"),
    offset: int = Query(0, ge=0, description="Offset position for pagination"),
):
    """Retrieve paginated opportunities list with search and source filtering."""
    result = list_opportunities(query=query, source=source, limit=limit, offset=offset)
    return OpportunitiesResponse(
        total=result["total"],
        limit=result["limit"],
        offset=result["offset"],
        items=[OpportunityItem(**item) for item in result["items"]],
    )


@router_v1.get("/opportunities/{opp_id}", response_model=OpportunityItem)
@app.get("/opportunities/{opp_id}", response_model=OpportunityItem, include_in_schema=False)
async def get_opportunity_detail(opp_id: int):
    """Retrieve detail of a specific opportunity by ID."""
    item = get_opportunity_by_id(opp_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"Opportunity with ID {opp_id} not found.")
    return OpportunityItem(**item)


# --- SCRAPE ENDPOINTS ---
@router_v1.post("/scrape", response_model=ScrapeResponse)
@app.post("/scrape", response_model=ScrapeResponse, include_in_schema=False)
async def trigger_scrape_job(payload: ScrapeRequest, background_tasks: BackgroundTasks):
    """Trigger background scraping job across portals and reload RAG vectorstore upon completion."""
    global scrape_job_state
    if scrape_job_state["status"] == "running":
        return ScrapeResponse(
            status="running",
            message="A scraping task is already running in background.",
            items_scraped=scrape_job_state["items_scraped"],
            scraped_at=scrape_job_state["scraped_at"],
        )

    background_tasks.add_task(
        _execute_scrape_job,
        days_back=payload.days_back,
        threshold=payload.score_threshold,
        reindex=payload.reindex,
    )

    return ScrapeResponse(
        status="started",
        message="Scraping job successfully initiated in background.",
        items_scraped=0,
        scraped_at=datetime.now().isoformat(),
    )


@router_v1.get("/scrape/status", response_model=ScrapeResponse)
@app.get("/scrape/status", response_model=ScrapeResponse, include_in_schema=False)
async def get_scrape_job_status():
    """Get the current or latest scraping job execution status."""
    return ScrapeResponse(
        status=scrape_job_state["status"],
        message=scrape_job_state["message"],
        items_scraped=scrape_job_state["items_scraped"],
        scraped_at=scrape_job_state["scraped_at"],
    )


# --- SESSION MANAGEMENT ENDPOINTS ---
@router_v1.get("/sessions", response_model=List[SessionResponse])
@app.get("/sessions", response_model=List[SessionResponse], include_in_schema=False)
@app.get("/api/sessions", response_model=List[SessionResponse], include_in_schema=False)
async def get_all_sessions():
    """List all chat sessions."""
    return list_sessions()


@router_v1.post("/sessions", response_model=SessionResponse)
@app.post("/sessions", response_model=SessionResponse, include_in_schema=False)
@app.post("/api/sessions", response_model=SessionResponse, include_in_schema=False)
async def create_new_session(payload: SessionCreate):
    """Create a new chat session."""
    sid = create_session(title=payload.title or "New Chat")
    return SessionResponse(
        session_id=sid,
        title=payload.title or "New Chat",
        created_at="",
        updated_at="",
    )


@router_v1.get("/sessions/{session_id}/messages", response_model=List[MessageItem])
@app.get("/sessions/{session_id}/messages", response_model=List[MessageItem], include_in_schema=False)
@app.get("/api/sessions/{session_id}/messages", response_model=List[MessageItem], include_in_schema=False)
async def get_messages(session_id: str):
    """Get all messages for a specific session."""
    return get_session_messages(session_id)


@router_v1.delete("/sessions/{session_id}")
@app.delete("/sessions/{session_id}", include_in_schema=False)
@app.delete("/api/sessions/{session_id}", include_in_schema=False)
async def delete_chat_session(session_id: str):
    """Delete a session and its message history."""
    delete_session(session_id)
    return {"status": "deleted", "session_id": session_id}


# Mount router
app.include_router(router_v1)


if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host=settings.api.host,
        port=settings.api.port,
        reload=True,
    )
