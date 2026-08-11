"""PgKnowledgeRepository — PostgreSQL 实现的 KnowledgeRepository。

通过构造注入接收 PostgresDatabase 实例，严禁自行创建连接池。
owner_id 在构造时传入，所有数据库查询的 WHERE 条件必须带 owner_id。
"""

import json
from uuid import UUID, uuid4
from typing import Optional

from infrastructure.database.postgres import PostgresDatabase
from domain.knowledge.repository import KnowledgeRepository
from domain.knowledge.models import DocumentStatus, KnowledgeChunk


class KnowledgeBaseBusyError(RuntimeError):
    """Raised when a knowledge base still contains documents being ingested."""


class PgKnowledgeRepository(KnowledgeRepository):
    """PostgreSQL 版知识库仓储实现。

    依赖 pgvector 扩展提供向量检索能力。
    """

    def __init__(self, db: PostgresDatabase, owner_id: str):
        """构造注入。

        Args:
            db: 已建好连接池的 PostgresDatabase 实例。
            owner_id: 当前用户标识，所有 DB 查询的 WHERE 条件强制绑定。
        """
        self._db = db
        self._owner_id = owner_id

    # ═══════════════════════════════════════════
    # 知识库（knowledge_bases 表）
    # ═══════════════════════════════════════════

    async def create_knowledge_base(
        self, name: str, description: str = ""
    ) -> UUID:
        """新建一个知识库，返回它的 ID。"""
        kb_id = uuid4()
        async with self._db.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO knowledge_bases (id, owner_id, name, description) "
                "VALUES ($1, $2, $3, $4)",
                kb_id, self._owner_id, name, description,
            )
        return kb_id

    async def list_knowledge_bases(self) -> list[dict]:
        """列出某个用户的所有知识库。"""
        async with self._db.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, owner_id, name, description, created_at, updated_at "
                "FROM knowledge_bases "
                "WHERE owner_id = $1 "
                "ORDER BY created_at DESC",
                self._owner_id,
            )
        return [dict(row) for row in rows]

    async def delete_knowledge_base(self, kb_id: UUID) -> bool:
        """删除知识库（级联删文档和切片由 DB FOREIGN KEY ON DELETE CASCADE 处理）。

        返回是否成功删除。
        """
        async with self._db.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM knowledge_bases WHERE id = $1 AND owner_id = $2",
                kb_id, self._owner_id,
            )
        # asyncpg execute 返回格式: "DELETE N"，解析 N 判断是否有行被删除
        return _parse_delete_count(result) > 0

    async def delete_knowledge_base_with_documents(
        self,
        kb_id: UUID,
    ) -> Optional[list[str]]:
        """Delete an owned knowledge base and return its document file paths.

        ``None`` means the knowledge base does not exist for this owner. Active
        ingestion is rejected so a background task cannot keep writing while
        its parent knowledge base is being deleted.
        """
        async with self._db.pool.acquire() as conn:
            async with conn.transaction():
                kb = await conn.fetchrow(
                    "SELECT id FROM knowledge_bases "
                    "WHERE id = $1 AND owner_id = $2 FOR UPDATE",
                    kb_id,
                    self._owner_id,
                )
                if kb is None:
                    return None

                documents = await conn.fetch(
                    "SELECT storage_path, status FROM knowledge_documents "
                    "WHERE knowledge_base_id = $1 AND owner_id = $2 FOR UPDATE",
                    kb_id,
                    self._owner_id,
                )
                active_statuses = {
                    row["status"]
                    for row in documents
                    if row["status"] not in {
                        DocumentStatus.READY.value,
                        DocumentStatus.FAILED.value,
                    }
                }
                if active_statuses:
                    statuses = ", ".join(sorted(active_statuses))
                    raise KnowledgeBaseBusyError(statuses)

                result = await conn.execute(
                    "DELETE FROM knowledge_bases WHERE id = $1 AND owner_id = $2",
                    kb_id,
                    self._owner_id,
                )
                if _parse_delete_count(result) != 1:
                    return None

                return [
                    row["storage_path"]
                    for row in documents
                    if row["storage_path"]
                ]

    # ═══════════════════════════════════════════
    # 文档（knowledge_documents 表）
    # ═══════════════════════════════════════════

    async def create_document(
        self,
        kb_id: UUID,
        owner_id: str,
        filename: str,
        mime_type: str,
        storage_path: str,
        content_hash: str,
        file_size: int,
    ) -> UUID:
        """记录一个新上传的文档，返回文档 ID。"""
        doc_id = uuid4()
        async with self._db.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO knowledge_documents "
                "(id, knowledge_base_id, owner_id, filename, mime_type, "
                "storage_path, content_hash, file_size, status) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
                doc_id, kb_id, self._owner_id, filename, mime_type,
                storage_path, content_hash, file_size, DocumentStatus.UPLOADED.value,
            )
        return doc_id

    async def create_document_with_task(
        self,
        kb_id: UUID,
        filename: str,
        mime_type: str,
        storage_path: str,
        content_hash: str,
        file_size: int,
    ) -> tuple[UUID, UUID]:
        """在同一个事务中创建文档记录和入库任务。

        防止 create_document 成功但 create_ingestion_task 失败时
        留下指向不存在文件的孤儿数据库记录。
        """
        doc_id = uuid4()
        task_id = uuid4()

        async with self._db.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO knowledge_documents (
                        id, knowledge_base_id, owner_id, filename,
                        mime_type, storage_path, content_hash,
                        file_size, status
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    """,
                    doc_id,
                    kb_id,
                    self._owner_id,
                    filename,
                    mime_type,
                    storage_path,
                    content_hash,
                    file_size,
                    "uploaded",
                )

                await conn.execute(
                    """
                    INSERT INTO ingestion_tasks (id, document_id, status)
                    VALUES ($1, $2, 'pending')
                    """,
                    task_id,
                    doc_id,
                )

        return doc_id, task_id

    async def update_document_status(
        self,
        doc_id: UUID,
        status: DocumentStatus,
        error_message: Optional[str] = None,
    ) -> None:
        """更新文档处理状态（uploaded → parsing → indexing → ready/failed）。"""
        async with self._db.pool.acquire() as conn:
            await conn.execute(
                "UPDATE knowledge_documents "
                "SET status = $1, error_message = $2, updated_at = NOW() "
                "WHERE id = $3 AND owner_id = $4",
                status.value, error_message, doc_id, self._owner_id,
            )

    async def get_document(self, doc_id: UUID) -> Optional[dict]:
        """查单个文档信息。"""
        async with self._db.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, knowledge_base_id, owner_id, filename, mime_type, "
                "storage_path, content_hash, file_size, status, error_message, "
                "created_at, updated_at "
                "FROM knowledge_documents "
                "WHERE id = $1 AND owner_id = $2",
                doc_id, self._owner_id,
            )
        return dict(row) if row else None

    async def list_documents(self, kb_id: UUID) -> list[dict]:
        """列出某个知识库下的所有文档。"""
        async with self._db.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, knowledge_base_id, owner_id, filename, mime_type, "
                "storage_path, content_hash, file_size, status, error_message, "
                "created_at, updated_at "
                "FROM knowledge_documents "
                "WHERE knowledge_base_id = $1 AND owner_id = $2 "
                "ORDER BY created_at DESC",
                kb_id, self._owner_id,
            )
        return [dict(row) for row in rows]

    async def delete_document(self, doc_id: UUID) -> bool:
        """删除文档及其关联切片。返回是否成功。"""
        async with self._db.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM knowledge_documents WHERE id = $1 AND owner_id = $2",
                doc_id, self._owner_id,
            )
        return _parse_delete_count(result) > 0

    # ═══════════════════════════════════════════
    # 切片（knowledge_chunks 表）—— 核心！
    # ═══════════════════════════════════════════

    async def save_chunks(
        self,
        kb_id: UUID,
        doc_id: UUID,
        chunks: list[KnowledgeChunk],
        embeddings: list[list[float]],
    ) -> None:
        """批量保存文档切片 + 对应的向量。

        chunks 和 embeddings 按索引一一对应，长度必须相等。
        使用 executemany 批量插入，每条 INSERT 对应一个 chunk + embedding。
        """
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"chunks 和 embeddings 长度不匹配: {len(chunks)} vs {len(embeddings)}"
            )

        records: list[tuple] = []
        for chunk, embedding in zip(chunks, embeddings):
            chunk_id = uuid4()
            records.append((
                chunk_id,
                kb_id,
                doc_id,
                chunk.chunk_index,
                chunk.content,
                embedding,
                chunk.page_number,
                chunk.section_title,
                json.dumps(chunk.metadata or {}, ensure_ascii=False),
            ))

        async with self._db.pool.acquire() as conn:
            await conn.executemany(
                "INSERT INTO knowledge_chunks "
                "(id, knowledge_base_id, document_id, chunk_index, content, "
                "embedding, page_number, section_title, metadata) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
                records,
            )

    async def search_chunks(
        self,
        kb_id: UUID,
        embedding: list[float],
        top_k: int = 5,
    ) -> list[dict]:
        """向量语义检索。

        使用 pgvector 的 <=> 运算符计算余弦距离，
        1 - distance 作为相似度分数（越接近 1 越相似）。
        """
        async with self._db.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT\n"
                "    c.document_id,\n"
                "    d.filename,\n"
                "    c.content,\n"
                "    c.page_number,\n"
                "    c.section_title,\n"
                "    1 - (c.embedding <=> $1) AS score\n"
                "FROM knowledge_chunks c\n"
                "JOIN knowledge_documents d ON d.id = c.document_id\n"
                "WHERE c.knowledge_base_id = $2\n"
                "  AND d.owner_id = $3\n"
                "  AND d.status = 'ready'\n"
                "ORDER BY c.embedding <=> $1\n"
                "LIMIT $4",
                embedding, kb_id, self._owner_id, top_k,
            )
        return [dict(row) for row in rows]

    async def replace_chunks(
        self,
        kb_id: UUID,
        doc_id: UUID,
        chunks: list[KnowledgeChunk],
        embeddings: list[list[float]],
    ) -> None:
        """在一个数据库事务中：删除旧切片并写入新切片。

        事务隔离确保原子性——要么全部替换，要么全部不变。
        文件解析和向量化不在此事务内（调用方负责先完成这些耗时操作）。

        Args:
            kb_id: 所属知识库 ID。
            doc_id: 所属文档 ID。
            chunks: 新切片列表。
            embeddings: 对应的向量列表，按索引与 chunks 一一对应。
        """
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"chunks 和 embeddings 长度不匹配: {len(chunks)} vs {len(embeddings)}"
            )

        # 构建 insert 记录（复用 save_chunks 的 INSERT 逻辑）
        records: list[tuple] = []
        for chunk, embedding in zip(chunks, embeddings):
            chunk_id = uuid4()
            records.append((
                chunk_id,
                kb_id,
                doc_id,
                chunk.chunk_index,
                chunk.content,
                embedding,
                chunk.page_number,
                chunk.section_title,
                json.dumps(chunk.metadata or {}, ensure_ascii=False),
            ))

        async with self._db.pool.acquire() as conn:
            async with conn.transaction():
                # 先删旧切片
                await conn.execute(
                    "DELETE FROM knowledge_chunks WHERE document_id = $1",
                    doc_id,
                )
                # 再批量写入新切片
                await conn.executemany(
                    "INSERT INTO knowledge_chunks "
                    "(id, knowledge_base_id, document_id, chunk_index, content, "
                    "embedding, page_number, section_title, metadata) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
                    records,
                )

    async def delete_chunks(self, doc_id: UUID) -> None:
        """删除某个文档的所有切片。"""
        async with self._db.pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM knowledge_chunks WHERE document_id = $1",
                doc_id,
            )

    # ═══════════════════════════════════════════
    # ingestion 任务（ingestion_tasks 表）
    # ═══════════════════════════════════════════

    async def create_ingestion_task(self, doc_id: UUID) -> UUID:
        """为文档创建一个 ingestion 任务，返回任务 ID。"""
        task_id = uuid4()
        async with self._db.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO ingestion_tasks (id, document_id, status) "
                "VALUES ($1, $2, $3)",
                task_id, doc_id, "pending",
            )
        return task_id

    async def update_ingestion_task(
        self,
        task_id: UUID,
        status: str,
        error_message: Optional[str] = None,
        attempts: Optional[int] = None,
    ) -> None:
        """更新 ingestion 任务状态（pending → running → done/failed）。

        只更新传入的非 None 字段。
        """
        set_clauses: list[str] = ["status = $1", "updated_at = NOW()"]
        params: list = [status]
        idx = 2

        if error_message is not None:
            set_clauses.append(f"error_message = ${idx}")
            params.append(error_message)
            idx += 1

        if attempts is not None:
            set_clauses.append(f"attempts = ${idx}")
            params.append(attempts)
            idx += 1

        params.append(task_id)
        sql = f"UPDATE ingestion_tasks SET {', '.join(set_clauses)} WHERE id = ${idx}"

        async with self._db.pool.acquire() as conn:
            await conn.execute(sql, *params)

    async def complete_ingestion(self, doc_id: UUID, task_id: UUID) -> None:
        """Atomically mark a document ready and its ingestion task done."""
        async with self._db.pool.acquire() as conn:
            async with conn.transaction():
                doc_result = await conn.execute(
                    "UPDATE knowledge_documents "
                    "SET status = $1, error_message = NULL, updated_at = NOW() "
                    "WHERE id = $2 AND owner_id = $3",
                    DocumentStatus.READY.value,
                    doc_id,
                    self._owner_id,
                )
                task_result = await conn.execute(
                    "UPDATE ingestion_tasks "
                    "SET status = 'done', error_message = NULL, updated_at = NOW() "
                    "WHERE id = $1 AND document_id = $2",
                    task_id,
                    doc_id,
                )
                if doc_result != "UPDATE 1" or task_result != "UPDATE 1":
                    raise RuntimeError("文档或入库任务不存在，无法完成入库")

    async def fail_ingestion(
        self,
        doc_id: UUID,
        task_id: UUID,
        error_message: str,
    ) -> None:
        """Atomically mark a document and its ingestion task failed."""
        async with self._db.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE knowledge_documents "
                    "SET status = $1, error_message = $2, updated_at = NOW() "
                    "WHERE id = $3 AND owner_id = $4",
                    DocumentStatus.FAILED.value,
                    error_message,
                    doc_id,
                    self._owner_id,
                )
                await conn.execute(
                    "UPDATE ingestion_tasks "
                    "SET status = 'failed', error_message = $1, updated_at = NOW() "
                    "WHERE id = $2 AND document_id = $3",
                    error_message,
                    task_id,
                    doc_id,
                )


# ═════════════════════════════════════════════
# 模块私有工具函数
# ═════════════════════════════════════════════


def _parse_delete_count(result: str) -> int:
    """解析 asyncpg DELETE 返回的 'DELETE N' 字符串，提取行数。

    Args:
        result: asyncpg execute 的返回值，形如 "DELETE 3"。

    Returns:
        删除的行数，解析失败返回 0。
    """
    try:
        return int(result.split()[-1])
    except (ValueError, IndexError, AttributeError):
        return 0
