from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import UUID

class ResearchSourceType(str, Enum):
    KNOWLEDGE = "knowledge"
    WEB = "web"

class ResearchStatus(str, Enum):
    CREATED = "created"
    PLANNING = "planning"
    SEARCHING = "searching"
    READING = "reading"
    VERIFYING = "verifying"
    WRITING = "writing"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset({
    ResearchStatus.COMPLETED,
    ResearchStatus.FAILED,
    ResearchStatus.CANCELLED,
})


ALLOWED_TRANSITIONS = {
    ResearchStatus.CREATED: {
        ResearchStatus.PLANNING,
        ResearchStatus.PAUSED,
        ResearchStatus.CANCELLED,
    },
    ResearchStatus.PLANNING: {
        ResearchStatus.SEARCHING,
        ResearchStatus.PAUSED,
        ResearchStatus.FAILED,
        ResearchStatus.CANCELLED,
    },
    ResearchStatus.SEARCHING: {
        ResearchStatus.READING,
        ResearchStatus.PAUSED,
        ResearchStatus.FAILED,
        ResearchStatus.CANCELLED,
    },
    ResearchStatus.READING: {
        ResearchStatus.WRITING,
        ResearchStatus.PAUSED,
        ResearchStatus.FAILED,
        ResearchStatus.CANCELLED,
    },
    ResearchStatus.WRITING: {
        ResearchStatus.VERIFYING,
        ResearchStatus.PAUSED,
        ResearchStatus.FAILED,
        ResearchStatus.CANCELLED,
    },
    ResearchStatus.VERIFYING: {
        ResearchStatus.COMPLETED,
        ResearchStatus.WRITING,
        ResearchStatus.PAUSED,
        ResearchStatus.FAILED,
        ResearchStatus.CANCELLED,
    },
    ResearchStatus.PAUSED: {
        ResearchStatus.CREATED,
        ResearchStatus.CANCELLED,
    },
    ResearchStatus.COMPLETED: set(),
    ResearchStatus.FAILED: set(),
    ResearchStatus.CANCELLED: set(),
}


def require_transition(current: str, target: str) -> None:
    try:
        current_status = ResearchStatus(current)
        target_status = ResearchStatus(target)
    except ValueError as exc:
        raise ValueError(f"未知研究状态迁移: {current} -> {target}") from exc
    if target_status not in ALLOWED_TRANSITIONS[current_status]:
        raise ValueError(f"非法研究状态迁移: {current} -> {target}")


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
