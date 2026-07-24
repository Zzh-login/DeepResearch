"""
上下文构建器 —— 把会话状态收敛成 AgentContext 并委托 PromptBuilder 生成 messages

定位：Session 与 PromptBuilder 之间的薄适配层。
  - Session 持有原始状态（history / user_id / persona 等）
  - ContextBuilder 负责把它们打包成 AgentContext（domain 层的标准输入结构）
  - 再调用 PromptBuilder.build(ctx) 产出最终 messages

这样 Session 不直接拼装 prompt，职责更清晰；未来 AgentContext 加字段，
只改这里和 AgentContext 定义，不动 Session 与 PromptBuilder 的对接逻辑。
"""

from typing import Optional, List, Dict, Any

from domain.types.context import AgentContext
from domain.prompt.builder import PromptBuilder


class ContextBuilder:
    """把会话输入组装为 AgentContext 并构建 prompt messages"""

    def __init__(self, builder: PromptBuilder):
        """
        初始化上下文构建器

        参数：
          builder  已注入记忆数据源的 PromptBuilder 实例
        """
        self._builder = builder

    async def build(
        self,
        user_text: str,
        history: list,
        user_id: str,
        include_vector: bool,
        persona_override: Optional[str],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> tuple[str, List[Dict[str, Any]]]:
        """
        构建本轮对话的 system_prompt 与 messages

        参数：
          user_text          当前用户输入
          history            Session 的内存历史（Message 列表）
          user_id           用户标识
          include_vector    是否启用向量检索
          persona_override  运行时生效的角色文本
          tools             可用工具 schema（AgentContext.tools，预留给后续扩展）

        返回：
          (system_prompt, messages) —— 与 PromptBuilder.build 一致
        """
        ctx = AgentContext(
            user_input=user_text,
            history=history,
            user_id=user_id,
            include_vector=include_vector,
            persona_override=persona_override,
            tools=tools or [],
        )
        return await self._builder.build(ctx)
