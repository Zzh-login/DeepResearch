"""
双轨记忆 · 温层压缩器（第二轨）

架构定位：
  - 这是"双轨记忆"里【温层】的压缩执行单元，对应工业记忆分层里的
    "情景短期记忆（episodic short-term）"。
  - 唯一职责：把"被 trim 淘汰掉的旧对话片段"用 LLM 压缩，
    融合进一份不断演进的"滚动摘要"。

与其他模块的关系（调用链）：
  - 由 app/session/session.py 的 ChatSession._reduce_and_archive()
    通过 asyncio.create_task(...) 以【异步 fire-and-forget】方式触发，
    不会阻塞用户回复。
  - 压缩结果交给 infrastructure/memory/archive_memory.py 的
    JsonArchiveMemory 落盘（本模块只管"怎么压"，不管"怎么存"）。

设计原则（与 MemoryExtractor 同构）：
  - 独立的 LLM 调用 + 独立提示词（REDUCE_PROMPT），
    与用户对话的 system prompt 完全解耦，不动 prompt 工程。
  - 失败/超时返回 None，由调用方保留旧摘要，绝不丢记忆。
"""

import asyncio
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from infrastructure.llm.client import LLMClient


REDUCE_PROMPT = """你是一个工业级对话记忆压缩器，负责维护一份"滚动摘要"。

任务：给定"已有滚动摘要"和"本轮被淘汰的对话片段"，产出一份**新的、连贯的中文滚动摘要**，把两者要点融合成一段。

融合规则：
- 保留：用户身份 / 画像、长期意图与目标、关键决策与约定、未完成的任务、反复出现的主题、对后续对话有价值的上下文。
- 舍弃：寒暄客套、重复内容、一次性调试过程、临时报错、与长期无关的技术细节、可从上轮直接推断的琐碎信息。
- 严格基于原文：不得新增任何原文没有的信息，不得编造、不得夹带个人判断。

格式要求：
- 纯中文文本，不要 markdown、不要标题、不要序号、不要解释、不要任何前缀。
- 控制在 600 字以内，信息密度优先；若已有摘要已很完整，可在其基础上做小幅增补而非重写。

已有滚动摘要：
{existing_digest}

本轮被淘汰的对话片段：
{new_turns}
"""


class MemoryReducer:
    """将淘汰的旧对话轮次压缩成滚动摘要。

    与 MemoryExtractor 同模式：
      - 独立的异步 LLM 调用（不在用户生成路径上同步执行）
      - 独立的提示词（REDUCE_PROMPT，与 EXTRACT_PROMPT 平级）
      - 由 ChatSession 以 fire-and-forget 任务触发（asyncio.create_task）

    职责单一：输入"已有摘要 + 本轮被淘汰片段"，输出"融合后的新摘要"。
    它只负责"压缩"，不负责"存储"——存储交给 JsonArchiveMemory。
    """

    def __init__(self, llm: "LLMClient"):
        """初始化温层压缩器，注入用于压缩的独立 LLM 客户端。

        Args:
            llm: LLM 客户端；压缩走独立的 REDUCE_PROMPT 调用，
                 与用户对话的 system prompt 完全解耦。
        """
        self._llm = llm

    async def reduce(
        self, existing_digest: str, new_turns: str
    ) -> Optional[str]:
        """压缩并融合一轮被淘汰的对话。

        参数：
          existing_digest：归档里已有的滚动摘要（可能为空）
          new_turns：本轮被 trim 淘汰的对话片段（纯文本，已格式化为"角色：内容"）

        返回：
          融合后的新滚动摘要字符串；失败 / 超时时返回 None，
          由调用方决定是否保留旧摘要（保留即可，绝不丢记忆）。
        """
        existing = existing_digest.strip() or "（暂无已有摘要）"
        prompt = REDUCE_PROMPT.format(
            existing_digest=existing,
            new_turns=new_turns,
        )
        messages = [{"role": "user", "content": prompt}]

        # 独立的 LLM 调用：使用 REDUCE_PROMPT（与用户对话提示词解耦），
        # 并用 wait_for 限定 15s 超时——压缩失败也只是"暂时没新摘要"，绝不能拖慢主回复。
        try:
            resp = await asyncio.wait_for(
                self._llm.chat(messages, temperature=0.2, max_tokens=800),
                timeout=15.0,
            )
            text = resp.text.strip()
            return text or None
        except asyncio.TimeoutError:
            print("[Archive] reduce timed out; kept previous digest")
            return None
        except Exception as e:
            print(f"[Archive] reduce failed: {e}")
            return None
