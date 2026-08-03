import os
from pathlib import Path
from typing import Dict, List
from pydantic_settings import BaseSettings, SettingsConfigDict


class ScraperSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    days_back: int = 30
    score_threshold: float = 0.7

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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    scraper: ScraperSettings = ScraperSettings()
    model: ModelSettings = ModelSettings()
    api: APISettings = APISettings()


settings = Settings()