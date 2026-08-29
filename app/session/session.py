"""
对话会话 —— 流程编排中枢，不干具体活

技术栈说明：
  - 依赖注入：所有能力由外部注入，session 只拼装流程
  - 六层管道：config → persona → prompt → llm → validator → output
  - 适配器模式：任何一层都可替换（如 JSON→PostgreSQL、火山→Azure TTS）

职责边界（非常明确）：
  session 只管：编排顺序、异常处理、数据流转
  session 不管：prompt 拼装、输出校验、API 调用、文件读写

记忆三层编排（本文件是中枢）：
  - 热层（工作记忆）：self._memory（JsonChatMemory）→ 最近 20 轮原文，save() 同步落盘
  - 温层（滚动摘要）：self._reducer + self._archive → 淘汰即压缩，异步 fire-and-forget
  - 冷层（长期事实+向量）：self._extractor → 写 long_memory.json / memory_vectors
  读路径：chat() 开头经 PromptBuilder.build() 同步从三层拉取，编译进 system prompt。
  写路径：chat() 末尾 save() 后，用 asyncio.create_task 把温层/冷层压缩派发给后台协程。
"""

import asyncio
from typing import Optional, Dict, Any, List
from uuid import UUID

from domain.prompt.builder import PromptBuilder
from domain.prompt.validator import Validator
from domain.persona.entity import load_persona, PersonaConfig
from domain.types.message import (
    Message, UserMessage, AssistantMessage, SystemMessage,
    from_dict, messages_to_dicts,
)

from infrastructure.storage.json_repo import JsonChatMemory, MAX_ROUNDS
from infrastructure.memory.json_long_memory import JsonLongMemory
from infrastructure.memory.vector_repo import VectorMemory
from domain.memory.extractor import MemoryExtractor
from domain.memory.reducer import MemoryReducer
from infrastructure.memory.archive_memory import JsonArchiveMemory
from infrastructure.llm.client import LLMClient, LLMResponse
from infrastructure.tts.client import TTSClient

# Agent 能力（Step 5：决策层 + 工具系统）
from .context_builder import ContextBuilder
# 阶段2 核心改造：LangGraph 决策图引擎（替代 Pipeline / AgentRunner 的对话生成）
from .graph import GraphAgent
from infrastructure.tools.langchain_tools import build_graph_tools
from infrastructure.tools.registry import ToolRegistry
from infrastructure.tools.get_time import GetCurrentTimeTool
from infrastructure.tools.recall_memory import RecallMemoryTool


