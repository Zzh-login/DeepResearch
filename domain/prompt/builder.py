
"""
Prompt 构建器 —— 将配置 + 记忆 + 历史编译为 LLM 可用的 messages

架构定位：记忆【读路径】的汇合点（编译成 system prompt 前的最后一站）。
  在 chat() 调用 LLM 之前，build() 同步从三层记忆拉取上下文：
    - 冷层 KV  ：_get_kv_context()      → long_memory.json
    - 冷层向量  ：_get_vector_context()  → memory_vectors（pgvector 语义检索）
    - 温层摘要  ：archive_memory.render_context() → archive_memory.json
  三层结果拼进 system_parts，与角色/规则/格式一起组成完整的 system prompt。

技术栈说明：
  - 配置驱动：读取 persona.yaml / rules.yaml / format.yaml
  - 记忆注入：预留 KV Memory / Vector Memory 插槽
  - 历史裁剪：保留最近 N 轮对话，防止 token 溢出
  - 依赖倒置：不直接 import LLM / TTS / Storage，
    只依赖 domain/persona/entity.py 和 domain/types/message.py

设计原则：
  Prompt Builder 是"编译器"——把分散的数据源编译成一条完整的 system prompt
  和 messages 列表。它不负责存储、不负责调用 LLM、不负责验证输出，
  只做一件事：拼装。
"""

from typing import Optional, TYPE_CHECKING
from ..persona.entity import load_persona, load_rules, load_format, PersonaConfig, Rule, FormatTemplate
from ..types.message import Message, SystemMessage, UserMessage, AssistantMessage, messages_to_dicts

if TYPE_CHECKING:
    from infrastructure.memory.json_long_memory import JsonLongMemory
    from infrastructure.memory.vector_repo import VectorMemory
    from infrastructure.memory.archive_memory import JsonArchiveMemory
    from ..types.context import AgentContext


