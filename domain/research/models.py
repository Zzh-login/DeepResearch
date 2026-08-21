from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import UUID


class ResearchStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ResearchSourceType(str, Enum):
    KNOWLEDGE = "knowledge"
    WEB = "web"


@dataclass(frozen=True)
class ResearchSource:
    source_id: str
    source_type: ResearchSourceType
    title: str
    excerpt: str
    content: str
    score: float
    url: str | None = None
    filename: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResearchTask:
    id: UUID
    owner_id: str
    query: str
    status: ResearchStatus
    progress: int
    current_step: str
    knowledge_base_id: UUID | None = None
    report: dict[str, Any] | None = None
    error_message: str | None = None