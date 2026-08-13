from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from domain.rag.models import RagAnswerStatus


class RagAskRequest(BaseModel):
    query: str = Field(min_length=1, max_length=10000)
    top_k: int | None = Field(default=None, ge=1, le=10)

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("问题不能为空")
        return clean


class CitationResponse(BaseModel):
    source_id: str
    chunk_id: UUID
    document_id: UUID
    chunk_index: int
    filename: str
    excerpt: str
    score: float
    page_number: int | None = None
    section_title: str | None = None


class RagAskResponse(BaseModel):
    query: str
    answer: str
    status: RagAnswerStatus
    citations: list[CitationResponse]
    retrieved_count: int
    citation_valid: bool