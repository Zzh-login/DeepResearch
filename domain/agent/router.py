"""Agent routing: ask the LLM to select a tool, with policy overrides."""

from typing import Any, Dict, List, TYPE_CHECKING

from .action import Action
from .decision import Decision
from .policy_guard import PolicyGuard

if TYPE_CHECKING:
    from infrastructure.llm.client import LLMClient


class AgentRouter:
    """Convert the current conversation into a normalized Agent Decision."""

    def __init__(
        self,
        llm: "LLMClient",
        policy_guard: PolicyGuard | None = None,
    ):
        self._llm = llm
        self._policy_guard = policy_guard or PolicyGuard()

    async def route(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        capabilities: Dict[str, str] | None = None,
    ) -> Decision:
        # With no registered tools, avoid an unnecessary LLM tool-routing call.
        if not tools:
            return Decision(action=Action.CHAT, tool_calls=[])

        # PolicyGuard may override auto selection for high-confidence intents.
        forced_tool_choice = self._policy_guard.tool_choice(
            messages,
            tools,
            capabilities or {},
        )
        response = await self._llm.chat(
            messages,
            tools=tools,
            # None means the model keeps its normal auto tool-selection behavior.
            tool_choice=forced_tool_choice or "auto",
        )
        return Decision.from_llm_response(response.tool_calls)
