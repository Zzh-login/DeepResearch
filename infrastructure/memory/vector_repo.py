"""
向量记忆存储 —— pgvector 语义检索

架构定位：双轨记忆【冷层】的"语义向量"存储（与 json_long_memory.py 并列：
前者存结构化事实、本文件存可语义检索的向量，两者都由 MemoryExtractor 写入）。

技术栈（最终方案）：
  - PostgreSQL + pgvector: 向量存储与 ANN 检索（余弦距离 <=>）
  - asyncpg: 异步 PostgreSQL 驱动
  - 默认 embedding: BGE-M3（1024 维，见 infrastructure.embedding.bge_m3）
  - 可替换 embedding: 传入自定义 async embed_fn（如 MiniLM / OpenAI）

架构角色：
  Vector Memory 负责"从历史记忆中找出与当前输入语义相似的内容"。
  不存全文，只存摘要 + 向量 + 元数据。

设计决策：
  - embed_fn 可插拔：__init__ 接收 async callable，默认用 BGE-M3
  - embedding_dim 固定 1024（与 BGE-M3 输出维度一致）
  - 接口：add / search / ensure_table / close / count，全异步
  - source 字段隔离用户
  - BGE-M3 未下载时给出清晰指引（不静默失败）
"""

import os
import json
import hashlib
import asyncio
from typing import Optional, List, Dict, Any, Callable, Awaitable

from infrastructure.embedding.bge_m3 import (
    BGE_M3_DIM,
    embed_query,
    embed_documents,
)

# EmbedFn: async (text: str) -> list[float]  （也兼容返回普通 list 的同步函数）
EmbedFn = Callable[[str], Awaitable[List[float]]]

# ============================================================
# VectorMemory
# ============================================================

