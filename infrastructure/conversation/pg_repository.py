from __future__ import annotations

from uuid import UUID, uuid4

from infrastructure.database.postgres import PostgresDatabase


class PgConversationRepository:
    def __init__(self, database: PostgresDatabase, owner_id: str) -> None:
        self._database = database
        self._owner_id = owner_id

    async def create(self, title: str = "新对话") -> UUID:
        conversation_id = uuid4()
        clean_title = title.strip()[:100] or "新对话"
        async with self._database.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO conversations (id, owner_id, title)
                VALUES ($1, $2, $3)
                """,
                conversation_id,
                self._owner_id,
                clean_title,
            )
        return conversation_id

    async def get_owned(self, conversation_id: UUID) -> dict | None:
        async with self._database.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM conversations
                WHERE id=$1 AND owner_id=$2
                """,
                conversation_id,
                self._owner_id,
            )
        return dict(row) if row else None

    async def list(self, limit: int = 30) -> list[dict]:
        async with self._database.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, title, archived, created_at, updated_at
                FROM conversations
                WHERE owner_id=$1 AND archived=FALSE
                ORDER BY updated_at DESC
                LIMIT $2
                """,
                self._owner_id,
                limit,
            )
        return [dict(row) for row in rows]

    async def rename(self, conversation_id: UUID, title: str) -> bool:
        clean_title = title.strip()[:100]
        if not clean_title:
            return False
        async with self._database.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE conversations
                SET title=$3, updated_at=NOW()
                WHERE id=$1 AND owner_id=$2
                """,
                conversation_id,
                self._owner_id,
                clean_title,
            )
        return result == "UPDATE 1"

    async def delete(self, conversation_id: UUID) -> bool:
        async with self._database.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM conversations WHERE id=$1 AND owner_id=$2",
                conversation_id,
                self._owner_id,
            )
        return result == "DELETE 1"

    async def add_message(
        self,
        conversation_id: UUID,
        role: str,
        content: str,
        requested_mode: str,
        resolved_mode: str | None,
        knowledge_base_id: UUID | None = None,
        research_task_id: UUID | None = None,
        route_metadata: dict | None = None,
        citations: list[dict] | None = None,
    ) -> UUID:
        message_id = uuid4()
        citation_rows = []
        for ordinal, citation in enumerate(citations or [], 1):
            source_id = str(citation.get("source_id", "")).strip()
            source_kind = "web" if source_id.startswith("W") else "knowledge"
            citation_rows.append(
                (
                    uuid4(), message_id, ordinal, source_kind,
                    source_id, citation,
                )
            )

        async with self._database.pool.acquire() as conn:
            async with conn.transaction():
                owned = await conn.fetchval(
                    "SELECT 1 FROM conversations WHERE id=$1 AND owner_id=$2",
                    conversation_id,
                    self._owner_id,
                )
                if not owned:
                    raise LookupError("会话不存在或无权访问")
                await conn.execute(
                    """
                    INSERT INTO chat_messages (
                        id, conversation_id, owner_id, role, content,
                        requested_mode, resolved_mode, knowledge_base_id,
                        research_task_id, route_metadata
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)
                    """,
                    message_id,
                    conversation_id,
                    self._owner_id,
                    role,
                    content,
                    requested_mode,
                    resolved_mode,
                    knowledge_base_id,
                    research_task_id,
                    route_metadata or {},
                )
                if citation_rows:
                    await conn.executemany(
                        """
                        INSERT INTO message_citations (
                            id, message_id, ordinal, source_kind,
                            source_id, payload
                        ) VALUES ($1,$2,$3,$4,$5,$6::jsonb)
                        """,
                        citation_rows,
                    )
                await conn.execute(
                    """
                    UPDATE conversations SET updated_at=NOW()
                    WHERE id=$1 AND owner_id=$2
                    """,
                    conversation_id,
                    self._owner_id,
                )
        return message_id

    async def list_messages(
        self,
        conversation_id: UUID,
        limit: int = 100,
    ) -> list[dict]:
        async with self._database.pool.acquire() as conn:
            owned = await conn.fetchval(
                "SELECT 1 FROM conversations WHERE id=$1 AND owner_id=$2",
                conversation_id,
                self._owner_id,
            )
            if not owned:
                raise LookupError("会话不存在或无权访问")
            rows = await conn.fetch(
                """
                SELECT m.*,
                       COALESCE(
                           jsonb_agg(c.payload ORDER BY c.ordinal)
                           FILTER (WHERE c.id IS NOT NULL),
                           '[]'::jsonb
                       ) AS citations
                FROM chat_messages m
                LEFT JOIN message_citations c ON c.message_id=m.id
                WHERE m.conversation_id=$1 AND m.owner_id=$2
                GROUP BY m.id
                ORDER BY m.created_at, m.id
                LIMIT $3
                """,
                conversation_id,
                self._owner_id,
                limit,
            )
        return [dict(row) for row in rows]
    async def list_context_messages(
        self,
        conversation_id: UUID,
        limit: int = 10,
    ) -> list[dict]:
        async with self._database.pool.acquire() as conn:
            owned = await conn.fetchval(
                """
                SELECT 1
                FROM conversations
                WHERE id=$1 AND owner_id=$2
                """,
                conversation_id,
                self._owner_id,
            )

            if not owned:
                raise LookupError("会话不存在或无权访问")

            rows = await conn.fetch(
                """
                SELECT role, content, requested_mode,
                    resolved_mode, created_at
                FROM chat_messages
                WHERE conversation_id=$1
                AND owner_id=$2
                ORDER BY created_at DESC, id DESC
                LIMIT $3
                """,
                conversation_id,
                self._owner_id,
                limit,
            )

        return [dict(row) for row in reversed(rows)]