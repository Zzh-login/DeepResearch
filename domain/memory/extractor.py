"""
双轨记忆 · 冷层提炼器（长期记忆写入端）

架构定位：
  - 这是"双轨记忆"里【冷层】的写入执行单元，对应工业记忆分层里的
    "语义长期记忆（semantic long-term）"。
  - 职责：每轮对话后，用 LLM 从对话中抽取"长期有用"的事实
    （用户偏好 / 画像 / 项目 / 决策 / 目标 / 约束），写入长期记忆。

与其他模块的关系（调用链）：
  - 由 app/session/session.py 的 ChatSession.chat() 在回复返回后，
    通过 asyncio.create_task(self._extractor.extract(...)) 以
    【异步 fire-and-forget】方式触发，不阻塞用户回复。
  - 抽取结果同时写两条存储：
      1) infrastructure/memory/json_long_memory.py → long_memory.json（结构化事实）
      2) infrastructure/memory/vector_repo.py       → memory_vectors 表（语义向量，供检索）
  - 重要性 < 0.3 的事实不会写入向量库（纯噪声不入库）。

设计原则（与 MemoryReducer 同构）：
  - 独立的 LLM 调用 + 独立提示词（EXTRACT_PROMPT）。
  - 自带"空闲跳过"机制：连续 N 轮无有用记忆则跳过后续几轮，省 token。
"""

import asyncio
import json
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from infrastructure.llm.client import LLMClient
    from infrastructure.memory.json_long_memory import JsonLongMemory
    from infrastructure.memory.vector_repo import VectorMemory


EXTRACT_PROMPT = """你是一个工业级长期记忆提炼助手。你的任务是从本轮对话中提取“未来长期有用、可治理、可解释”的用户记忆。

对话：
用户：{user_input}
星程：{assistant_reply}

只输出一行合法 JSON，不要输出 markdown、解释、注释或多余文字。

硬性约束：
- 只提取用户明确表达或强烈暗示的信息。
- 不要把星程的回复、建议、推测当成用户事实。
- 不要记录一次性的技术问题、临时调试过程、闲聊寒暄。
- 技术内容本身不记；但用户的长期项目、技术栈、工具偏好、架构决策、长期目标可以记。
- 不要记录密码、API Key、Token、验证码、身份证号、银行卡、手机号、邮箱、精确住址等敏感信息。
- 不要记录对星程/机器人的评价，除非用户把它明确设为长期交互偏好。
- 每条 fact 必须使用第三人称，一句话，自包含，不依赖上下文。
- 只输出新增或可更新的长期事实；不要输出重复、模糊、低价值内容。

值得记的类型：
- preference：用户偏好，例如回答风格、语言、工具偏好
- profile：用户身份、角色、工作内容
- project：用户正在长期进行的项目、项目路径、项目技术栈
- decision：用户做出的长期决策或约定
- goal：用户的长期目标
- constraint：用户长期限制或环境约束

不值得记：
- 纯技术问答，例如“这段代码怎么调试”
- 单次 bug、一次性报错、临时命令请求
- 寒暄、闲聊、短期情绪
- 星程给出的建议，除非用户明确采纳为长期约定

如果没有值得记的信息，输出：
{{"should_remember": false}}

如果有，输出：
{{"should_remember": true,"memories":[{{"type":"preference","fact":"用户偏好中文、直接且讲清楚的回答。","confidence":0.9,"importance":0.7}}]}}

字段要求：
- type 必须是 preference/profile/project/decision/goal/constraint 之一。
- fact 必须是中文第三人称事实句，优先以“用户...”开头。
- confidence 表示事实确定性，范围 0 到 1。
- importance 表示长期有用程度，范围 0 到 1。
- 最多输出 5 条 memory。
- JSON 必须合法，必须是一行。"""

SKIP_AFTER_IDLE = 3
SKIP_DURATION = 5
MEMORY_TYPES = {
    "preference",
    "profile",
    "project",
    "decision",
    "goal",
    "constraint",
}


