from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any


@dataclass
class ResearchCheckpointState:
    schema_version: int = 1
    query: str = ""
    scope: str = ""
    plan: dict[str, Any] = field(default_factory=dict)
    sources: list[dict[str, Any]] = field(default_factory=list)
    verified_evidence: list[dict[str, Any]] = field(default_factory=list)
    report_draft: dict[str, Any] = field(default_factory=dict)
    current_node: str = "created"
    attempt: int = 0
    usage_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)

    def state_hash(self):
        raw = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()