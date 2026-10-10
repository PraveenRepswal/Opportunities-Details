import os
from pathlib import Path
from typing import Dict, List
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# Directories searched (by file name) when a configured model path does not exist.
# Override with MODEL_SEARCH_DIRS in .env (os.pathsep-separated), e.g. "D:\Models;/mnt/models".
_DEFAULT_MODEL_DIRS = ["models", "models/slm", "models/llm", "/app/models"]


def _resolve_model_path(configured: str, search_dirs: List[str] | None = None) -> str:
    """Return `configured` if it exists, else the first same-named file in the search dirs."""
    p = Path(configured)
    if p.exists():
        return str(p)
    dirs = search_dirs or _DEFAULT_MODEL_DIRS
    for d in dirs:
        candidate = Path(d) / p.name
        if candidate.exists():
            return str(candidate)
    return configured


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

    embedding_model: str = Field(
        default="intfloat/e5-small-v2",
        validation_alias=AliasChoices("EMBEDDING_MODEL", "MODEL__EMBEDDING_MODEL", "embedding_model"),
    )
    main_model: str = Field(
        default="models/Qwen3.5-4B-IQ4_NL.gguf",
        validation_alias=AliasChoices("MAIN_MODEL_PATH", "MODEL__MAIN_MODEL", "main_model"),
    )
    model_type: str = Field(
        default="LLamaCPP",
        validation_alias=AliasChoices("MODEL_TYPE", "MODEL__MODEL_TYPE", "model_type"),
    )
    ollama_base_url: str = Field(
        default="http://localhost:11434",
        validation_alias=AliasChoices("OLLAMA_BASE_URL", "MODEL__OLLAMA_BASE_URL", "ollama_base_url"),
    )
    llamacpp_server_url: str = Field(
        default="http://localhost:8080",
        validation_alias=AliasChoices("LLAMACPP_SERVER_URL", "MODEL__LLAMACPP_SERVER_URL", "llamacpp_server_url"),
    )
    stt_model: str = Field(
        default="UsefulSensors/moonshine-tiny",
        validation_alias=AliasChoices("STT_MODEL_NAME", "MODEL__STT_MODEL", "stt_model"),
    )
    stt_device: str = Field(
        default="cpu",
        validation_alias=AliasChoices("STT_DEVICE", "MODEL__STT_DEVICE", "stt_device"),
    )

    # Semantic answer cache (single-turn requests only; invalidated on re-index)
    semantic_cache_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("SEMANTIC_CACHE_ENABLED", "MODEL__SEMANTIC_CACHE_ENABLED", "semantic_cache_enabled"),
    )
    semantic_cache_similarity_threshold: float = Field(
        default=0.93,
        validation_alias=AliasChoices("SEMANTIC_CACHE_SIMILARITY_THRESHOLD", "MODEL__SEMANTIC_CACHE_SIMILARITY_THRESHOLD", "semantic_cache_similarity_threshold"),
    )
    semantic_cache_ttl_hours: float = Field(
        default=24.0,
        validation_alias=AliasChoices("SEMANTIC_CACHE_TTL_HOURS", "MODEL__SEMANTIC_CACHE_TTL_HOURS", "semantic_cache_ttl_hours"),
    )
    semantic_cache_max_entries: int = Field(
        default=500,
        validation_alias=AliasChoices("SEMANTIC_CACHE_MAX_ENTRIES", "MODEL__SEMANTIC_CACHE_MAX_ENTRIES", "semantic_cache_max_entries"),
    )

    # Pipeline & Model paths (configured centrally from .env or defaults)
    slm_model: str = Field(
        default="models/Qwen3.5-0.8B-UD-Q5_K_XL.gguf",
        validation_alias=AliasChoices("SLM_MODEL_PATH", "MODEL__SLM_MODEL", "slm_model"),
    )
    llm_model: str = Field(
        default="models/Qwen3.5-4B-IQ4_NL.gguf",
        validation_alias=AliasChoices("LLM_MODEL_PATH", "MAIN_MODEL_PATH", "MODEL__LLM_MODEL", "llm_model"),
    )
    llamacpp_server_port: int = Field(
        default=8080,
        validation_alias=AliasChoices("LLAMACPP_SERVER_PORT", "MODEL__LLAMACPP_SERVER_PORT", "llamacpp_server_port"),
    )
    reasoning_budget: int = Field(
        default=1000,
        validation_alias=AliasChoices("REASONING_BUDGET", "MODEL__REASONING_BUDGET", "reasoning_budget"),
    )
    model_search_dirs: str = Field(
        default=os.pathsep.join(_DEFAULT_MODEL_DIRS),
        validation_alias=AliasChoices("MODEL_SEARCH_DIRS", "model_search_dirs"),
    )

    @property
    def model_search_dir_list(self) -> List[str]:
        return [d for d in self.model_search_dirs.split(os.pathsep) if d]

    @property
    def resolved_slm_model_path(self) -> str:
        """Resolve SLM model path with local Windows and Docker fallback locations."""
        return _resolve_model_path(self.slm_model, self.model_search_dir_list)

    @property
    def resolved_llm_model_path(self) -> str:
        """Resolve chat LLM model path with local Windows and Docker fallback locations."""
        return _resolve_model_path(self.llm_model, self.model_search_dir_list)

    @property
    def resolved_main_model_path(self) -> str:
        """Resolve the main chat model path (same search order as the LLM path)."""
        return _resolve_model_path(self.main_model, self.model_search_dir_list)


class APISettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    host: str = Field(default="127.0.0.1", validation_alias=AliasChoices("API_HOST", "API__HOST", "host"))
    port: int = Field(default=8000, validation_alias=AliasChoices("API_PORT", "API__PORT", "port"))
    cors_origins: List[str] = [
        o.strip()
        for o in os.getenv("API_CORS_ORIGINS", "http://localhost:8501,http://127.0.0.1:8501,http://localhost:8000,http://127.0.0.1:8000").split(",")
        if o.strip()
    ]

    # Rate limiting: per-client-IP sliding window, tiered by endpoint cost.
    rate_limit_enabled: bool = Field(default=True, validation_alias=AliasChoices("RATE_LIMIT_ENABLED", "API__RATE_LIMIT_ENABLED", "rate_limit_enabled"))
    rate_limit_trust_proxy: bool = Field(default=False, validation_alias=AliasChoices("RATE_LIMIT_TRUST_PROXY", "API__RATE_LIMIT_TRUST_PROXY", "rate_limit_trust_proxy"))
    rate_limit_chat_per_minute: int = Field(default=10, validation_alias=AliasChoices("RATE_LIMIT_CHAT_PER_MINUTE", "API__RATE_LIMIT_CHAT_PER_MINUTE", "rate_limit_chat_per_minute"))
    rate_limit_transcribe_per_minute: int = Field(default=15, validation_alias=AliasChoices("RATE_LIMIT_TRANSCRIBE_PER_MINUTE", "API__RATE_LIMIT_TRANSCRIBE_PER_MINUTE", "rate_limit_transcribe_per_minute"))
    rate_limit_scrape_per_minute: int = Field(default=5, validation_alias=AliasChoices("RATE_LIMIT_SCRAPE_PER_MINUTE", "API__RATE_LIMIT_SCRAPE_PER_MINUTE", "rate_limit_scrape_per_minute"))
    rate_limit_default_per_minute: int = Field(default=120, validation_alias=AliasChoices("RATE_LIMIT_DEFAULT_PER_MINUTE", "API__RATE_LIMIT_DEFAULT_PER_MINUTE", "rate_limit_default_per_minute"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    debug: bool = Field(default=False, validation_alias=AliasChoices("DEBUG", "debug"))
    scraper: ScraperSettings = ScraperSettings()
    model: ModelSettings = ModelSettings()
    api: APISettings = APISettings()


settings = Settings()
