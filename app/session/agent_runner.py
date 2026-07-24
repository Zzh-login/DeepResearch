"""
Agent 运行器 —— 决策层（Router）与执行层（ToolRegistry）的编排中枢

职责：
  把「Router 判定 → 工具执行 → LLM 总结」这一核心循环封装起来，
  对外只暴露 run()（非流式）与 run_stream()（流式）两个入口。

设计要点：
  1. 工具循环（Tool Loop）：
     - 调 Router 判断是否需要工具
     - 需要 → 把 assistant(tool_calls) + 各 tool 结果追加进 messages
     - 再调 LLM（不带 tools，让它基于结果总结）
     - 若 LLM 仍要调工具 → 继续循环，直到不再请求工具或达到上限
  2. 防死循环：MAX_TOOL_ITERATIONS 上限（默认 3），避免模型反复调工具卡死
  3. agent_mode 开关：关闭时 tools 列表为空，Router 直接判 CHAT，
     退化为「纯 LLM 对话」，与改造前行为一致（可一键回退）
  4. 失败降级：工具执行异常由 ToolRegistry 兜底返回错误字符串，
     不会中断整个对话

与 DeepSeek function calling 的关系：
  这里用 tool_choice="auto" 让模型在一次调用里自行决定调不调工具，
  比 GPT 评审里「先分类再执行」的独立 Router 省一次 LLM 调用。
"""

import json
from typing import List, Dict, Any, AsyncGenerator

from domain.agent.router import AgentRouter
from domain.agent.decision import Decision
from domain.agent.action import Action
from infrastructure.tools.registry import ToolRegistry
from infrastructure.llm.client import LLMClient, LLMResponse


# 工具循环最大迭代次数（防模型反复调工具死循环）
MAX_TOOL_ITERATIONS = 3


class AgentRunner:
    """运行 Agent 决策 + 工具循环，产出最终 LLM 回答"""

    def __init__(self, llm: LLMClient, registry: ToolRegistry, agent_mode: bool = True):
        """
        初始化 Agent 运行器

        参数：
          llm         LLM 客户端
          registry    工具注册表（含已注册工具）
          agent_mode  是否开启 Agent 能力（关闭则纯对话）
        """
        self._llm = llm
        self._registry = registry
        self._agent_mode = agent_mode
        self._router = AgentRouter(llm)

    def set_agent_mode(self, on: bool) -> None:
        """运行时切换 agent_mode（True=启用工具，False=纯对话）"""
        self._agent_mode = on

    async def _prepare(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        执行 Router 判定 + 工具循环，返回喂给「最终 LLM 调用」的 messages。

        若无需工具，直接返回原 messages 的浅拷贝（不污染调用方）。
        若需要工具，返回已含 assistant(tool_calls) + tool(result) 的完整 messages。
        """
        tools = self._registry.schemas() if self._agent_mode else []
        working = [dict(m) for m in messages]  # 浅拷贝，避免污染 Session 的 messages

        decision = await self._router.route(working, tools)
        iteration = 0
        while decision.action == Action.TOOL and iteration < MAX_TOOL_ITERATIONS:
            # 把 assistant 的 tool_calls 作为一条消息追加
            working.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["arguments"], ensure_ascii=False),
                        },
                    }
                    for tc in decision.tool_calls
                ],
            })
            # 逐个执行工具，把结果作为 tool role 消息追加
            for tc in decision.tool_calls:
                result = await self._registry.execute(tc["name"], tc["arguments"])
                working.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })
            # 再调 LLM（本轮不带 tools，让它基于工具结果总结）
            resp = await self._llm.chat(working)
            if resp.tool_calls:
                # 模型还想继续调工具 → 进入下一轮
                decision = Decision.from_llm_response(resp.tool_calls)
                iteration += 1
                continue
            return working

        # 纯对话（无工具）：返回原 messages 副本
        return working

    async def run(self, messages: List[Dict[str, Any]]) -> LLMResponse:
        """
        非流式运行：工具循环结束后调用 LLM，返回完整 LLMResponse
        （含 text / finish_reason / tool_calls）。
        """
        working = await self._prepare(messages)
        return await self._llm.chat(working)

    async def run_stream(self, messages: List[Dict[str, Any]]) -> AsyncGenerator[str, None]:
        """
        流式运行：先跑工具循环（非流式），再对流式最终回答逐 token 产出。
        这样既能用工具，又能保留打字机效果。
        """
        working = await self._prepare(messages)
        async for token in self._llm.chat_stream(working):
            yield token
