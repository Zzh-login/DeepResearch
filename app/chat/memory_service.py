from app.chat.memory_policy import (
    allowed_user_memory_types,
)
from infrastructure.memory.json_long_memory import (
    JsonLongMemory,
)


class UnifiedMemoryService:
    """统一的用户长期记忆读取服务。

    当前使用 JsonLongMemory 作为兼容存储。
    后续迁移 PostgreSQL 时，只替换这里的存储实现。
    """

    def __init__(self, user_id: str):
        self._source = f"user_{user_id}"
        self._memory = JsonLongMemory(self._source)

    def render_for_mode(self, mode: str) -> str:
        """按照回答模式读取允许的长期记忆。"""
        allowed_types = allowed_user_memory_types(mode)

        if not allowed_types:
            return ""

        return self._memory.render_context(
            allowed_types=allowed_types,
        )