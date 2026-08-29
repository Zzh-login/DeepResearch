from dataclasses import dataclass
from enum import Enum
from uuid import UUID


class ArtifactKind(str, Enum):
    MARKDOWN = "markdown"
    DOCX = "docx"
    PDF = "pdf"


class ExportJobStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class ResearchArtifact:
    id: UUID
    task_id: UUID
    kind: ArtifactKind
    filename: str
    storage_key: str
    sha256: str
    size_bytes: int
    expires_at: object | None = None


@dataclass(frozen=True)
class GeneratedArtifact:
    filename: str
    storage_key: str
    sha256: str
    size_bytes: int
