"""
对话管道 —— 六层编排的汇聚点

职责：
  把一次对话轮次的「拼装 prompt → Agent 运行 → 校验输出」三步串起来，
  对 Session 暴露简洁的 generate() / generate_stream() 入口。

六层映射（与设计文档一致）：
  config   → PromptBuilder 加载 persona/rules/format
  persona  → Session 提供的 persona_override
  prompt   → ContextBuilder + PromptBuilder 产出 messages
  agent    → AgentRunner 执行 Router + 工具循环
  llm      → LLMClient 生成回答
  validate → Validator 校验 + 截断兜底
  output   → 返回最终文本（记忆写入由 Session 负责，不在此层）

为什么独立成类：
  - 可测试：单独测「prompt→agent→validate」链路，不依赖 Session 的历史管理
  - 可替换：未来多 Agent 协作时，每个 Agent 用自己的 Pipeline
  - 职责单一：Session 只管历史/记忆/并发，Pipeline 只管单轮内容生成
"""

from typing import Optional, Tuple, AsyncGenerator

from .context_builder import ContextBuilder
from .agent_runner import AgentRunner
from domain.prompt.validator import Validator


class Pipeline:
    """单轮对话内容生成的六层管道"""

    def __init__(
        self,
        context_builder: ContextBuilder,
        agent_runner: AgentRunner,
        validator: Validator,
    ):
        """
        初始化管道

        参数：
          context_builder  上下文构建器（拼装 messages）
          agent_runner    Agent 运行器（工具循环 + LLM）
          validator       输出校验器
        """
        self._cb = context_builder
        self._runner = agent_runner
        self._validator = validator

    async def generate(
        self,
        user_text: str,
        history: list,
        user_id: str,
        include_vector: bool,
        persona_override: Optional[str],
    ) -> Tuple[str, str, str]:
        """
        非流式生成一轮回答

        返回：
          (final_reply, finish_reason, raw_reply)
            final_reply   经 Validator 校验/兜底后的最终文本
            finish_reason LLM 结束原因（用于截断检测）
            raw_reply     LLM 原始输出（未校验）
        """
        # 1. 拼装 prompt
        _, messages = await self._cb.build(
            user_text, history, user_id, include_vector, persona_override
        )
        # 2. Agent 运行（含工具循环）
        resp = await self._runner.run(messages)
        # 3. 校验 + 截断兜底
        final = self._validator.validate_with_fallback(
            resp.text, truncated=resp.truncated
        )
        return final, resp.finish_reason, resp.text

    async def generate_stream(
        self,
        user_text: str,
        history: list,
        user_id: str,
        include_vector: bool,
        persona_override: Optional[str],
    ) -> AsyncGenerator[str, None]:
        """
        流式生成一轮回答（逐 token 产出，供 WebSocket 推前端打字机效果）
        """
        _, messages = await self._cb.build(
            user_text, history, user_id, include_vector, persona_override
        )
        async for token in self._runner.run_stream(messages):
            yield token