class PromptBuilder:
    """
    Prompt 构建器

    职责：
      1. 加载角色配置（persona.yaml）
      2. 加载行为规则（rules.yaml）
      3. 加载输出格式（format.yaml）
      4. KV 记忆注入（用户偏好、档案）
      5. 向量记忆注入（语义检索相关历史）
      6. 裁剪历史对话（保留最近 N 轮）
      7. 拼装成 OpenAI API 的 messages 格式

    为什么独立成类而不是 session.py 里的一个函数：
      - 可测试：单独测 prompt 拼装逻辑，不依赖 session
      - 可复用：未来多 Agent 协作时，每个 Agent 用自己的 PromptBuilder
      - 可扩展：加新数据源（如用户画像、外部知识库）只需改这个类
    """

    def __init__(
        self,
        source: str = "web",
        long_memory: "Optional[JsonLongMemory]" = None,
        vector_memory: "Optional[VectorMemory]" = None,
        archive_memory: "Optional[JsonArchiveMemory]" = None,
    ):
        """初始化 Prompt 构建器，注入记忆数据源（均为可选，默认 None）。

        Args:
            source: 记忆归属来源标识。
            long_memory: 冷层 KV 结构化事实（JsonLongMemory）。
            vector_memory: 冷层语义向量检索（VectorMemory）。
            archive_memory: 温层滚动摘要（JsonArchiveMemory）。
        构造时不读配置文件，延迟到首次 build() 时通过 _load_configs() 加载
        persona/rules/format 配置，避免每次构建都读磁盘。
        """
        self._source = source
        self._long_memory = long_memory
        self._vector_memory = vector_memory
        self._archive_memory = archive_memory
        self._persona: Optional[PersonaConfig] = None
        self._rules: list[Rule] = []
        self._format: Optional[FormatTemplate] = None

    def _load_configs(self):
        """惰性加载配置（首次调用时加载，避免每次 build 都读文件）"""
        if self._persona is None:
            self._persona = load_persona()
        if not self._rules:
            self._rules = load_rules()
        if self._format is None:
            self._format = load_format()

    def _get_kv_context(self, user_id: str = "default") -> str:
        """
        获取 KV 记忆上下文

        从 JsonLongMemory 读取当前用户的所有长期记忆，
        渲染为可注入 prompt 的文本。

        无记忆时返回空字符串，不污染 system prompt。
        """
        if self._long_memory is None:
            return ""
        return self._long_memory.render_context()

    async def _get_vector_context(self, query: str, top_k: int = 3) -> list[str]:
        """
        语义检索：从向量记忆库中找出与当前输入语义最相似的记忆片段

        只用 summary（摘要）注入 prompt，不含 score/metadata。
        检索不到结果返回空列表，不污染 system prompt。

        优雅降级：
          - 未注入 vector_memory → 返回 []
          - 检索异常（连不上 DB / embedding 失败）→ 返回 [] + 日志打印
        """
        if self._vector_memory is None:
            return []

        try:
            results = await self._vector_memory.search(
                query=query,
                source=self._source,
                top_k=top_k,
                min_score=0.2,  # BGE-M3 语义质量高，阈值可保持偏低
            )
        except Exception as e:
            print(f"[Vector] search failed: {e}")
            return []

        return [r["summary"] for r in results]

    def _trim_history(
        self, history: list[Message], max_rounds: int = 5
    ) -> list[Message]:
        """
        裁剪历史对话，保留最近 max_rounds 轮

        策略：
          - system 消息（如果有）始终保留在 [0]
          - 只裁剪 user/assistant 对
          - 一轮 = user + assistant 两条消息

        为什么默认 5 轮而不是 20 轮（存储层的 MAX_ROUNDS）：
          存储层存 20 轮是为了历史回顾（用户翻看聊天记录），
          Prompt 里只放 5 轮是为了 token 经济性——5 轮约 1000 token，
          加上 system prompt 约 200 token，总共 1200 token，
          在 DeepSeek 128K 窗口内且响应速度快。
        """
        if not history:
            return []

        # 分离 system 消息和对话消息
        system = None
        dialogue = []
        for msg in history:
            if isinstance(msg, SystemMessage):
                system = msg
            else:
                dialogue.append(msg)

        # 保留最近 max_rounds 轮（每轮 2 条消息）
        keep = dialogue[-(max_rounds * 2):]

        # 重新组装
        result = []
        if system:
            result.append(system)
        result.extend(keep)
        return result

    async def build(
        self,
        ctx: "AgentContext",
    ) -> tuple[str, list[dict]]:
        """
        构建完整 prompt（异步：向量检索需要 await）

        参数：
          ctx: AgentContext —— 收敛了 user_input / history / user_id /
              include_vector / persona_override 等全部输入，避免参数膨胀。

        返回：
          system_prompt: 完整的 system 提示词（含角色 + 规则 + 格式 + 记忆）
          messages: OpenAI API 格式的 messages 列表（含历史对话 + 当前输入）

        调用时机：session.chat() 在调用 LLM 前调用此方法
        """
        self._load_configs()

        user_input = ctx.user_input
        history = ctx.history
        user_id = ctx.user_id
        include_vector = ctx.include_vector
        persona_override = ctx.persona_override

        # 1. 基础配置 → 文本
        persona_text = persona_override if persona_override else self._persona.to_prompt_text()
        rules_text = "\n".join(r.text for r in self._rules)
        format_text = self._format.template if self._format else ""

        # 2. 记忆上下文（当前阶段为空）
        kv_context = self._get_kv_context(user_id)
        vector_context = []
        if include_vector:
            vector_context = await self._get_vector_context(user_input)
        # 双轨记忆 · 温层：滚动摘要（被裁旧对话的压缩要义）
        archive_context = (
            self._archive_memory.render_context() if self._archive_memory else ""
        )

        # 3. 拼装 system prompt
        system_parts = [persona_text]
        if rules_text:
            system_parts.append(rules_text)
        if format_text:
            system_parts.append(f"输出格式：\n{format_text}")
        if kv_context:
            system_parts.append(f"长期记忆：\n{kv_context}")
        if archive_context:
            system_parts.append(f"早期对话摘要：\n{archive_context}")
        if vector_context:
            system_parts.append("相关历史：")
            system_parts.extend(f"- {ctx_item}" for ctx_item in vector_context)

        system_prompt = "\n".join(system_parts)

        # 4. 裁剪历史
        trimmed_history = self._trim_history(history)

        # 5. 构建 messages（OpenAI 格式）
        messages = []
        # system 消息（直接以System消息开头）
        messages.append(SystemMessage(content=system_prompt))

        # 历史对话（user/assistant）
        for msg in trimmed_history:
            if not isinstance(msg, SystemMessage):
                messages.append(msg)

        # 当前用户输入
        messages.append(UserMessage(content=user_input))

        # 6. 序列化为 dict 列表（OpenAI API 要求）
        return system_prompt, messages_to_dicts(messages)