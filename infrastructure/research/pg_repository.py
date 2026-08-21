from uuid import UUID, uuid4

from domain.research.models import (
    ResearchSource,
    ResearchStatus,
)
from infrastructure.database.postgres import PostgresDatabase


class ResearchQuotaExceeded(RuntimeError):
    pass


class IdempotencyConflict(RuntimeError):
    pass


class PgResearchRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    async def create_task(
        self,
        owner_id: str,
        query: str,
        knowledge_base_id: UUID | None,
        conversation_id: UUID,
        idempotency_key: str,
        request_hash: str,
        max_active: int,
        ttl_hours: int,
    ) -> tuple[UUID, bool]:
        task_id = uuid4()
        async with self._database.pool.acquire() as conn:
            async with conn.transaction():
                existing = await conn.fetchrow(
                    """
                    SELECT request_hash, task_id
                    FROM research_request_keys
                    WHERE owner_id=$1 AND idempotency_key=$2
                      AND expires_at > NOW()
                    FOR UPDATE
                    """,
                    owner_id,
                    idempotency_key,
                )
                if existing:
                    if existing["request_hash"] != request_hash:
                        raise IdempotencyConflict(
                            "同一个幂等键不能用于不同请求"
                        )
                    return existing["task_id"], True

                owned = await conn.fetchval(
                    """
                    SELECT 1 FROM conversations
                    WHERE id=$1 AND owner_id=$2
                    """,
                    conversation_id,
                    owner_id,
                )
                if not owned:
                    raise LookupError("会话不存在或无权访问")

                active = await conn.fetchval(
                    """
                    SELECT count(*) FROM research_tasks
                    WHERE owner_id=$1 AND status IN ('pending','running')
                    """,
                    owner_id,
                )
                if active >= max_active:
                    raise ResearchQuotaExceeded("已有研究任务正在运行")

                await conn.execute(
                    """
                    INSERT INTO research_tasks (
                        id, owner_id, knowledge_base_id,
                        conversation_id, query
                    ) VALUES ($1,$2,$3,$4,$5)
                    """,
                    task_id,
                    owner_id,
                    knowledge_base_id,
                    conversation_id,
                    query,
                )
                await conn.execute(
                    """
                    INSERT INTO chat_messages (
                        id, conversation_id, owner_id, role, content,
                        requested_mode, resolved_mode, research_task_id
                    ) VALUES ($1,$2,$3,'user',$4,
                              'deep_research','deep_research',$5)
                    """,
                    uuid4(),
                    conversation_id,
                    owner_id,
                    query,
                    task_id,
                )
                await conn.execute(
                    """
                    INSERT INTO research_request_keys (
                        owner_id, idempotency_key, request_hash,
                        task_id, expires_at
                    ) VALUES (
                        $1,$2,$3,$4,NOW() + make_interval(hours => $5)
                    )
                    """,
                    owner_id,
                    idempotency_key,
                    request_hash,
                    task_id,
                    ttl_hours,
                )
        return task_id, False

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

    async def delete_task(self, task_id: UUID, owner_id: str) -> str:
        """Delete an owned terminal task and its dependent research data."""
        async with self._database.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT status FROM research_tasks
                    WHERE id=$1 AND owner_id=$2
                    FOR UPDATE
                    """,
                    task_id,
                    owner_id,
                )
                if row is None:
                    return "not_found"
                if row["status"] in {"pending", "running"}:
                    return row["status"]
                await conn.execute(
                    "DELETE FROM chat_messages WHERE research_task_id=$1",
                    task_id,
                )
                await conn.execute(
                    "DELETE FROM research_tasks WHERE id=$1 AND owner_id=$2",
                    task_id,
                    owner_id,
                )
        return "deleted"

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
                item.content, item.score, item.metadata,
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
        citation_ids = list(dict.fromkeys(report.get("citation_ids", [])))
        message_id = uuid4()
        async with self._database.pool.acquire() as conn:
            async with conn.transaction():
                task = await conn.fetchrow(
                    """
                    UPDATE research_tasks
                    SET status='completed', progress=100,
                        current_step='研究完成', report=$2::jsonb,
                        completed_at=NOW(), updated_at=NOW()
                    WHERE id=$1 AND status='running'
                    RETURNING owner_id, conversation_id
                    """,
                    task_id,
                    report,
                )
                if task is None or task["conversation_id"] is None:
                    return

                await conn.execute(
                    """
                    INSERT INTO chat_messages (
                        id, conversation_id, owner_id, role, content,
                        requested_mode, resolved_mode, research_task_id
                    ) VALUES ($1,$2,$3,'assistant',$4,
                              'deep_research','deep_research',$5)
                    """,
                    message_id,
                    task["conversation_id"],
                    task["owner_id"],
                    report.get("markdown", ""),
                    task_id,
                )

                if citation_ids:
                    sources = await conn.fetch(
                        """
                        SELECT source_id, source_type, title, url,
                               filename, excerpt, score, metadata
                        FROM research_sources
                        WHERE task_id=$1 AND source_id=ANY($2::text[])
                        """,
                        task_id,
                        citation_ids,
                    )
                    by_id = {row["source_id"]: dict(row) for row in sources}
                    rows = []
                    for ordinal, source_id in enumerate(citation_ids, 1):
                        source = by_id.get(source_id)
                        if source is None:
                            continue
                        payload = {
                            key: value
                            for key, value in source.items()
                            if key != "source_type"
                        }
                        rows.append(
                            (
                                uuid4(), message_id, ordinal,
                                source["source_type"], source_id,
                                payload,
                            )
                        )
                    if rows:
                        await conn.executemany(
                            """
                            INSERT INTO message_citations (
                                id, message_id, ordinal, source_kind,
                                source_id, payload
                            ) VALUES ($1,$2,$3,$4,$5,$6::jsonb)
                            """,
                            rows,
                        )

                await conn.execute(
                    """
                    UPDATE conversations SET updated_at=NOW()
                    WHERE id=$1 AND owner_id=$2
                    """,
                    task["conversation_id"],
                    task["owner_id"],
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

    async def replace_plan(
        self,
        task_id: UUID,
        scope: str,
        queries: list[str],
    ) -> None:
        raw_plan = {"scope": scope, "queries": queries}
        rows = [
            (uuid4(), task_id, ordinal, query)
            for ordinal, query in enumerate(queries, 1)
        ]
        async with self._database.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO research_plans (
                        task_id, scope, raw_plan
                    ) VALUES ($1,$2,$3::jsonb)
                    ON CONFLICT (task_id) DO UPDATE SET
                        scope=EXCLUDED.scope,
                        raw_plan=EXCLUDED.raw_plan,
                        updated_at=NOW()
                    """,
                    task_id,
                    scope,
                    raw_plan,
                )
                await conn.execute(
                    "DELETE FROM research_subtasks WHERE task_id=$1",
                    task_id,
                )
                await conn.executemany(
                    """
                    INSERT INTO research_subtasks (
                        id, task_id, ordinal, query
                    ) VALUES ($1,$2,$3,$4)
                    """,
                    rows,
                )

    async def update_subtask(
        self,
        task_id: UUID,
        ordinal: int,
        status: str,
        result_count: int = 0,
        error_message: str | None = None,
    ) -> None:
        async with self._database.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE research_subtasks
                SET status=$3,
                    result_count=$4,
                    error_message=$5,
                    started_at=CASE
                        WHEN $3='running' THEN COALESCE(started_at, NOW())
                        ELSE started_at
                    END,
                    completed_at=CASE
                        WHEN $3 IN ('completed','failed') THEN NOW()
                        ELSE completed_at
                    END,
                    updated_at=NOW()
                WHERE task_id=$1 AND ordinal=$2
                """,
                task_id,
                ordinal,
                status,
                result_count,
                error_message[:500] if error_message else None,
            )

    async def get_plan(self, task_id: UUID) -> dict | None:
        async with self._database.pool.acquire() as conn:
            plan = await conn.fetchrow(
                "SELECT * FROM research_plans WHERE task_id=$1",
                task_id,
            )
            subtasks = await conn.fetch(
                """
                SELECT ordinal, query, status, result_count,
                       error_message, started_at, completed_at
                FROM research_subtasks
                WHERE task_id=$1
                ORDER BY ordinal
                """,
                task_id,
            )
        if plan is None:
            return None
        raw_plan = plan["raw_plan"]
        return {
            "scope": plan["scope"],
            "queries": raw_plan.get("queries", []),
            "subtasks": [dict(row) for row in subtasks],
        }

    async def touch_worker(
        self,
        instance_id: str,
        status: str = "running",
    ) -> None:
        async with self._database.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO service_heartbeats (
                    service_name, instance_id, status, updated_at
                ) VALUES ('research-worker',$1,$2,NOW())
                ON CONFLICT (service_name) DO UPDATE SET
                    instance_id=EXCLUDED.instance_id,
                    status=EXCLUDED.status,
                    updated_at=NOW()
                """,
                instance_id,
                status,
            )

    async def has_healthy_worker(self, stale_seconds: float) -> bool:
        async with self._database.pool.acquire() as conn:
            return bool(
                await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM service_heartbeats
                        WHERE service_name='research-worker'
                          AND status='running'
                          AND updated_at > NOW()
                              - make_interval(secs => $1::double precision)
                    )
                    """,
                    stale_seconds,
                )
            )
