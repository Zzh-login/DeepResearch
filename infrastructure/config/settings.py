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

    research_worker_poll_seconds: float = Field(default=2.0, gt=0, le=30)
    research_max_attempts: int = Field(default=2, ge=1, le=5)
    research_max_search_queries: int = Field(default=5, ge=3, le=5)
    research_results_per_query: int = Field(default=5, ge=1, le=10)
    research_max_sources: int = Field(default=12, ge=1, le=30)
    research_max_context_chars: int = Field(default=50000, ge=5000, le=100000)
    research_fetch_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    research_max_response_bytes: int = Field(default=2_000_000, ge=100_000, le=10_000_000)
    research_max_source_chars: int = Field(default=8000, ge=500, le=30000)
    research_model_timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    research_task_timeout_seconds: float = Field(default=600.0, gt=60, le=1800)
    research_worker_heartbeat_seconds: float = Field(default=5.0, gt=1, le=30)
    research_worker_stale_seconds: float = Field(default=20.0, gt=5, le=120)
    research_max_active_tasks_per_user: int = Field(default=1, ge=1, le=10)
    research_idempotency_ttl_hours: int = Field(default=24, ge=1, le=168)
    conversation_page_size: int = Field(default=30, ge=10, le=100)
    message_page_size: int = Field(default=100, ge=20, le=500)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()