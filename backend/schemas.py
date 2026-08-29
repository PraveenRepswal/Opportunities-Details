from pydantic import BaseModel, Field
from typing import List, Optional

class ChatMessage(BaseModel):
    role: str = Field(..., description="Role of the message sender, e.g., 'user' or 'assistant'")
    content: str = Field(..., description="Content of the message")
    metadata: Optional[dict] = Field(default=None, description="Optional message metadata")

class ChatRequest(BaseModel):
    prompt: str = Field(..., description="User query prompt")
    session_id: Optional[str] = Field(default=None, description="Optional chat session ID for database persistence")
    conversation_history: List[ChatMessage] = Field(default_factory=list, description="Recent conversation history")
    provider: str = Field(default="Ollama", description="LLM provider: 'Ollama' or 'LLamaCPP'")
    think: bool = Field(default=False, description="Enable thinking/reasoning mode if supported")
    rerank: bool = Field(default=True, description="Enable re-ranking of retrieved document chunks")
    debug: bool = Field(default=False, description="Enable debug mode to expose prompt and retrieval details")
    ollama_base_url: Optional[str] = Field(default=None, description="Optional override for Ollama API server base URL")
    llamacpp_server_url: Optional[str] = Field(default=None, description="Optional override for llama.cpp HTTP server URL")

class ChatResponse(BaseModel):
    session_id: str
    response: str
    metadata: Optional[dict] = None

class OpportunityCard(BaseModel):
    title: str = Field(..., description="Title or name of the opportunity")
    organization: Optional[str] = Field(None, description="Host organization or university")
    location: Optional[str] = Field(None, description="Country or 'Remote'/'Hybrid'")
    deadline: Optional[str] = Field(None, description="Application closing date")
    type: Optional[str] = Field(None, description="Category, e.g. 'Fellowship', 'Fully Funded'")
    application_url: Optional[str] = Field(None, description="Direct URL or portal link")

class StructuredChatResponse(BaseModel):
    answer_summary: str = Field(..., description="Friendly intro/outro conversational message")
    cards: List[OpportunityCard] = Field(default_factory=list, description="Structured cards for items with metadata")

class SessionCreate(BaseModel):
    title: Optional[str] = Field(default="New Chat", description="Optional title for the chat session")

class SessionResponse(BaseModel):
    session_id: str
    title: str
    created_at: str
    updated_at: str

class MessageItem(BaseModel):
    id: int
    session_id: str
    role: str
    content: str
    metadata: Optional[dict] = None
    created_at: str

class HealthResponse(BaseModel):
    status: str = "ok"
    docs_count: int = 0
    device: str = "cpu"
    days_back: int = 30
    ollama_base_url: str = ""
    llamacpp_server_url: str = ""
    cache_hits: int = 0
    cache_misses: int = 0
    cache_entries: int = 0

class OpportunityItem(BaseModel):
    id: int
    title: str
    content: str
    source: Optional[str] = None
    url: Optional[str] = None
    created_at: str
    deadline: Optional[str] = Field(None, description="Application deadline in ISO format (YYYY-MM-DD)")
    metadata: Optional[dict] = Field(None, description="Extracted metadata: organization, location, type, deadline")

class OpportunitiesResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: List[OpportunityItem]

class ScrapeRequest(BaseModel):
    days_back: int = Field(default=30, description="Scrape posts published in last N days")
    score_threshold: float = Field(default=0.7, description="Relevance score threshold for scraping")
    reindex: bool = Field(default=True, description="Automatically re-index RAG vectorstore after scraping")

class ScrapeResponse(BaseModel):
    status: str
    message: str
    items_scraped: Optional[int] = 0
    scraped_at: str

class TranscribeResponse(BaseModel):
    text: str
    success: bool = True
    device_used: str = "cpu"
    duration_seconds: float = 0.0
    inference_time_seconds: Optional[float] = 0.0
    error: Optional[str] = None