class ChatSession:
    """
    对话会话

    职责：
      1. 协调 PromptBuilder → LLMClient → Validator → TTSClient 的串行工作流
      2. 管理消息历史（增删改查、持久化）
      3. 管理角色设定（当前角色、角色历史列表）
      4. 管理 LLM API 配置（密钥、模型、端点）

    不做什么：
      - 不写文件（交给 JsonChatMemory）
      - 不调 API（交给 LLMClient / TTSClient）
      - 不拼 prompt（交给 PromptBuilder）
      - 不校验输出（交给 Validator）

    ── 生命周期 ──
      __init__ → _load() → chat_stream() 循环 → close()
    """

    def __init__(
        self,
        source: str = "web",
        memory: Optional[JsonChatMemory] = None,
        llm: Optional[LLMClient] = None,
        tts: Optional[TTSClient] = None,
        builder: Optional[PromptBuilder] = None,
        validator: Optional[Validator] = None,
        long_memory: Optional[JsonLongMemory] = None,
        vector_memory: Optional[VectorMemory] = None,
        model_gateway=None,
    ):
        """
        依赖注入：所有组件可替换

        不传参数时使用默认实现（JSON 存储 + DeepSeek + 火山 TTS），
        传参时可替换为 PostgreSQL / GPT-4 / Azure TTS。

        新增 long_memory：长期记忆注入点（JSON KV）。
        新增 vector_memory：语义检索注入点（PostgreSQL + pgvector + BGE-M3）。
        默认使用 JsonLongMemory 和 VectorMemory（pgvector 后端）。
        """
        self._source = source

        # 长期记忆（独立于对话历史）—— JSON KV
        self._long_memory = long_memory or JsonLongMemory(source)

        # 向量记忆（语义检索）—— PostgreSQL + pgvector + BGE-M3
        self._vector_memory = vector_memory or VectorMemory(
            model_gateway=model_gateway,
            owner_id=self._source,
        )

        # 双轨记忆 · 温层：归档存储（需在 builder 之前就绪）
        self._archive = JsonArchiveMemory(source)
        # 已折叠进摘要的 user/assistant 消息条数（用于"每个被淘汰片段只压缩一次"）
        self._archived_count = 0

        # 全部注入，session 只管编排
        self._memory = memory or JsonChatMemory(source)
        self._llm = llm or LLMClient()
        self._tts = tts or TTSClient()
        self._builder = builder or PromptBuilder(
            source,
            long_memory=self._long_memory,
            vector_memory=self._vector_memory,
            archive_memory=self._archive,
        )
        self._validator = validator or Validator()
        # 滚动摘要压缩器（依赖 _llm，须在 LLMClient 就绪之后创建）
        self._reducer = MemoryReducer(self._llm)
        self._extractor = MemoryExtractor(
            self._long_memory,
            self._llm,
            vector_memory=self._vector_memory,
        )

        # ── Agent 能力（Step 5：决策层 + 工具系统）──
        # agent_mode 默认开启；关闭时退化为纯 LLM 对话（行为与改造前一致）
        self._agent_mode = True
        # 工具注册表：注册两个零风险示范工具
        self._tool_registry = ToolRegistry()
        self._tool_registry.register(GetCurrentTimeTool())
        self._tool_registry.register(
            RecallMemoryTool(vector_memory=self._vector_memory, source=self._source)
        )
        # 联网检索能力（含 P0 清洗/溯源两道闸，详见 infrastructure/tools/web_search.py）
        from infrastructure.tools.web_search import WebSearchTool
        self._tool_registry.register(WebSearchTool())
        # 子组件：上下文构建
        self._context_builder = ContextBuilder(self._builder)

        # LangGraph 决策图（阶段2：替代旧 Pipeline/AgentRunner 的对话生成引擎）
        from infrastructure.config.settings import get_settings

        settings = get_settings()

        self._graph = GraphAgent(
            tools=build_graph_tools(self._tool_registry),
            context_builder=self._context_builder,
            settings=settings,
            model_gateway=model_gateway,
            agent_mode=self._agent_mode,
        )

        # 运行时状态
        self._custom_persona: Optional[str] = None
        self._history: List[Message] = []
        self._loaded = False

        # 会话内并发锁：同一用户开多个标签页同时调 chat() 时，
        # 串行化对 _history 的读写，避免对话记录错乱
        self._lock = asyncio.Lock()

        # 加载
        self._load()

    # ════════════════════════════════════════════════════════
    # 内部：初始化
    # ════════════════════════════════════════════════════════

    def _get_effective_persona(self) -> str:
        """
        获取当前生效的角色文本

        优先级：
          1. 运行时 set_persona() 设定的（_custom_persona）
          2. 持久化文件中的（persona_web.txt）
          3. persona.yaml 默认配置编译出的文本
        """
        if self._custom_persona is not None:
            return self._custom_persona

        # 从文件读取持久化角色
        file_persona = self._memory.load_persona("")
        if file_persona:
            return file_persona

        # 回退到 persona.yaml 默认
        config: PersonaConfig = load_persona()
        return config.to_prompt_text()

    def _load(self) -> None:
        """
        启动加载

        流程：
          1. 获取当前角色
          2. 从 JsonChatMemory 加载历史（自动替换 system 消息）
          3. 保存到 self._history
        """
        if self._loaded:
            return

        persona = self._get_effective_persona()
        self._history = self._memory.load(persona, self._source)
        self._loaded = True

    def _refresh_persona(self) -> None:
        """
        刷新 system 消息

        每次请求前调用，确保 history[0] 的 system 消息
        与当前角色一致（角色可能在两次 chat 之间被改过）。
        """
        persona = self._get_effective_persona()
        if self._history and isinstance(self._history[0], SystemMessage):
            if self._history[0].content != persona:
                self._history[0].content = persona

    # ════════════════════════════════════════════════════════
    # 核心：对话
    # ════════════════════════════════════════════════════════

    async def chat_stream(
        self,
        user_text: str,
        user_id: str = "default",
        conversation_id: UUID | None = None,
        include_vector: bool = False,
    ):
        """
        流式对话轮次 —— Web 端实时打字机效果的核心入口。

        流程：
          1. 刷新 persona + 流式生成
          2. 流式生成 → 逐 token yield {"type":"token","text":"..."}
          3. 写入历史 + Validator 校验 + 存盘 + 双轨压缩
          最后 yield {"type":"done","text":final_reply, "error":""}

        返回值是异步生成器，不是 Dict：
            async for event in session.chat_stream(user_text):
                if event["type"] == "token":
                    ... 推给前端实时显示 ...
                elif event["type"] == "done":
                    ... event["text"] 是 Validator 校验后的最终文字 ...

        与 chat() 的关键差异：
          - 锁内执行全程（含流式 yield），串行化保证历史一致性。
          - 暂不返回 token 计数（流式模式下需额外抓最后一块的 usage）。
          - TTS 不在此方法内合成，交给 WebSocket 处理器在 "done" 后调 synthesize_tts。
        """
        async with self._lock:
            # 1-3. 刷新 persona + 流式生成
            self._refresh_persona()
            include_vector = self._vector_memory is not None

            # 4'. 流式生成（含工具循环：工具阶段非流式、最终回答流式打字机）
            #       generate_stream 内部先读 history 拼装、再于末尾追加 user_input，
            #       所以用户/助手消息在生成结束后再写入 history（避免重复）
            full_text = ""
            try:
                async for token in self._graph.generate_stream(
                    user_text=user_text,
                    history=self._history,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    include_vector=include_vector,
                    persona_override=self._get_effective_persona(),
                ):
                    full_text += token
                    yield {"type": "token", "text": token}
            except Exception as e:
                error_msg = f"对话生成失败：{e}"
                self._memory.add_message(self._history, AssistantMessage(content=error_msg))
                self._memory.save(self._history, self._source)
                yield {"type": "done", "text": error_msg, "error": str(e)}
                return

            # 5-7. 用户消息 + 助手回复写入历史 + 存盘 + 双轨（同 chat）
            self._memory.add_message(self._history, UserMessage(content=user_text))
            final_reply = self._validator.validate_with_fallback(full_text)
            self._memory.add_message(self._history, AssistantMessage(content=final_reply))
            self._memory.save(self._history, self._source)
            evicted_turns = self._compute_evicted_turns()

        # 锁外 fire-and-forget（同 chat，不阻塞）
        if evicted_turns:
            asyncio.create_task(self._reduce_and_archive(evicted_turns))
        # 每轮成功聊天都提炼长期记忆。
        # 不能把它放在 if evicted_turns 里面，
        # 否则短对话永远不会写入长期记忆。
        asyncio.create_task(
            self._extractor.extract(
                user_text,
                final_reply,
                source=self._source,
                memory_scope="user",
                source_type="normal_chat",
            )
        )
        yield {"type": "done", "text": final_reply, "error": ""}

    # ════════════════════════════════════════════════════════
    # Agent 能力开关
    # ════════════════════════════════════════════════════════

    def set_agent_mode(self, on: bool) -> None:
        """
        运行时切换 Agent 能力开关

        参数：
          on  True=启用工具（Router + Tool 循环），False=纯 LLM 对话
        说明：
          关闭后 Router 收不到 tools 列表，直接判 CHAT，行为与改造前一致，
          相当于一键回退到「无 Agent」状态，便于排查问题。
        """
        self._agent_mode = on
        self._graph.set_agent_mode(on)

    # ════════════════════════════════════════════════════════
    # 双轨记忆 · 温层（滚动摘要）
    # ════════════════════════════════════════════════════════

    def _compute_evicted_turns(self) -> list[str]:
        """算出"本轮将被 trim 淘汰、且尚未压缩过的"对话片段。

        核心思路（与 JsonChatMemory.trim 对齐，keep = MAX_ROUNDS * 2 条消息）：
          - self._history 在内存中是完整历史（save 只写文件、不改内存列表），
            所以这里能看到所有轮次。
          - 文件只保留最近 MAX_ROUNDS 轮 = keep 条 user/assistant 消息。
          - 超出 keep 的头部消息会被 trim 丢弃；这些就是"被淘汰的轮次"。
          - self._archived_count 记录"已经折叠进摘要的头部消息条数"，
            保证每个被淘汰片段只压缩一次（不会重复、也不会漏）。
          - 重启后文件仅 20 轮、_archived_count 归零：第一轮淘汰只会压到
            文件里最旧的轮次（它们本来就不在摘要里），不会重复已归档内容。

        返回：["用户：...", "星程：...", ...] 纯文本行；无可压缩时返回空列表。
        """
        keep = MAX_ROUNDS * 2  # 与 trim() 保留的条数一致
        ua = [m for m in self._history if not isinstance(m, SystemMessage)]
        total = len(ua)

        # 头部尚未归档的消息数 减去 要保留的条数 = 本次真正新淘汰的条数
        if total - self._archived_count <= keep:
            return []

        # 仅取"本次新被淘汰"的那一段（archived_count 之前已是旧摘要内容）
        evicted_msgs = ua[self._archived_count: total - keep]
        # 更新边界：以后这段也算"已归档"
        self._archived_count = total - keep

        lines = []
        for m in evicted_msgs:
            role = "用户" if isinstance(m, UserMessage) else "星程"
            lines.append(f"{role}：{m.content}")
        return lines

    async def _reduce_and_archive(self, evicted_turns: list[str]) -> None:
        """把被淘汰片段折叠进滚动摘要（fire-and-forget 异步任务）。

        流程：取旧摘要 → reducer 融合 → 写回归档。
        任何失败都"保留旧摘要"，绝不丢记忆。
        """
        try:
            existing = self._archive.get_digest()
            summary = await self._reducer.reduce(
                existing, "\n".join(evicted_turns)
            )
            if summary:
                self._archive.set_digest(summary)
                print(
                    f"[Archive] folded {len(evicted_turns)} evicted turns "
                    f"into rolling digest (len={len(summary)})"
                )
        except Exception as e:
            print(f"[Archive] reduce_and_archive failed: {e}")

    # ════════════════════════════════════════════════════════
    # 角色管理
    # ════════════════════════════════════════════════════════

    def get_persona(self) -> Dict[str, Any]:
        """返回当前角色信息（前端展示用）。

        返回 {"persona": 生效角色文本, "custom": 是否为运行时自定义角色}。
        """
        return {
            "persona": self._get_effective_persona(),
            "custom": self._custom_persona is not None,
        }

    def set_persona(self, text: str) -> None:
        """
        设置当前角色

        流程：
          1. 存入 _custom_persona（运行时）
          2. 持久化到文件（重启丢失保护）
          3. 加入历史列表（前端下拉框）
          4. 刷新 system 消息（立即生效）
        """
        text = text.strip()
        self._custom_persona = text if text else None

        if text:
            self._memory.save_persona(text)
            self._memory.add_persona_history(text)
            self._refresh_persona()

    # ════════════════════════════════════════════════════════
    # 角色历史
    # ════════════════════════════════════════════════════════

    def get_persona_history(self) -> List[str]:
        """返回角色历史列表（前端下拉框展示所有设置过的角色）。"""
        return self._memory.load_persona_history()

    def remove_persona_history(self, index: int) -> None:
        """按索引删除一条角色历史（仅删历史列表，不影响当前对话）。"""
        self._memory.delete_persona_history(index)

    # ════════════════════════════════════════════════════════
    # LLM API 配置
    # ════════════════════════════════════════════════════════

    def get_api_config(self) -> Dict[str, Any]:
        """返回当前 LLM API 配置（含 api_key 掩码、base_url、model 及是否自定义）。"""
        config = self._llm.get_config()
        config["custom"] = bool(self._llm._custom_config)
        return config

    def set_api_config(
        self, api_key: str = "", base_url: str = "", model: str = ""
    ) -> None:
        """更新 LLM API 配置；空字符串参数视为不修改该项（传 None）。"""
        self._llm.set_config(
            api_key=api_key or None,
            base_url=base_url or None,
            model=model or None,
        )

    def reset_api_config(self) -> None:
        """重置 LLM API 配置为默认值（清空自定义项）。"""
        self._llm.reset_config()

    # ════════════════════════════════════════════════════════
    # 长期记忆管理
    # ════════════════════════════════════════════════════════

    def get_long_memory(self) -> dict:
        """返回当前用户全部长期记忆（前端展示用）"""
        return self._long_memory.load()

    def set_long_memory(self, key_name: str, value: Any) -> None:
        """写入一条长期记忆"""
        self._long_memory.set(key_name, value)

    def delete_long_memory(self, key_name: str) -> None:
        """删除一条长期记忆"""
        self._long_memory.delete(key_name)

    # ════════════════════════════════════════════════════════
    # 历史管理（后端 API 接口）
    # ════════════════════════════════════════════════════════

    def get_history(self) -> List[Dict]:
        """
        返回用户可见的历史（不含 system 消息）

        序列化为 dict 列表，兼容前端现有格式：
        [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
        """
        visible = [m for m in self._history if not isinstance(m, SystemMessage)]
        return messages_to_dicts(visible)

    def clear_history(self) -> None:
        """清空历史（保留 system 消息）"""
        system = None
        if self._history and isinstance(self._history[0], SystemMessage):
            system = self._history[0]
        self._history = [system] if system else []
        self._memory.save(self._history, self._source)

    async def synthesize_tts(self, text: str) -> str:
        """独立的 TTS 合成，用于文字先返回后异步补语音"""
        return await self._tts.synthesize(text)

    # ════════════════════════════════════════════════════════
    # 生命周期
    # ════════════════════════════════════════════════════════

    def close(self) -> None:
        """释放资源"""
        self._memory.save(self._history, self._source)
