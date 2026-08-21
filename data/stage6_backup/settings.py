from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    pg_dsn: str = "postgresql://postgres:postgres@localhost:15432/robot"
    knowledge_storage_path: str = "data/knowledge"
    upload_max_size_mb: int = 50

    rag_top_k: int = Field(default=5, ge=1, le=10)
    rag_min_score: float = Field(default=0.45, ge=-1.0, le=1.0)
    rag_max_query_chars: int = Field(default=2000, ge=1, le=10000)
    rag_max_context_chars: int = Field(default=12000, ge=1000, le=100000)
    rag_max_source_chars: int = Field(default=2400, ge=200, le=20000)
    rag_model_timeout_seconds: float = Field(default=60.0, gt=0, le=300)

    auto_router_timeout_seconds: float = Field(default=15.0, gt=0, le=60)
    auto_router_min_confidence: float = Field(default=0.65, ge=0.0, le=1.0)
    
    hybrid_max_context_chars: int = Field(default=10000, ge=1000, le=100000)
    hybrid_max_source_chars: int = Field(default=2000, ge=200, le=20000)
    hybrid_model_timeout_seconds: float = Field(default=60.0, gt=0, le=300)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()