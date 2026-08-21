import json
from uuid import UUID, uuid4

from domain.research.models import (
    ResearchSource,
    ResearchStatus,
)
from infrastructure.database.postgres import PostgresDatabase


class PgResearchRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    async def create_task(
        self,
        owner_id: str,
        query: str,
        knowledge_base_id: UUID | None,
    ) -> UUID:
        task_id = uuid4()
        async with self._database.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO research_tasks (
                    id, owner_id, knowledge_base_id, query
                ) VALUES ($1, $2, $3, $4)
                """,
                task_id,
                owner_id,
                knowledge_base_id,
                query,
            )
        return task_id

    async def get_task(self, task_id: UUID, owner_id: str) -> dict | None:
        async with self._database.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM research_tasks WHERE id=$1 AND owner_id=$2",
                task_id,
                owner_id,
            )
        return dict(row) if row else None

    async def list_tasks(self, owner_id: str, limit: int = 20) -> list[dict]:
        async with self._database.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, query, status, progress, current_step,
                       knowledge_base_id, error_message,
                       created_at, completed_at
                FROM research_tasks
                WHERE owner_id=$1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                owner_id,
                limit,
            )
        return [dict(row) for row in rows]

    async def list_sources(self, task_id: UUID) -> list[dict]:
        async with self._database.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT source_id, source_type, title, url, filename,
                       excerpt, score, metadata
                FROM research_sources
                WHERE task_id=$1
                ORDER BY source_id
                """,
                task_id,
            )
        return [dict(row) for row in rows]

    async def claim_next_task(self, max_attempts: int) -> dict | None:
        async with self._database.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE research_tasks
                    SET status='failed', current_step='研究失败',
                        error_message='任务重试次数已用尽',
                        completed_at=NOW(), updated_at=NOW()
                    WHERE status='pending' AND attempts >= $1
                    """,
                    max_attempts,
                )
                row = await conn.fetchrow(
                    """
                    SELECT * FROM research_tasks
                    WHERE status='pending' AND attempts < $1
                    ORDER BY created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """,
                    max_attempts,
                )
                if row is None:
                    return None
                await conn.execute(
                    """
                    UPDATE research_tasks
                    SET status='running', progress=5,
                        current_step='制定研究计划',
                        attempts=attempts+1,
                        started_at=COALESCE(started_at, NOW()),
                        updated_at=NOW()
                    WHERE id=$1
                    """,
                    row["id"],
                )
                claimed = dict(row)
                claimed["status"] = "running"
                claimed["progress"] = 5
                return claimed

    async def update_progress(
        self,
        task_id: UUID,
        progress: int,
        current_step: str,
    ) -> None:
        async with self._database.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE research_tasks
                SET progress=$2, current_step=$3, updated_at=NOW()
                WHERE id=$1 AND status='running'
                """,
                task_id,
                progress,
                current_step,
            )

    async def is_cancelled(self, task_id: UUID) -> bool:
        async with self._database.pool.acquire() as conn:
            status = await conn.fetchval(
                "SELECT status FROM research_tasks WHERE id=$1",
                task_id,
            )
        return status == ResearchStatus.CANCELLED.value

    async def cancel_task(self, task_id: UUID, owner_id: str) -> bool:
        async with self._database.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE research_tasks
                SET status='cancelled', current_step='已取消',
                    completed_at=NOW(), updated_at=NOW()
                WHERE id=$1 AND owner_id=$2
                  AND status IN ('pending', 'running')
                """,
                task_id,
                owner_id,
            )
        return result == "UPDATE 1"

    async def replace_sources(
        self,
        task_id: UUID,
        sources: list[ResearchSource],
    ) -> None:
        records = [
            (
                uuid4(), task_id, item.source_id, item.source_type.value,
                item.title, item.url, item.filename, item.excerpt,
                item.content, item.score, json.dumps(item.metadata),
            )
            for item in sources
        ]
        async with self._database.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM research_sources WHERE task_id=$1",
                    task_id,
                )
                if records:
                    await conn.executemany(
                        """
                        INSERT INTO research_sources (
                            id, task_id, source_id, source_type, title,
                            url, filename, excerpt, content, score, metadata
                        ) VALUES (
                            $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb
                        )
                        """,
                        records,
                    )

    async def complete_task(self, task_id: UUID, report: dict) -> None:
        async with self._database.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE research_tasks
                SET status='completed', progress=100,
                    current_step='研究完成', report=$2::jsonb,
                    completed_at=NOW(), updated_at=NOW()
                WHERE id=$1 AND status='running'
                """,
                task_id,
                json.dumps(report, ensure_ascii=False),
            )

    async def fail_task(self, task_id: UUID, message: str) -> None:
        async with self._database.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE research_tasks
                SET status='failed', current_step='研究失败',
                    error_message=$2, completed_at=NOW(), updated_at=NOW()
                WHERE id=$1 AND status <> 'cancelled'
                """,
                task_id,
                message[:1000],
            )

    async def recover_interrupted_tasks(self) -> int:
        async with self._database.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE research_tasks
                SET status='pending', progress=0,
                    current_step='等待重试', updated_at=NOW()
                WHERE status='running'
                """
            )
        return int(result.split()[-1])