"""
行为枚举 —— Agent 在一次对话轮次中可采取的动作类别

设计说明：
  GPT 评审建议把「意图分类」显式化为枚举，便于未来扩展更多动作类型
  （如 SEARCH / MEMORY 当前都归入 TOOL 的实现，但保留独立枚举位便于区分统计与日志）。

  - CHAT：直接由 LLM 回答，无需调用任何工具
  - TOOL：需要调用一个或多个工具（function calling），执行后由 LLM 总结
  - SEARCH：预留 —— 联网搜索类工具（本质仍是 TOOL 的一种细分）
  - MEMORY：预留 —— 主动回忆类工具（本质仍是 TOOL 的一种细分）

注意：当前 AgentRouter 只产出 CHAT 或 TOOL 两种决策；
  SEARCH / MEMORY 保留为语义标签，方便将来在日志 / 监控里区分工具用途。
"""

from enum import Enum


class Action(Enum):
    """Agent 动作类别"""

    CHAT = "chat"        # 直接回答
    TOOL = "tool"        # 调用工具（function calling）
    SEARCH = "search"    # 预留：联网搜索
    MEMORY = "memory"    # 预留：主动回忆
