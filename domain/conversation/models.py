from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class Conversation:
    id: UUID
    owner_id: str
    title: str
    archived: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class StoredMessage:
    id: UUID
    conversation_id: UUID
    owner_id: str
    role: str
    content: str
    requested_mode: str
    resolved_mode: str | None
    knowledge_base_id: UUID | None
    research_task_id: UUID | None
    route_metadata: dict[str, Any] = field(default_factory=dict)
    citations: list[dict[str, Any]] = field(default_factory=list)