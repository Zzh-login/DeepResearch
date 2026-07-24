"""
决策结果 —— AgentRouter 对一次输入的意图判定产物

设计说明：
  Router 调用 LLM（携带 tools 参数）后，把返回结果标准化为 Decision，
  使 AgentRunner 无需关心 LLM 返回的原始结构差异。

  关键映射：
    - LLM 返回 tool_calls（非空）→ action=TOOL，tool_calls 原样保留
    - LLM 返回普通文本（finish_reason=stop）→ action=CHAT，tool_calls=[]

  提供 from_llm_response() 工厂方法，从 LLMResponse 直接构造，
  保持「Router 只做判断、不碰执行」的单一职责。
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

from .action import Action


@dataclass
class Decision:
    """
    Router 的决策输出

    字段说明：
      action      判定的动作类别（CHAT / TOOL）
      tool_calls  需要执行的工具调用列表（CHAT 时为空）
      reason      人类可读的判定理由（调试 / 日志用，可空）
    """

    action: Action
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    reason: str = ""

    @classmethod
    def from_llm_response(cls, tool_calls: List[Dict[str, Any]], reason: str = "") -> "Decision":
        """
        从 LLM 解析出的 tool_calls 构造 Decision

        参数：
          tool_calls  LLM 返回的工具调用列表（可能为空）
          reason      可选的人类可读判定理由
        返回：
          tool_calls 非空 → Action.TOOL；否则 → Action.CHAT
        """
        if tool_calls:
            return cls(action=Action.TOOL, tool_calls=tool_calls, reason=reason)
        return cls(action=Action.CHAT, tool_calls=[], reason=reason)
