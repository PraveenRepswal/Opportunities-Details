from pydantic_settings import BaseSettings
from typing import Dict

class ScraperSettings(BaseSettings):
    days_back: int = 30
    score_threshold: float = 0.7
    
    urls: Dict[str, str] = {
        "youthop": "https://www.youthop.com/sitemap_index.xml",
        "greatyop": "https://greatyop.com/sitemap_index.xml",
        "scholars4dev": "https://www.scholars4dev.com/sitemap.xml",
        "scholarshipscorner": "https://scholarshipscorner.website/sitemap_index.xml",
        "opportunitiescorner": "https://opportunitiescorners.com/sitemap_index.xml",
        "opportunitiesforyouth": "https://opportunitiesforyouth.org/sitemap-1.xml"
    }

class ModelSettings(BaseSettings):
    # embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_model: str ="intfloat/e5-small-v2"

class Settings(BaseSettings):
    scraper: ScraperSettings = ScraperSettings()
    model: ModelSettings = ModelSettings()

settings = Settings()