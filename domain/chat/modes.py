from enum import Enum


class ChatMode(str, Enum):
    NORMAL = "normal"
    KNOWLEDGE = "knowledge"
    HYBRID = "hybrid"
    DEEP_RESEARCH = "deep_research"
    AUTO = "auto"


ENABLED_CHAT_MODES = frozenset(
    {
        ChatMode.NORMAL,
        ChatMode.KNOWLEDGE,
        ChatMode.HYBRID,
        ChatMode.DEEP_RESEARCH,
        ChatMode.AUTO,
    }
)


def is_chat_mode_enabled(mode: ChatMode) -> bool:
    return mode in ENABLED_CHAT_MODES