class MemoryExtractor:
    """Extracts structured long-term memories after a conversation turn.

    新增 vector_memory：提炼出的 memory 同步写入向量存储，
    供后续对话语义检索用。只写 importance >= 0.3 的条目。
    """

    def __init__(
        self,
        long_memory: "JsonLongMemory",
        llm: "LLMClient",
        vector_memory: "Optional[VectorMemory]" = None,
    ):
        """初始化提炼器，注入长期记忆存储与 LLM 客户端。

        Args:
            long_memory: 结构化长期记忆写入端（JsonLongMemory）。
            llm: LLM 客户端，用于抽取事实的独立调用。
            vector_memory: 可选语义向量存储（VectorMemory）；
                          为 None 时只写结构化事实、不写向量库。
        内部维护空闲跳过计数器（_idle_count / _skip_until），
        连续 N 轮无有用记忆时跳过后续轮次以省 token。
        """
        self._lm = long_memory
        self._llm = llm
        self._vm = vector_memory
        self._idle_count = 0
        self._skip_until = 0

    async def extract(
        self,
        user_input: str,
        assistant_reply: str,
        source: str = "default",
    ) -> None:
        """从一轮对话中异步抽取长期记忆并写入冷层（fire-and-forget 调用）。

        流程：调用 LLM（EXTRACT_PROMPT，10s 超时）→ 解析 JSON →
        归一化 → 写入 JsonLongMemory；若配有 vector_memory，则把
        importance>=0.3 的事实同步写入向量库供语义检索。
        自带"空闲跳过"：连续无记忆达阈值后跳过后续若干轮。
        任何异常（超时 / JSON 解析 / 其他）均被吞掉并打日志，不影响主回复。
        """
        if self._skip_until > 0:
            self._skip_until -= 1
            return

        text = ""
        try:
            prompt = EXTRACT_PROMPT.format(
                user_input=user_input,
                assistant_reply=assistant_reply,
            )
            messages = [{"role": "user", "content": prompt}]

            resp = await asyncio.wait_for(
                self._llm.chat(messages, temperature=0.1, max_tokens=500),
                timeout=10.0,
            )

            text = self._strip_code_fence(resp.text.strip())
            result = json.loads(text)
            memories = self._parse_memories(result)

            if memories:
                self._lm.add_memories(memories)
                print(f"[Memory] extracted {len(memories)} structured memories")
                self._idle_count = 0

                # 写入向量存储：只写重要性 >= 0.3 的条目
                if self._vm is not None:
                    for mem in memories:
                        importance = mem.get("importance", 0)
                        if importance >= 0.3:
                            try:
                                await self._vm.add(
                                    source=source,
                                    summary=mem["fact"],
                                    metadata={
                                        "type": mem.get("type", "profile"),
                                        "confidence": mem.get("confidence", 0),
                                        "importance": importance,
                                    },
                                )
                            except Exception as e:
                                print(f"[Vector] write failed for '{mem['fact'][:40]}': {e}")
                    print(f"[Vector] wrote {sum(1 for m in memories if m.get('importance', 0) >= 0.3)} items")
            else:
                self._idle_count += 1

            if self._idle_count >= SKIP_AFTER_IDLE:
                self._skip_until = SKIP_DURATION
                self._idle_count = 0
                print(
                    f"[Memory] no useful memory for {SKIP_AFTER_IDLE} turns; "
                    f"skip next {SKIP_DURATION} turns"
                )

        except asyncio.TimeoutError:
            print("[Memory] extraction timed out; skipped this turn")
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            print(f"[Memory] JSON parse failed: {e} | raw: {text[:100]}")
        except Exception as e:
            print(f"[Memory] extraction failed: {e}")

    def _parse_memories(self, result: dict[str, Any]) -> list[dict]:
        """把 LLM 返回的 JSON 解析为结构化记忆列表（最多 5 条）。

        兼容两种返回：{"should_remember":true,"memories":[...]} 或
        旧式 {"should_remember":true,"facts":[...]}（facts 退化为 profile）。
        should_remember 为 false 或非 dict 时返回空列表；每条经
        _normalize_memory 清洗后才纳入。
        """
        if not isinstance(result, dict) or not result.get("should_remember"):
            return []

        raw_memories = result.get("memories")
        if raw_memories is None and isinstance(result.get("facts"), list):
            raw_memories = [
                {
                    "type": "profile",
                    "fact": fact,
                    "confidence": 0.75,
                    "importance": 0.5,
                }
                for fact in result["facts"]
            ]

        if not isinstance(raw_memories, list):
            return []

        memories = []
        for raw in raw_memories[:5]:
            memory = self._normalize_memory(raw)
            if memory is not None:
                memories.append(memory)
        return memories

    def _normalize_memory(self, raw: Any) -> dict | None:
        """把单条原始记忆规整为内部标准结构。

        事实为空或 raw 非 dict → 丢弃（返回 None）；
        type 不在白名单 → 回退 "profile"；
        confidence/importance 经 _clamp_float 归一化到 [0,1]。
        """
        if not isinstance(raw, dict):
            return None

        fact = str(raw.get("fact", "")).strip()
        if not fact:
            return None

        memory_type = str(raw.get("type", "profile")).strip()
        if memory_type not in MEMORY_TYPES:
            memory_type = "profile"

        return {
            "type": memory_type,
            "fact": fact,
            "confidence": self._clamp_float(raw.get("confidence", 0.75)),
            "importance": self._clamp_float(raw.get("importance", 0.5)),
            "source": "conversation",
        }

    def _clamp_float(self, value: Any) -> float:
        """把任意值安全转成 [0.0, 1.0] 区间浮点；非法输入回退 0.0。"""
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = 0.0
        return max(0.0, min(1.0, number))

    def _strip_code_fence(self, text: str) -> str:
        """去掉 LLM 输出外层的 markdown 代码围栏（```json ... ```）。

        非围栏格式或行数不足时原样返回；否则取首尾围栏之间的内容。
        """
        if not text.startswith("```"):
            return text
        lines = text.splitlines()
        if len(lines) <= 2:
            return text
        return "\n".join(lines[1:-1]).strip()
