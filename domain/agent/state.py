"""
Agent 状态 —— 单次决策过程中流动的数据载体

设计说明：
  把「一次工具调用循环」中需要传递的字段集中到一个 dataclass，
  避免 AgentRunner 内部用一长串返回值在函数间传递（可读性差、易漏字段）。

  典型生命周期：
    ChatSession 构造初始 state
      → AgentRouter.route() 读取 messages/tools，产出 Decision 写入 state
      → AgentRunner 按 state.need_tool / state.tool_calls 执行工具、追加消息
      → 最终 state.messages 包含完整的 tool 往返记录
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any

from .action import Action


@dataclass
class AgentState:
    """
    Agent 单次轮次的运行状态

    字段说明：
      user_message   原始用户输入（用于日志 / 兜底）
      messages       当前对话 messages（OpenAI dict 列表），随工具往返不断追加
      tools          可用工具的 OpenAI schema 列表
      need_tool      是否进入了工具调用分支
      action         Router 判定的动作类别
      tool_calls     待执行 / 已解析的工具调用列表，元素形如
                     {"id": str, "name": str, "arguments": dict}
    """

    user_message: str
    messages: List[Dict[str, Any]]
    tools: List[Dict[str, Any]] = field(default_factory=list)
    need_tool: bool = False
    action: Action = Action.CHAT
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
