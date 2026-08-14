from dataclasses import dataclass
from enum import Enum


class RouteSource(str, Enum):
    RULE = "rule"
    MODEL = "model"


class ResolvedChatMode(str, Enum):
    NORMAL = "normal"
    KNOWLEDGE = "knowledge"
    HYBRID = "hybrid"


@dataclass(frozen=True)
class RouteDecision:
    mode: ResolvedChatMode
    source: RouteSource
    confidence: float
    reason: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence 必须在 0 到 1 之间")
        if not self.reason.strip():
            raise ValueError("reason 不能为空")