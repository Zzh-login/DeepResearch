"""Deterministic policy checks applied before LLM tool selection."""

from typing import Any, Dict, List, Optional


class PolicyGuard:
    """Force a tool only when a deterministic rule has high confidence."""

    # A current-context word alone is too broad to force a tool call.
    _CURRENT_WORDS = ("\u73b0\u5728", "\u5f53\u524d", "\u6b64\u523b", "\u76ee\u524d", "\u4eca\u5929", "\u5f53\u4e0b")
    # A time word alone can appear in non-time questions, so it is not enough.
    _TIME_WORDS = ("\u65f6\u95f4", "\u51e0\u70b9", "\u65e5\u671f", "\u51e0\u53f7", "\u661f\u671f", "\u5468\u51e0", "\u949f\u70b9")

    def tool_choice(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        capabilities: Dict[str, str],
    ) -> Optional[Dict[str, Any]]:
        # Only the latest user message controls this deterministic policy.
        content = self._latest_user_content(messages)
        has_current = any(word in content for word in self._CURRENT_WORDS)
        has_time = any(word in content for word in self._TIME_WORDS)

        # Requiring both groups reduces false positives such as date-format questions.
        if not (has_current and has_time):
            return None

        for tool in tools:
            name = tool.get("function", {}).get("name")
            # Resolve the concrete name through the declared capability.
            if capabilities.get(name) == "current_time":
                return {"type": "function", "function": {"name": name}}

        # If no matching capability is registered, keep normal LLM selection.
        return None

    @staticmethod
    def _latest_user_content(messages: List[Dict[str, Any]]) -> str:
        # Search backwards because assistant and tool messages may follow it.
        for message in reversed(messages):
            if message.get("role") == "user":
                return str(message.get("content") or "")
        return ""
