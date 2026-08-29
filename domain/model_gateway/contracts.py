from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID, uuid4


ModelMode = Literal[
    "normal",
    "auto",
    "knowledge",
    "hybrid",
    "deep_research",
    "ingestion",
    "system",
]


@dataclass(frozen=True)
class ModelRequestContext:
    owner_id: str
    mode: ModelMode
    operation: str
    request_id: UUID = field(default_factory=uuid4)
    conversation_id: UUID | None = None
    research_task_id: UUID | None = None
    idempotent: bool = True


@dataclass(frozen=True)
class ModelProfile:
    operation: str
    temperature: float = 0.1
    max_tokens: int = 2048
    timeout_seconds: float = 60.0
    response_format: dict[str, Any] | None = None


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated: bool = False


@dataclass(frozen=True)
class ChatResult:
    text: str
    raw: Any
    provider: str
    model: str
    usage: TokenUsage
    latency_ms: int


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: list[list[float]]
    provider: str
    model: str
    latency_ms: int


@dataclass(frozen=True)
class RerankItem:
    index: int
    score: float
    text: str


@dataclass(frozen=True)
class RerankResult:
    items: list[RerankItem]
    provider: str
    model: str
    latency_ms: int