class VectorMemory:
    """向量记忆存储（pgvector 后端，BGE-M3 默认 embedding）

    用法：
        # 默认：BGE-M3 1024 维 + 本地 PG
        vm = VectorMemory()

        # 自定义连接串
        vm = VectorMemory(dsn="postgresql://user:pass@host:15432/robot")

        # 自定义 embedding（如 MiniLM / OpenAI）
        async def my_embed(text): ...
        vm = VectorMemory(embedding_dim=384, embed_fn=my_embed)
    """

    def __init__(
        self,
        dsn: Optional[str] = None,
        embedding_dim: int = BGE_M3_DIM,
        embed_fn: Optional[EmbedFn] = None,
    ):
        """初始化向量记忆存储。

        Args:
            dsn: PostgreSQL 连接串；缺省时读环境变量 PG_DSN，再缺省用
                 localhost:15432/robot 默认串。
            embedding_dim: 向量维度（默认 1024，与 BGE-M3 一致）。
            embed_fn: 可插拔的异步 embedding 函数；为 None 时走默认 BGE-M3。
        连接池与建表均惰性创建（首次 _get_pool 时），构造本身不连库。
        """
        self._dsn = dsn or os.getenv(
            "PG_DSN",
            "postgresql://postgres:postgres@localhost:15432/robot",
        )
        self._embedding_dim = embedding_dim
        self._embed_fn: Optional[EmbedFn] = embed_fn
        self._pool = None
        self._table_ready = False

    # ---- 连接 ----

    async def _init_conn(self, conn):
        """连接池 init 回调：为每条连接注册 pgvector 类型"""
        from pgvector.asyncpg import register_vector
        await register_vector(conn)

    async def _get_pool(self):
        """惰性创建连接池 + 确保表存在"""
        if self._pool is None:
            import asyncpg
            self._pool = await asyncpg.create_pool(
                self._dsn,
                min_size=1,
                max_size=5,
                init=self._init_conn,
            )
            # 建表（首次创建 pool 时执行一次）
            async with self._pool.acquire() as conn:
                await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
                await self._create_table_sql(conn)
            self._table_ready = True
        return self._pool

    # ---- 建表 ----

    async def _create_table_sql(self, conn) -> None:
        """建表 + 建 HNSW 索引（幂等）。

        表 memory_vectors：source + content_hash 唯一约束实现 UPSERT 去重；
        embedding 列维度用当前 _embedding_dim；HNSW(vector_cosine_ops)
        支撑毫秒级近似最近邻检索。首次创建连接池时调用一次。
        """
        await conn.execute(f"""
            CREATE TABLE IF NOT EXISTS memory_vectors (
                id          SERIAL PRIMARY KEY,
                source      TEXT NOT NULL DEFAULT 'default',
                content_hash TEXT NOT NULL,
                summary     TEXT NOT NULL,
                full_text   TEXT DEFAULT '',
                embedding   vector({self._embedding_dim}),
                metadata    JSONB DEFAULT '{{}}',
                created_at  TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(source, content_hash)
            )
        """)
        # HNSW 索引（pgvector 0.8.x 原生支持，增量构建、无需训练）
        await conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_mv_embedding
            ON memory_vectors
            USING hnsw (embedding vector_cosine_ops)
        """)

    async def ensure_table(self) -> None:
        """确保 pgvector 扩展和表存在（幂等，可单独调用）"""
        if self._table_ready:
            return
        await self._get_pool()  # 内部会建表并置 _table_ready

    # ---- 写 ----

    async def add(
        self,
        source: str,
        summary: str,
        full_text: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        添加记忆片段（幂等 UPSERT，按 source+summary 哈希去重）

        参数：
          source: 用户 / 会话标识
          summary: 语义摘要（用于检索）
          full_text: 完整原文（可选）
          metadata: 自定义元数据

        返回：内容哈希（content_hash）
        """
        content_hash = self._hash(source + summary)
        embedding = await self._embed(summary)

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO memory_vectors
                    (source, content_hash, summary, full_text, embedding, metadata)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                ON CONFLICT (source, content_hash)
                DO UPDATE SET
                    summary    = $3,
                    full_text  = $4,
                    embedding  = $5,
                    metadata   = $6::jsonb,
                    created_at = NOW()
                """,
                source,
                content_hash,
                summary,
                full_text or "",
                embedding,  # list[float] → pgvector 编码器自动转为 vector
                json.dumps(metadata or {}),
            )
        return content_hash

    # ---- 读 ----

    async def search(
        self,
        query: str,
        source: str = "default",
        top_k: int = 3,
        min_score: float = 0.3,
    ) -> List[Dict[str, Any]]:
        """
        语义检索

        参数：
          query: 用户当前输入
          source: 用户隔离
          top_k: 返回条数
          min_score: 相似度阈值（余弦相似度 0-1，越大越严格）

        返回：
          [{"summary": str, "score": float, "metadata": dict}, ...]

        score 解读（余弦相似度 = 1 - 余弦距离）：
          0.9+  → 高度相关
          0.7-0.9 → 相关，可作上下文补充
          0.5-0.7 → 弱相关，不建议注入
          <0.5  → 不相关
        """
        query_embedding = await self._embed(query, is_query=True)

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    summary,
                    metadata,
                    1 - (embedding <=> $1) AS score
                FROM memory_vectors
                WHERE source = $2
                  AND 1 - (embedding <=> $1) >= $3
                ORDER BY embedding <=> $1
                LIMIT $4
                """,
                query_embedding,  # list[float] → vector
                source,
                min_score,
                top_k,
            )

        return [
            {
                "summary": row["summary"],
                "score": round(row["score"], 4),
                "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
            }
            for row in rows
        ]

    # ---- 统计 ----

    async def count(self, source: Optional[str] = None) -> int:
        """返回记忆条数（可按 source 过滤）"""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            if source is not None:
                return await conn.fetchval(
                    "SELECT count(*) FROM memory_vectors WHERE source = $1",
                    source,
                )
            return await conn.fetchval("SELECT count(*) FROM memory_vectors")

    # ---- Embedding ----

    async def _embed(self, text: str, is_query: bool = False) -> List[float]:
        """
        文本 → 向量（默认 BGE-M3，可插拔 embed_fn）

        is_query=True 时（检索查询）走 BGE-M3 查询前缀分支；
        自定义 embed_fn 不感知该标志（直接传 text），保持向后兼容。
        """
        if self._embed_fn is not None:
            out = self._embed_fn(text)
        else:
            # 默认走 BGE-M3（embedding 逻辑已抽离到 infrastructure.embedding.bge_m3）
            if is_query:
                out = await embed_query(text)
            else:
                results = await embed_documents([text])
                out = results[0]
        # 兼容同步函数（返回 list）与异步函数（返回 coroutine）
        if asyncio.iscoroutine(out):
            out = await out
        return out

    # ---- 工具 ----

    @staticmethod
    def _hash(text: str) -> str:
        """对文本取 sha256 前 24 位作内容哈希，用于去重主键（content_hash）。"""
        return hashlib.sha256(text.encode()).hexdigest()[:24]

    # ---- 生命周期 ----

    async def close(self) -> None:
        """释放连接池"""
        if self._pool:
            await self._pool.close()
            self._pool = None
            self._table_ready = False
