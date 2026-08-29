import os
from pathlib import Path
from typing import Dict, List
from pydantic_settings import BaseSettings, SettingsConfigDict


class ScraperSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    days_back: int = 30
    score_threshold: float = 0.7

    extract_metadata: bool = True
    llm_enrichment: bool = True
    llm_enrichment_concurrency: int = 2
    llm_enrichment_timeout: float = 45.0
    metadata_content_chars: int = 4000

    urls: Dict[str, str] = {
        "youthop": "https://www.youthop.com/sitemap_index.xml",
        "greatyop": "https://greatyop.com/sitemap_index.xml",
        "scholars4dev": "https://www.scholars4dev.com/sitemap.xml",
        "scholarshipscorner": "https://scholarshipscorner.website/sitemap_index.xml",
        "opportunitiescorner": "https://opportunitiescorners.com/sitemap_index.xml",
        "opportunitiesforyouth": "https://opportunitiesforyouth.org/sitemap-1.xml",
    }


class ModelSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    embedding_model: str = "intfloat/e5-small-v2"
    main_model: str = os.getenv("MAIN_MODEL_PATH", "models/Qwen3.5-4B-IQ4_NL.gguf")
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    llamacpp_server_url: str = os.getenv("LLAMACPP_SERVER_URL", "http://localhost:8080")
    stt_model: str = os.getenv("STT_MODEL_NAME", "UsefulSensors/moonshine-tiny")
    stt_device: str = os.getenv("STT_DEVICE", "cpu")  # "cpu", "cuda", or "auto"

    # Semantic answer cache (single-turn requests only; invalidated on re-index)
    semantic_cache_enabled: bool = os.getenv("SEMANTIC_CACHE_ENABLED", "true").lower() in ("true", "1", "yes")
    semantic_cache_similarity_threshold: float = float(os.getenv("SEMANTIC_CACHE_SIMILARITY_THRESHOLD", "0.93"))
    semantic_cache_ttl_hours: float = float(os.getenv("SEMANTIC_CACHE_TTL_HOURS", "24"))
    semantic_cache_max_entries: int = int(os.getenv("SEMANTIC_CACHE_MAX_ENTRIES", "500"))

    @property
    def resolved_main_model_path(self) -> str:
        """Resolve model path, prioritizing configured path, with fallbacks for local Windows and Docker environments."""
        p = Path(self.main_model)
        if p.exists():
            return str(p)
        win_fallback = Path(r"X:\HuggingFace\models\Qwen3.5-4B-IQ4_NL.gguf")
        if win_fallback.exists():
            return str(win_fallback)
        docker_fallback = Path("/app/models/Qwen3.5-4B-IQ4_NL.gguf")
        if docker_fallback.exists():
            return str(docker_fallback)
        relative_fallback = Path("./models/Qwen3.5-4B-IQ4_NL.gguf")
        if relative_fallback.exists():
            return str(relative_fallback)
        return self.main_model


class APISettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    host: str = os.getenv("API_HOST", "127.0.0.1")
    port: int = int(os.getenv("API_PORT", "8000"))
    cors_origins: List[str] = ["*"]

    # Rate limiting: per-client-IP sliding window, tiered by endpoint cost.
    rate_limit_enabled: bool = True
    rate_limit_trust_proxy: bool = os.getenv("RATE_LIMIT_TRUST_PROXY", "false").lower() in ("true", "1", "yes")
    rate_limit_chat_per_minute: int = int(os.getenv("RATE_LIMIT_CHAT_PER_MINUTE", "10"))
    rate_limit_transcribe_per_minute: int = int(os.getenv("RATE_LIMIT_TRANSCRIBE_PER_MINUTE", "15"))
    rate_limit_scrape_per_minute: int = int(os.getenv("RATE_LIMIT_SCRAPE_PER_MINUTE", "5"))
    rate_limit_default_per_minute: int = int(os.getenv("RATE_LIMIT_DEFAULT_PER_MINUTE", "120"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    debug: bool = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")
    scraper: ScraperSettings = ScraperSettings()
    model: ModelSettings = ModelSettings()
    api: APISettings = APISettings()


settings = Settings()