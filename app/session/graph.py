"""
LangGraph 决策图 —— 替代原有 Pipeline 的对话生成引擎（阶段2 核心改造）

设计要点：
  - 自定义 StateGraph 实现 ReAct 式「模型决策 → 工具执行 → 再决策」循环，
    取代基础设施层自研的 AgentRunner 工具循环（Router + 工具循环）。
  - 流式：call_model 节点用 model.astream 逐块累计后 yield 出完整 AIMessage，
    配合 graph.astream_events(version="v2") 捕获 on_chat_model_stream 事件，
    实现逐 token 推前端打字机效果（API 与 pipeline.generate_stream 同签名）。
    注意：call_model 必须「逐块 astream + 累计后 yield」，不能只 return/ainvoke，
    否则子层流式事件不会冒泡到 astream_events，前端收不到 token。
  - Tools：通过 ChatOpenAI.bind_tools 原生 function calling；工具失败由
    ToolRegistry / 原 Tool 降级，不向上抛异常。
  - agent_mode 关闭时退化为纯对话（不绑定工具，与改造前行为一致，可一键回退）。

依赖：langchain-openai（ChatOpenAI 指向 DeepSeek）+ langgraph（StateGraph）。
"""

import os
from typing import Annotated, Any, AsyncGenerator, List, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langchain_openai import ChatOpenAI
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from .context_builder import ContextBuilder

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
# 工具循环最大迭代次数（防模型反复调工具死循环，与 AgentRunner.MAX_TOOL_ITERATIONS 对齐）
MAX_TOOL_ITERATIONS = 10


class State(TypedDict):
    """LangGraph 状态：消息列表用 add_messages reducer 自动追加。"""

    messages: Annotated[list, add_messages]

class GraphAgent:
    """基于 LangGraph 的对话生成引擎，对外暴露 generate_stream 流式入口。"""

    def __init__(
        self,
        tools: list,
        context_builder: ContextBuilder,
        settings,                                  # ← 新增：由外部注入
        agent_mode: bool = True,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ):
        self._context_builder = context_builder
        self._agent_mode = agent_mode
        self._settings = settings                   # ← 现在 settings 是参数，合法了

        self._model = ChatOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
            temperature=temperature,
            max_tokens=max_tokens,
            streaming=True,
        )
        # 工具：仅 agent_mode 开启时绑定到模型
        self._tool_map = {t.name: t for t in tools}
        self._tools = tools
        self._model_with_tools = (
            self._model.bind_tools(tools) if tools else self._model
        )

        self._graph = self._build()

    # ───────────────────────────── 节点 ─────────────────────────────

    async def call_model(self, state: State):
        """
        LLM 决策节点：流式累计后 yield 一个完整 AIMessage。

        - agent_mode 开 → 用绑定了工具的模型（可能产出 tool_calls）
        - agent_mode 关 → 用裸模型（纯对话）
        """
        model = self._model_with_tools if self._agent_mode else self._model
        full = None
        async for chunk in model.astream(state["messages"]):
            full = chunk if full is None else full + chunk
        if full is None:
            return
        # 把累计出的 AIMessageChunk 转成标准 AIMessage 再写回 state，
        # 保证后续 should_continue / tool_node 拿到的是规整消息对象
        yield {
            "messages": [
                AIMessage(content=full.content or "", tool_calls=full.tool_calls or [])
            ]
        }

    async def tool_node(self, state: State):
        """工具执行节点：对上一条 assistant 消息里的每个 tool_call 执行并回 ToolMessage。"""
        last = state["messages"][-1]
        results = []
        for tc in getattr(last, "tool_calls", []) or []:
            name = tc["name"]
            args = tc["args"] or {}
            tool = self._tool_map.get(name)
            if tool is None:
                results.append(
                    ToolMessage(content=f"[工具 '{name}' 不存在或未注册]", tool_call_id=tc["id"])
                )
                continue
            try:
                res = await tool.ainvoke(args)
            except Exception as e:  # 工具执行失败不崩溃，错误串喂回 LLM
                res = f"[工具 '{name}' 执行失败：{e}]"
            results.append(ToolMessage(content=str(res), tool_call_id=tc["id"]))
        return {"messages": results}

    @staticmethod
    def _should_continue(state: State) -> str:
        """条件边：有 tool_calls 且未超迭代上限 → 走 tools，否则结束。"""
        last = state["messages"][-1]
        if not getattr(last, "tool_calls", None):
            return END
        n = sum(
            1
            for m in state["messages"]
            if isinstance(m, (AIMessage, AIMessageChunk)) and getattr(m, "tool_calls", None)
        )
        if n >= MAX_TOOL_ITERATIONS:
            return END
        return "tools"

    def _build(self):
        b = StateGraph(State)
        b.add_node("call_model", self.call_model)
        b.add_node("tools", self.tool_node)
        b.add_edge(START, "call_model")
        b.add_conditional_edges(
            "call_model", self._should_continue, {"tools": "tools", END: END}
        )
        b.add_edge("tools", "call_model")
        return b.compile()

    # ───────────────────────────── 运行时开关 ─────────────────────────────

    def set_agent_mode(self, on: bool) -> None:
        """运行时切换 agent_mode（True=工具可用，False=纯对话）。"""
        self._agent_mode = on

    # ───────────────────────────── 消息转换 ─────────────────────────────

    @staticmethod
    def _to_lc_messages(messages: List[dict]) -> List[Any]:
        """把 PromptBuilder 产出的 OpenAI dict 消息转成 LangChain 消息。"""
        out = []
        for m in messages:
            role = m.get("role")
            content = m.get("content") or ""
            if role == "system":
                out.append(SystemMessage(content=content))
            elif role == "user":
                out.append(HumanMessage(content=content))
            elif role == "assistant":
                out.append(AIMessage(content=content))
            elif role == "tool":
                out.append(
                    ToolMessage(content=content, tool_call_id=m.get("tool_call_id", ""))
                )
        return out

    async def _build_messages(
        self, user_text, history, user_id, include_vector, persona_override
    ):
        """复用 ContextBuilder（与 Pipeline 完全一致的拼装路径）。"""
        _, messages = await self._context_builder.build(
            user_text, history, user_id, include_vector, persona_override
        )
        return self._to_lc_messages(messages)

    # ───────────────────────────── 对外入口 ─────────────────────────────

    async def generate_stream(
        self, user_text, history, user_id, include_vector, persona_override
    ) -> AsyncGenerator[str, None]:
        """
        流式生成一轮回答（逐 token 产出，供 WebSocket 推前端打字机效果）。

        只把「最终回答」的内容块 yield 出去；工具调用阶段（tool_calls 块）
        content 为空，被过滤掉，不污染前端显示。
        """
        lc = await self._build_messages(
            user_text, history, user_id, include_vector, persona_override
        )
        async for ev in self._graph.astream_events({"messages": lc}, version="v2"):
            if ev.get("event") == "on_chat_model_stream":
                chunk = ev["data"]["chunk"]
                text = getattr(chunk, "content", None)
                if text:
                    yield text
