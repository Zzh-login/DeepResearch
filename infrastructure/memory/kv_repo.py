"""
KV 记忆存储 —— 用户偏好 / 长期记忆的键值对存储

技术栈说明：
  - PostgreSQL：结构化数据存储
  - asyncpg：异步 PostgreSQL 驱动
  - 简单键值对：key = user_id + key_name，value = JSON 文本
  - RAG 扩展点：future 可对接 LangChain 的 InMemoryStore / RedisStore

架构角色：
  domain/memory/chat_memory.py  →  对话历史（Message 数组）
  infra/memory/kv_repo.py       →  用户偏好 / 长期记忆（键值对）
  infra/memory/vector_repo.py   →  语义检索记忆（向量）

三者的关系：
  KV Memory 存"用户叫什么、喜欢什么风格"（确定性事实）
  Vector Memory 存"三周前聊过什么相关的话题"（相似度检索）
  Chat Memory 存"刚才聊了什么"（最近 N 轮上下文）
"""

from typing import Optional, Dict, Any
from datetime import datetime

from infrastructure.config.settings import get_settings
from infrastructure.database.codecs import register_json_codecs

try:
    import asyncpg
except ImportError:
    asyncpg = None


TABLE_SQL = """
CREATE TABLE IF NOT EXISTS memory_kv (
    id          SERIAL PRIMARY KEY,
    user_id     VARCHAR(64) NOT NULL DEFAULT 'default',
    key_name    VARCHAR(128) NOT NULL,
    value       JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(user_id, key_name)
);
"""


class KVMemory:
    """
    KV 记忆存储

    职责：
      - 存储用户偏好（如"喜欢简洁"、"爱用 Python"）
      - 存储长期记忆（如"上次聊到项目完结"）
      - 提供 get / set / delete 接口

    数据结构：
      key = f"{user_id}:{key_name}"
      value = JSON（任意结构化数据）

    场景举例：
      set("default", "preferences", {"style": "concise", "lang": "Python"})
      get("default", "preferences")  →  {"style": "concise", "lang": "Python"}
    """

    def __init__(self, dsn: Optional[str] = None):
        """
        dsn: PostgreSQL 连接串，如 postgresql://user:pass@localhost:15432/robot
             不传则从项目 Settings.pg_dsn 读取（由 .env 的 PG_DSN 覆盖）
        """
        self._dsn = dsn
        self._pool = None

    async def _init_conn(self, conn):
        """连接池 init 回调：让 JSONB 读写保持 Python 对象语义。"""
        await register_json_codecs(conn)

    async def _get_pool(self):
        """惰性创建连接池"""
        if self._pool is None:
            dsn = self._dsn or get_settings().pg_dsn
            if asyncpg is None:
                raise ImportError("需要安装 asyncpg：pip install asyncpg")
            self._pool = await asyncpg.create_pool(
                dsn,
                min_size=1,
                max_size=5,
                init=self._init_conn,
            )
        return self._pool

    async def ensure_table(self) -> None:
        """确保表存在（首次启动时自动建表）"""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(TABLE_SQL)

    async def get(self, user_id: str, key_name: str) -> Optional[Any]:
        """
        读取 KV 值

        返回：Python 对象（dict / list / str / None）
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT value FROM memory_kv WHERE user_id=$1 AND key_name=$2",
                user_id, key_name,
            )
        if row:
            return row["value"]
        return None

    async def set(
        self, user_id: str, key_name: str, value: Any
    ) -> None:
        """
        写入 KV 值

        自动处理 INSERT / UPDATE（UPSERT 语义）
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO memory_kv (user_id, key_name, value, created_at, updated_at)
                VALUES ($1, $2, $3::jsonb, NOW(), NOW())
                ON CONFLICT (user_id, key_name)
                DO UPDATE SET value = $3::jsonb, updated_at = NOW()
                """,
                user_id, key_name, value,
            )

    async def delete(self, user_id: str, key_name: str) -> None:
        """删除 KV 值"""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM memory_kv WHERE user_id=$1 AND key_name=$2",
                user_id, key_name,
            )

    async def list_keys(self, user_id: str) -> list[str]:
        """列出某用户的所有 key_name"""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT key_name FROM memory_kv WHERE user_id=$1 ORDER BY updated_at DESC",
                user_id,
            )
        return [row["key_name"] for row in rows]

    async def close(self) -> None:
        """关闭连接池"""
        if self._pool:
            await self._pool.close()
            self._pool = None
