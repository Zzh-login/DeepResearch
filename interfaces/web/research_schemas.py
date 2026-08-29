from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class ExportRequest(BaseModel):
    kind: Literal["markdown", "docx", "pdf"]


class ResearchTaskCreate(BaseModel):
    query: str = Field(min_length=3, max_length=5000)
    conversation_id: UUID
    knowledge_base_id: UUID | None = None

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        clean = value.strip()
        if len(clean) < 3:
            raise ValueError("研究问题至少 3 个字符")
        return clean