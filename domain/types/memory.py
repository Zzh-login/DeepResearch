"""
记忆类型定义

技术栈说明：
  - Python dataclasses（标准库）：结构化数据容器

  - 设计对应 pgvector 架构文档中的 Memory System：
    · MemoryItem = KV Memory（memory_kv 表的 ORM 实体）
    · ChatTurn  = 对话轮次的结构化表示

  - 当引入 pgvector 后，MemoryItem 将扩展为：
    · KV 存储（PostgreSQL memory_kv 表）：key → value
    · Vector 存储（memory_vector 表）：content → embedding → 相似度检索
    · 检索时，检索出最相似的 K 个，并拼接成 Prompt
    · 检索结果拼接成 Prompt

  - 当前阶段（JSON 文件存储）：ChatTurn 为过渡类型，
    实际消息存储仍用 message.py 的 Message 体系，
    ChatTurn 仅在需要"成对管理一轮对话"时使用
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class MemoryItem:
    """
    KV 记忆条目（对应 PostgreSQL memory_kv 表）

    用途：存储用户偏好 / 长期记忆 / 配置片段
    技术路线：
      当前 → JSON 文件（infrastructure/storage/json_repo.py）
      未来 → PostgreSQL（SELECT key, value FROM memory_kv WHERE key = ?）

    示例数据：
      MemoryItem(
        key="user_pref_language",
        value="python",
        updated_at=datetime(2026, 7, 3, 12, 0)
      )
    """
    key: str
    value: str
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class ChatTurn:
    """
    一轮对话（用户问 + AI 答）

    用途：
      - 对话摘要（RAG 检索时可作为上下文注入）
      - 历史回顾（非实时对话，而是"往期对话轮次"的快照）

    与 Message 的区别：
      Message  = 单条消息（user / assistant / system），用于实时 LLM 调用
      ChatTurn = 成对消息（一问一答），用于存档 / 检索 / 摘要

    未来 pgvector 扩展：
      每轮对话生成一个 embedding 存入 memory_vector 表，
      新问题时检索最相关的历史轮次注入 Prompt Builder。
    """
    user: str
    assistant: str
    timestamp: datetime = field(default_factory=datetime.now)
