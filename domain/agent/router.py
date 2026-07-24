"""
Agent 路由器 —— 意图判断与工具选择

职责（单一且明确）：
  基于当前 messages 与可用 tools，调用 LLM（带 tools 参数）判定本轮
  是否需要调用工具，产出标准化的 Decision。

设计要点：
  1. Router 只「判断」，不「执行」。工具执行在 AgentRunner 中完成，
     保持决策层与执行层分离（GPT 评审推荐的清晰边界）。
  2. 通过 DeepSeek 原生 function calling（tools + tool_choice="auto"）
     在一次 LLM 调用内完成「意图识别 + 工具选择」，比「先分类再执行」
     的独立 Router 少一次 LLM 调用。
  3. 无可用工具时直接返回 CHAT 决策，连判断用的 LLM 调用都省了。

依赖：
  依赖 LLMClient（基础设施层）。项目既有代码（PromptBuilder）已采用
  TYPE_CHECKING 隔离基础设施类型，这里同样处理，避免反向依赖固化。
"""

from typing import List, Dict, Any, TYPE_CHECKING

from .action import Action
from .decision import Decision

if TYPE_CHECKING:
    from infrastructure.llm.client import LLMClient


class AgentRouter:
    """
    Agent 路由器

    把「是否调工具、调哪个」的判断封装为一个方法 route()，
    返回 Decision 供 AgentRunner 消费。
    """

    def __init__(self, llm: "LLMClient"):
        """
        初始化路由器

        参数：
          llm   LLM 客户端实例（用于带 tools 参数的意图判断调用）
        """
        self._llm = llm

    async def route(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
    ) -> Decision:
        """
        对当前对话状态做意图路由

        参数：
          messages  当前对话 messages（OpenAI dict 列表）
          tools     可用工具的 OpenAI schema 列表（agent_mode 关闭时为空）

        返回：
          Decision —— action=TOOL（含 tool_calls）或 action=CHAT

        逻辑：
          - tools 为空 → 直接 CHAT，不浪费 LLM 调用
          - tools 非空 → 调 LLM（tool_choice=auto），由模型自行决定是否 tool_calls
        """
        if not tools:
            return Decision(action=Action.CHAT, tool_calls=[])

        resp = await self._llm.chat(
            messages,
            tools=tools,
            tool_choice="auto",
        )
        return Decision.from_llm_response(resp.tool_calls)
