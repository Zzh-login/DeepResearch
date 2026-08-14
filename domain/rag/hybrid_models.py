from dataclasses import dataclass, field
from enum import Enum

from domain.rag.models import Citation


class HybridAnswerStatus(str, Enum):
    BLENDED = "blended"
    MODEL_ONLY = "model_only"
    CITATION_REJECTED = "citation_rejected"


@dataclass
class HybridAnswer:
    query: str
    answer: str
    status: HybridAnswerStatus
    citations: list[Citation] = field(default_factory=list)
    retrieved_count: int = 0
    citation_valid: bool = True