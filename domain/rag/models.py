from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from uuid import UUID


class RagAnswerStatus(str, Enum):
    GROUNDED = "grounded"
    INSUFFICIENT_CONTEXT = "insufficient_context"
    CITATION_REJECTED = "citation_rejected"


@dataclass(frozen=True)
class RetrievedSource:
    source_id: str
    chunk_id: UUID
    document_id: UUID
    chunk_index: int
    filename: str
    content: str
    score: float
    page_number: Optional[int] = None
    section_title: Optional[str] = None


@dataclass(frozen=True)
class Citation:
    source_id: str
    chunk_id: UUID
    document_id: UUID
    chunk_index: int
    filename: str
    excerpt: str
    score: float
    page_number: Optional[int] = None
    section_title: Optional[str] = None


@dataclass
class RagAnswer:
    query: str
    answer: str
    status: RagAnswerStatus
    citations: list[Citation] = field(default_factory=list)
    retrieved_count: int = 0
    citation_valid: bool = True