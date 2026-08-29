from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class MemoryContext:
    user_id: str
    conversation_id: UUID
    mode: str
    knowledge_base_id: UUID | None = None
    research_task_id: UUID | None = None
    include_user_memory: bool = False
    include_conversation: bool = True
    user_memory_context: str = ""