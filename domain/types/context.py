"""
Agent 上下文 —— 一次对话轮次所需的全部输入

设计目的：
  把原先 PromptBuilder.build() 的多参数（user_input / history / user_id /
  include_vector / persona_override / tools）收敛为一个 dataclass，
  避免未来每加一个输入维度就要改一遍 build() 的签名（参数膨胀问题）。

依赖方向：
  domain 层内部类型，不依赖任何 infrastructure 实现。
  tools 字段只存 OpenAI 格式的「工具 schema 字典列表」，不持有工具实例，
  因此不会反向引入 infrastructure.tools。
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any


@dataclass
class AgentContext:
    """
    单次对话轮次的完整上下文

    字段说明：
      user_input      当前用户输入文本
      history         当前会话的 Message 列表（含 system 头部），由 JsonChatMemory 提供
      user_id         用户标识，用于 KV 记忆 / 向量记忆查询隔离
      include_vector  是否启用向量语义检索（未注入 vector_memory 时应为 False）
      persona_override 运行时生效的角色文本（set_persona 切换后从这里注入）
      tools           可用工具的 OpenAI function schema 列表（agent_mode 开启时非空）
    """

    user_input: str
    history: list
    user_id: str = "default"
    include_vector: bool = False
    persona_override: Optional[str] = None
    tools: List[Dict[str, Any]] = field(default_factory=list)
