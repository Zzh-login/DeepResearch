import random
import hashlib
from uuid import UUID, uuid4
from typing import Literal

from domain.research.checkpoint import ResearchCheckpointState
from domain.research.models import (
    ResearchSource,
    ResearchStatus,
    require_transition,
)
from infrastructure.database.postgres import PostgresDatabase
from domain.model_gateway.contracts import ModelRequestContext


class ResearchQuotaExceeded(RuntimeError):
    pass


class IdempotencyConflict(RuntimeError):
    pass


class PgResearchRepository:
    def __init__(self, database: PostgresDatabase, audit_repository=None) -> None:
        self._database = database
        self._audit_repository = audit_repository

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
                    WHERE owner_id=$1 AND status IN ('created','planning','searching','reading','verifying','writing','paused')
                    """,
                    owner_id,
                )
                if active >= max_active:
                    raise ResearchQuotaExceeded("已有研究任务正在运行")

                await conn.execute(
                    """
                    INSERT INTO research_tasks (
                        id,
                        owner_id,
                        knowledge_base_id,
                        conversation_id,
                        query,
                        status
                    ) VALUES ($1, $2, $3, $4, $5, 'created')
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
                if row["status"] in {
                    "created",
                    "planning",
                    "searching",
                    "reading",
                    "verifying",
                    "writing",
                    "paused",
                }:
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

    async def list_evidence_snapshots(
        self,
        task_id: UUID,
        owner_id: str,
    ) -> list[dict]:
        """Return immutable report evidence only for the task owner."""
        async with self._database.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT evidence.source_id, evidence.source_type,
                       evidence.title, evidence.url, evidence.filename,
                       evidence.excerpt, evidence.content,
                       evidence.content_hash, evidence.used_by_block_ids,
                       evidence.metadata, evidence.captured_at
                FROM research_evidence_snapshots AS evidence
                JOIN research_tasks AS task ON task.id=evidence.task_id
                WHERE evidence.task_id=$1 AND task.owner_id=$2
                ORDER BY evidence.source_id
                """,
                task_id,
                owner_id,
            )
        return [dict(row) for row in rows]

    async def list_usage(
        self,
        task_id: UUID,
        owner_id: str,
    ) -> list[dict]:
        """Return per-operation usage records with owner isolation."""
        async with self._database.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT usage.operation, usage.provider, usage.model,
                       usage.input_tokens, usage.output_tokens,
                       usage.latency_ms, usage.estimated_cost,
                       usage.usage_estimated, usage.created_at
                FROM research_usage AS usage
                JOIN research_tasks AS task ON task.id=usage.task_id
                WHERE usage.task_id=$1 AND task.owner_id=$2
                ORDER BY usage.created_at
                """,
                task_id,
                owner_id,
            )
        return [dict(row) for row in rows]

    async def get_usage_summary(
        self,
        task_id: UUID,
        owner_id: str,
    ) -> dict:
        """Return an auditable aggregate; missing usage is explicit."""
        async with self._database.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    COUNT(usage.id)::int AS call_count,
                    COALESCE(SUM(usage.input_tokens), 0)::int AS input_tokens,
                    COALESCE(SUM(usage.output_tokens), 0)::int AS output_tokens,
                    COALESCE(SUM(usage.latency_ms), 0)::int AS latency_ms,
                    COALESCE(SUM(usage.estimated_cost), 0)::numeric AS estimated_cost,
                    COALESCE(BOOL_OR(usage.usage_estimated), FALSE)
                        AS usage_estimated
                FROM research_usage AS usage
                JOIN research_tasks AS task ON task.id=usage.task_id
                WHERE usage.task_id=$1 AND task.owner_id=$2
                """,
                task_id,
                owner_id,
            )
        return dict(row) if row else {
            "call_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "latency_ms": 0,
            "estimated_cost": 0,
            "usage_estimated": False,
        }

    async def record_usage(
        self,
        task_id: UUID,
        operation: str,
        provider: str,
        model: str,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        latency_ms: int | None = None,
        estimated_cost: float | None = None,
        usage_estimated: bool = False,
    ) -> None:
        """Persist one model/embedding operation without inventing usage."""
        async with self._database.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO research_usage (
                    id, task_id, operation, provider, model,
                    input_tokens, output_tokens, latency_ms,
                    estimated_cost, usage_estimated
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                """,
                uuid4(), task_id, operation, provider, model,
                input_tokens, output_tokens, latency_ms,
                estimated_cost, usage_estimated,
            )

    async def get_latest_checkpoint(
        self,
        task_id: UUID,
        owner_id: str,
    ) -> dict | None:
        """Read the newest checkpoint only for the task owner."""
        async with self._database.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT checkpoint.id, checkpoint.node_name,
                       checkpoint.state, checkpoint.state_hash,
                       checkpoint.attempt, checkpoint.created_at
                FROM research_checkpoints AS checkpoint
                JOIN research_tasks AS task
                  ON task.id = checkpoint.task_id
                WHERE checkpoint.task_id=$1
                  AND task.owner_id=$2
                ORDER BY checkpoint.created_at DESC
                LIMIT 1
                """,
                task_id,
                owner_id,
            )
        return dict(row) if row else None

    async def save_checkpoint(
        self,
        task_id: UUID,
        checkpoint: ResearchCheckpointState,
    ) -> UUID:
        """Persist a pure-data snapshot at a research safety boundary."""
        checkpoint_id = uuid4()
        async with self._database.pool.acquire() as conn:
            async with conn.transaction():
                task_exists = await conn.fetchval(
                    """
                    SELECT 1
                    FROM research_tasks
                    WHERE id=$1
                    """,
                    task_id,
                )
                if task_exists is None:
                    raise LookupError("研究任务不存在")

                await conn.execute(
                    """
                    INSERT INTO research_checkpoints (
                        id,
                        task_id,
                        node_name,
                        state,
                        state_hash,
                        attempt
                    ) VALUES ($1,$2,$3,$4::jsonb,$5,$6)
                    """,
                    checkpoint_id,
                    task_id,
                    checkpoint.current_node,
                    checkpoint.to_dict(),
                    checkpoint.state_hash(),
                    checkpoint.attempt,
                )
        return checkpoint_id

    async def save_checkpoint_and_transition(
        self,
        task_id: UUID,
        checkpoint: ResearchCheckpointState,
        expected_status: str,
        target_status: str,
        current_step: str,
        progress: int,
    ) -> UUID:
        """原子保存 checkpoint、状态迁移和事件；幂等时返回真实记录 ID。"""
        checkpoint_id = uuid4()
        state_hash = checkpoint.state_hash()
        async with self._database.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT status
                    FROM research_tasks
                    WHERE id=$1
                    FOR UPDATE
                    """,
                    task_id,
                )
                if row is None:
                    raise LookupError("研究任务不存在")

                actual_status = row["status"]
                if actual_status != expected_status:
                    if actual_status != target_status:
                        raise RuntimeError(
                            f"研究任务状态竞争：期待 {expected_status}，实际 {actual_status}"
                        )
                    existing_id = await conn.fetchval(
                        """
                        SELECT id
                        FROM research_checkpoints
                        WHERE task_id=$1
                          AND node_name=$2
                          AND state_hash=$3
                        ORDER BY created_at DESC
                        LIMIT 1
                        """,
                        task_id,
                        checkpoint.current_node,
                        state_hash,
                    )
                    if existing_id is None:
                        raise RuntimeError(
                            "任务已迁移到目标状态，但对应 checkpoint 不存在"
                        )
                    return existing_id

                require_transition(actual_status, target_status)
                await conn.execute(
                    """
                    INSERT INTO research_checkpoints (
                        id, task_id, node_name, state, state_hash, attempt
                    ) VALUES ($1,$2,$3,$4::jsonb,$5,$6)
                    """,
                    checkpoint_id,
                    task_id,
                    checkpoint.current_node,
                    checkpoint.to_dict(),
                    state_hash,
                    checkpoint.attempt,
                )
                await self.append_event(
                    task_id,
                    "checkpoint.saved",
                    actual_status,
                    {
                        "node_name": checkpoint.current_node,
                        "attempt": checkpoint.attempt,
                    },
                    conn=conn,
                )
                await conn.execute(
                    """
                    UPDATE research_tasks
                    SET status=$2, progress=$3, current_step=$4, updated_at=NOW()
                    WHERE id=$1
                    """,
                    task_id,
                    target_status,
                    progress,
                    current_step,
                )
                await self.append_event(
                    task_id,
                    "phase.changed",
                    target_status,
                    {
                        "progress": progress,
                        "current_step": current_step,
                    },
                    conn=conn,
                )
        return checkpoint_id

    async def list_events(
        self,
        task_id: UUID,
        owner_id: str,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> list[dict]:
        """读取指定用户任务的事件，并支持断线续传。"""
        after_sequence = max(0, int(after_sequence))
        limit = min(max(1, int(limit)), 200)

        async with self._database.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    event.sequence,
                    event.event_type,
                    event.status,
                    event.payload,
                    event.created_at
                FROM research_events AS event
                JOIN research_tasks AS task
                  ON task.id = event.task_id
                WHERE event.task_id=$1
                  AND task.owner_id=$2
                  AND event.sequence > $3
                ORDER BY event.sequence
                LIMIT $4
                """,
                task_id,
                owner_id,
                after_sequence,
                limit,
            )

        return [dict(row) for row in rows]

    async def claim_next_task(
        self,
        max_attempts: int,
        lease_seconds: int = 60,
    ) -> dict | None:
        async with self._database.pool.acquire() as conn:
            async with conn.transaction():
                exhausted_tasks = await conn.fetch(
                    """
                    UPDATE research_tasks
                    SET status='failed', current_step='研究失败',
                        error_message='任务重试次数已用尽',
                        completed_at=NOW(), lease_owner=NULL,
                        lease_expires_at=NULL, updated_at=NOW()
                    WHERE status='created'
                      AND attempts >= $1
                      AND cancel_requested_at IS NULL
                    RETURNING id
                    """,
                    max_attempts,
                )
                for exhausted in exhausted_tasks:
                    await self.append_event(
                        exhausted["id"],
                        "task.failed",
                        "failed",
                        {
                            "reason": "max_attempts_exceeded",
                            "error_message": "任务重试次数已用尽",
                        },
                        conn=conn,
                    )
                row = await conn.fetchrow(
                    """
                    SELECT * FROM research_tasks
                    WHERE status='created'
                        AND attempts < $1
                        AND next_attempt_at <= NOW()
                        AND (
                            lease_expires_at IS NULL
                            OR lease_expires_at < NOW()
                        )
                        AND cancel_requested_at IS NULL
                    ORDER BY created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """,
                    max_attempts,
                )
                if row is None:
                    return None
                lease_owner = f"research-worker-{uuid4()}"
                claimed = await conn.fetchrow(
                    """
                    UPDATE research_tasks
                    SET status='planning', progress=5,
                        current_step='制定研究计划',
                        attempts=attempts+1,
                        started_at=COALESCE(started_at, NOW()),
                        lease_owner=$2,
                        lease_expires_at=NOW() + make_interval(secs => $3),
                        updated_at=NOW()
                    WHERE id=$1
                      AND status='created'
                      AND cancel_requested_at IS NULL
                    RETURNING *
                    """,
                    row["id"],
                    lease_owner,
                    lease_seconds,
                )
                if claimed is None:
                    return None
                await self.append_event(
                    row["id"],
                    "phase.changed",
                    "planning",
                    {"progress": 5, "current_step": "制定研究计划"},
                    conn=conn,
                )
                return dict(claimed)

    async def renew_lease(
        self,
        task_id: UUID,
        lease_owner: str,
        lease_seconds: int,
    ) -> bool:
        """Extend an active lease only while this worker still owns it."""
        async with self._database.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE research_tasks
                SET lease_expires_at=NOW() + make_interval(secs => $3),
                    updated_at=NOW()
                WHERE id=$1
                  AND lease_owner=$2
                  AND status IN (
                      'planning','searching','reading',
                      'verifying','writing'
                  )
                  AND lease_expires_at > NOW()
                """,
                task_id,
                lease_owner,
                lease_seconds,
            )
        return result == "UPDATE 1"

    async def append_event(
        self,
        task_id: UUID,
        event_type: str,
        status: str,
        payload: dict | None = None,
        conn=None,
    ) -> int:
        async def write(connection):
            sequence = await connection.fetchval(
                """
                UPDATE research_tasks
                SET last_event_sequence=last_event_sequence+1,
                    updated_at=NOW()
                WHERE id=$1
                RETURNING last_event_sequence
                """,
                task_id,
            )
            if sequence is None:
                raise LookupError("研究任务不存在")
            await connection.execute(
                """
                INSERT INTO research_events
                    (id, task_id, sequence, event_type, status, payload)
                VALUES ($1,$2,$3,$4,$5,$6)
                """,
                uuid4(), task_id, sequence, event_type, status, payload or {},
            )
            return int(sequence)

        if conn is not None:
            return await write(conn)
        async with self._database.pool.acquire() as connection:
            async with connection.transaction():
                return await write(connection)

    async def transition_status(
        self,
        task_id: UUID,
        expected_status: str,
        target_status: str,
        current_step: str,
        progress: int,
    ) -> bool:
        async with self._database.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT status FROM research_tasks
                    WHERE id=$1 FOR UPDATE
                    """,
                    task_id,
                )
                if row is None:
                    return False
                actual_status = row["status"]
                if actual_status != expected_status:
                    if actual_status == target_status:
                        return True
                    if actual_status == ResearchStatus.CANCELLED.value:
                        return False
                    raise RuntimeError(
                        f"研究任务状态竞争：期待 {expected_status}，实际 {actual_status}"
                    )
                require_transition(actual_status, target_status)
                await conn.execute(
                    """
                    UPDATE research_tasks
                    SET status=$2, progress=$3, current_step=$4, updated_at=NOW()
                    WHERE id=$1
                    """,
                    task_id, target_status, progress, current_step,
                )
                await self.append_event(
                    task_id,
                    "phase.changed",
                    target_status,
                    {
                        "progress": progress,
                        "current_step": current_step,
                    },
                    conn=conn,
                )
                return True

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
                WHERE id=$1 AND status IN ('planning','searching','reading','verifying','writing')
                """,
                task_id,
                progress,
                current_step,
            )

    async def is_cancelled(self, task_id: UUID) -> bool:
        async with self._database.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT status, cancel_requested_at
                FROM research_tasks WHERE id=$1
                """,
                task_id,
            )
        return bool(
            row
            and (
                row["status"] == ResearchStatus.CANCELLED.value
                or row["cancel_requested_at"] is not None
            )
        )

    async def is_pause_requested(self, task_id: UUID) -> bool:
        async with self._database.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT status, pause_requested_at
                FROM research_tasks
                WHERE id=$1
                """,
                task_id,
            )
        return bool(
            row
            and row["status"] not in {
                ResearchStatus.COMPLETED.value,
                ResearchStatus.FAILED.value,
                ResearchStatus.CANCELLED.value,
            }
            and row["pause_requested_at"] is not None
        )

    async def request_pause(
        self,
        task_id: UUID,
        owner_id: str,
    ) -> str | None:
        """Request a safe pause, or pause a task that has not been claimed."""
        async with self._database.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT status, pause_requested_at
                    FROM research_tasks
                    WHERE id=$1 AND owner_id=$2
                    FOR UPDATE
                    """,
                    task_id,
                    owner_id,
                )
                if row is None:
                    return None
                current = row["status"]
                if current in {
                    ResearchStatus.COMPLETED.value,
                    ResearchStatus.FAILED.value,
                    ResearchStatus.CANCELLED.value,
                }:
                    return None
                if current == ResearchStatus.PAUSED.value:
                    return "paused"
                if current == ResearchStatus.CREATED.value:
                    require_transition(current, ResearchStatus.PAUSED.value)
                    await conn.execute(
                        """
                        UPDATE research_tasks
                        SET status='paused', paused_at=NOW(),
                            next_attempt_at=NOW(),
                            lease_owner=NULL, lease_expires_at=NULL,
                            updated_at=NOW()
                        WHERE id=$1
                        """,
                        task_id,
                    )
                    await self.append_event(
                        task_id,
                        "task.paused",
                        "paused",
                        {"reason": "user_request_before_claim"},
                        conn=conn,
                    )
                    return "paused"
                if row["pause_requested_at"] is not None:
                    return "pause_requested"
                await conn.execute(
                    """
                    UPDATE research_tasks
                    SET pause_requested_at=NOW(), updated_at=NOW()
                    WHERE id=$1
                    """,
                    task_id,
                )
                await self.append_event(
                    task_id,
                    "task.pause_requested",
                    current,
                    {"reason": "user_request"},
                    conn=conn,
                )
                return "pause_requested"

    async def resume_task(
        self,
        task_id: UUID,
        owner_id: str,
    ) -> str | None:
        """Move a paused task back to the durable worker queue."""
        async with self._database.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT status
                    FROM research_tasks
                    WHERE id=$1 AND owner_id=$2
                    FOR UPDATE
                    """,
                    task_id,
                    owner_id,
                )
                if row is None or row["status"] != ResearchStatus.PAUSED.value:
                    return None
                require_transition(
                    ResearchStatus.PAUSED.value,
                    ResearchStatus.CREATED.value,
                )
                await conn.execute(
                    """
                    UPDATE research_tasks
                    SET status='created', current_step='等待恢复',
                        pause_requested_at=NULL, paused_at=NULL,
                        next_attempt_at=NOW(), lease_owner=NULL,
                        lease_expires_at=NULL, updated_at=NOW()
                    WHERE id=$1
                    """,
                    task_id,
                )
                await self.append_event(
                    task_id,
                    "task.resumed",
                    "created",
                    {"reason": "user_request"},
                    conn=conn,
                )
                return "created"

    async def cancel_task(self, task_id: UUID, owner_id: str) -> str | None:
        async with self._database.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT status, cancel_requested_at FROM research_tasks
                    WHERE id=$1 AND owner_id=$2
                    FOR UPDATE
                    """,
                    task_id,
                    owner_id,
                )
                if row is None:
                    return None
                current = row["status"]
                if current in {
                    ResearchStatus.COMPLETED.value,
                    ResearchStatus.FAILED.value,
                    ResearchStatus.CANCELLED.value,
                }:
                    return None
                if current in {
                    ResearchStatus.CREATED.value,
                    ResearchStatus.PAUSED.value,
                }:
                    await conn.execute(
                        """
                        UPDATE research_tasks
                        SET status='cancelled', current_step='已取消',
                            cancel_requested_at=NULL,
                            lease_owner=NULL, lease_expires_at=NULL,
                            completed_at=NOW(), updated_at=NOW()
                        WHERE id=$1
                        """,
                        task_id,
                    )
                    await self.append_event(
                        task_id,
                        "task.cancelled",
                        "cancelled",
                        {"reason": "user_request"},
                        conn=conn,
                    )
                    return "cancelled"

                await conn.execute(
                    """
                    UPDATE research_tasks
                    SET cancel_requested_at=NOW(), updated_at=NOW()
                    WHERE id=$1
                    """,
                    task_id,
                )
                await self.append_event(
                    task_id,
                    "task.cancel_requested",
                    current,
                    {"reason": "user_request"},
                    conn=conn,
                )
                return "cancel_requested"

    async def finalize_cancel(self, task_id: UUID) -> bool:
        """Finalize a cancellation request at a Worker safe boundary."""
        async with self._database.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT status, cancel_requested_at
                    FROM research_tasks
                    WHERE id=$1
                    FOR UPDATE
                    """,
                    task_id,
                )
                if row is None or row["cancel_requested_at"] is None:
                    return False
                if row["status"] in {
                    ResearchStatus.COMPLETED.value,
                    ResearchStatus.FAILED.value,
                    ResearchStatus.CANCELLED.value,
                }:
                    return False
                await conn.execute(
                    """
                    UPDATE research_tasks
                    SET status='cancelled', current_step='已取消',
                        cancel_requested_at=NULL,
                        lease_owner=NULL, lease_expires_at=NULL,
                        completed_at=NOW(), updated_at=NOW()
                    WHERE id=$1
                    """,
                    task_id,
                )
                await self.append_event(
                    task_id,
                    "task.cancelled",
                    "cancelled",
                    {"reason": "worker_safe_boundary"},
                    conn=conn,
                )
                return True

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

    async def _persist_report_audit(
        self,
        conn,
        task_id: UUID,
        report: dict,
    ) -> None:
        """Persist report blocks and the exact cited source snapshots."""
        blocks = report.get("blocks") or []
        block_rows = []
        cited_by_source: dict[str, list[str]] = {}

        for ordinal, block in enumerate(blocks, 1):
            block_id = str(block.get("id", ""))
            citation_ids = list(dict.fromkeys(block.get("citation_ids") or []))
            for source_id in citation_ids:
                cited_by_source.setdefault(str(source_id), []).append(block_id)
            block_rows.append(
                (
                    uuid4(), task_id, ordinal, block_id,
                    str(block.get("section", "")),
                    str(block.get("kind", "")),
                    str(block.get("text", "")),
                    citation_ids,
                    list(dict.fromkeys(block.get("based_on_block_ids") or [])),
                    block.get("columns") or [],
                    block.get("rows") or [],
                )
            )

        await conn.execute(
            "DELETE FROM research_report_blocks WHERE task_id=$1",
            task_id,
        )
        if block_rows:
            await conn.executemany(
                """
                INSERT INTO research_report_blocks (
                    id, task_id, ordinal, block_id, section, kind, text,
                    citation_ids, based_on_block_ids, columns, rows
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                """,
                block_rows,
            )

        source_ids = list(cited_by_source)
        if not source_ids:
            return
        source_rows = await conn.fetch(
            """
            SELECT source_id, source_type, title, url, filename,
                   excerpt, content, metadata
            FROM research_sources
            WHERE task_id=$1 AND source_id=ANY($2::text[])
            """,
            task_id,
            source_ids,
        )
        evidence_rows = []
        for source in source_rows:
            content = str(source["content"] or "")
            evidence_rows.append(
                (
                    uuid4(), task_id, source["source_id"],
                    source["source_type"], source["title"], source["url"],
                    source["filename"], source["excerpt"], content,
                    hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    cited_by_source.get(source["source_id"], []),
                    source["metadata"] or {},
                )
            )
        if evidence_rows:
            await conn.executemany(
                """
                INSERT INTO research_evidence_snapshots (
                    id, task_id, source_id, source_type, title, url,
                    filename, excerpt, content, content_hash,
                    used_by_block_ids, metadata
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                """,
                evidence_rows,
            )

    async def complete_task(
        self,
        task_id: UUID,
        report: dict,
    ) -> Literal["completed", "paused", "cancelled", "state_changed"]:
        """在一个行锁事务内决定完成、暂停或取消，绝不静默失败。"""
        citation_ids = list(dict.fromkeys(report.get("citation_ids", [])))
        message_id = uuid4()

        async with self._database.pool.acquire() as conn:
            async with conn.transaction():
                control = await conn.fetchrow(
                    """
                    SELECT status, owner_id, conversation_id,
                           cancel_requested_at, pause_requested_at
                    FROM research_tasks
                    WHERE id=$1
                    FOR UPDATE
                    """,
                    task_id,
                )
                if control is None:
                    raise LookupError("研究任务不存在")

                status = control["status"]
                if status == "completed":
                    return "completed"
                if status in {"failed", "cancelled", "paused"}:
                    return "state_changed"

                if control["cancel_requested_at"] is not None:
                    await conn.execute(
                        """
                        UPDATE research_tasks
                        SET status='cancelled', current_step='已取消',
                            cancel_requested_at=NULL,
                            pause_requested_at=NULL,
                            lease_owner=NULL, lease_expires_at=NULL,
                            completed_at=NOW(), updated_at=NOW()
                        WHERE id=$1
                        """,
                        task_id,
                    )
                    await self.append_event(
                        task_id,
                        "task.cancelled",
                        "cancelled",
                        {"reason": "completion_boundary"},
                        conn=conn,
                    )
                    return "cancelled"

                if control["pause_requested_at"] is not None:
                    require_transition(status, "paused")
                    await conn.execute(
                        """
                        UPDATE research_tasks
                        SET status='paused', current_step='已暂停',
                            paused_at=NOW(), pause_requested_at=NULL,
                            lease_owner=NULL, lease_expires_at=NULL,
                            updated_at=NOW()
                        WHERE id=$1
                        """,
                        task_id,
                    )
                    await self.append_event(
                        task_id,
                        "task.paused",
                        "paused",
                        {"reason": "completion_boundary"},
                        conn=conn,
                    )
                    return "paused"

                if status != "verifying":
                    return "state_changed"

                task = await conn.fetchrow(
                    """
                    UPDATE research_tasks
                    SET status='completed', progress=100,
                        current_step='研究完成', report=$2::jsonb,
                        completed_at=NOW(),
                        lease_owner=NULL, lease_expires_at=NULL,
                        updated_at=NOW()
                    WHERE id=$1 AND status='verifying'
                    RETURNING owner_id, conversation_id
                    """,
                    task_id,
                    report,
                )
                if task is None:
                    return "state_changed"

                await self._persist_report_audit(conn, task_id, report)
                await self.append_event(
                    task_id,
                    "task.completed",
                    "completed",
                    {"progress": 100},
                    conn=conn,
                )

                if self._audit_repository is not None:
                    try:
                        await self._audit_repository.write(
                            ModelRequestContext(
                                owner_id=str(task["owner_id"]),
                                mode="deep_research",
                                operation="research_complete",
                                research_task_id=task_id,
                                conversation_id=task["conversation_id"],
                            ),
                            "research.complete",
                            "completed",
                            {"progress": 100},
                            conn=conn,
                        )
                    except Exception:
                        # 审计不能阻断已完成的业务事务。
                        pass

                if task["conversation_id"] is None:
                    return "completed"

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
                                source["source_type"], source_id, payload,
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
                return "completed"

    async def fail_task(self, task_id: UUID, message: str) -> None:
        async with self._database.pool.acquire() as conn:
            async with conn.transaction():
                failed = await conn.fetchrow(
                    """
                    UPDATE research_tasks
                    SET status='failed', current_step='研究失败',
                        error_message=$2, completed_at=NOW(),
                        lease_owner=NULL, lease_expires_at=NULL,
                        updated_at=NOW()
                    WHERE id=$1
                      AND status IN (
                          'planning','searching','reading',
                          'verifying','writing'
                      )
                      AND cancel_requested_at IS NULL
                    RETURNING id
                    """,
                    task_id,
                    message[:1000],
                )
                if failed is None:
                    return
                await self.append_event(
                    task_id,
                    "task.failed",
                    "failed",
                    {
                        "error_message": message[:1000],
                        "reason": "worker_failure",
                    },
                    conn=conn,
                )

    async def handle_failure(
        self,
        task_id: UUID,
        message: str,
        retryable: bool,
        max_attempts: int,
        retry_base_seconds: float,
        retry_max_seconds: float,
        retry_jitter_seconds: float,
    ) -> str:
        """Retry transient failures, otherwise finish the task as failed."""
        message = message[:1000]
        async with self._database.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT status, attempts, cancel_requested_at,
                           pause_requested_at
                    FROM research_tasks
                    WHERE id=$1
                    FOR UPDATE
                    """,
                    task_id,
                )
                if row is None or row["status"] in {
                    ResearchStatus.COMPLETED.value,
                    ResearchStatus.FAILED.value,
                    ResearchStatus.CANCELLED.value,
                }:
                    return "ignored"
                if row["cancel_requested_at"] is not None:
                    return "cancel_requested"
                if row["pause_requested_at"] is not None:
                    return "pause_requested"

                attempts = int(row["attempts"] or 0)
                if retryable and attempts < max_attempts:
                    base_delay = min(
                        retry_max_seconds,
                        retry_base_seconds * (2 ** max(0, attempts - 1)),
                    )
                    jitter = random.uniform(0, retry_jitter_seconds)
                    delay = base_delay + jitter
                    await conn.execute(
                        """
                        UPDATE research_tasks
                        SET status='created', current_step=$2,
                            error_message=$3, resume_from=$4,
                            next_attempt_at=NOW()
                                + make_interval(secs => $5::double precision),
                            lease_owner=NULL, lease_expires_at=NULL,
                            completed_at=NULL, updated_at=NOW()
                        WHERE id=$1
                        """,
                        task_id,
                        f"等待第 {attempts + 1} 次重试",
                        message,
                        row["status"],
                        delay,
                    )
                    await self.append_event(
                        task_id,
                        "task.retry_scheduled",
                        "created",
                        {
                            "retryable": True,
                            "attempt": attempts,
                            "max_attempts": max_attempts,
                            "delay_seconds": delay,
                            "resume_from": row["status"],
                            "error_message": message,
                        },
                        conn=conn,
                    )
                    return "retry_scheduled"

                await conn.execute(
                    """
                    UPDATE research_tasks
                    SET status='failed', current_step='研究失败',
                        error_message=$2, completed_at=NOW(),
                        lease_owner=NULL, lease_expires_at=NULL,
                        updated_at=NOW()
                    WHERE id=$1
                    """,
                    task_id,
                    message,
                )
                await self.append_event(
                    task_id,
                    "task.failed",
                    "failed",
                    {
                        "error_message": message,
                        "reason": (
                            "non_retryable"
                            if not retryable
                            else "max_attempts_exceeded"
                        ),
                    },
                    conn=conn,
                )
                return "failed"

    async def finalize_stale_cancel_requests(self) -> int:
        """Worker 崩溃后，把 lease 已失效且已请求取消的任务收敛为 cancelled。"""
        async with self._database.pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """
                    SELECT id
                    FROM research_tasks
                    WHERE status IN (
                        'planning','searching','reading','verifying','writing'
                    )
                      AND cancel_requested_at IS NOT NULL
                      AND (
                          lease_expires_at IS NULL
                          OR lease_expires_at < NOW()
                      )
                    FOR UPDATE SKIP LOCKED
                    """
                )
                for row in rows:
                    await conn.execute(
                        """
                        UPDATE research_tasks
                        SET status='cancelled', current_step='已取消',
                            cancel_requested_at=NULL,
                            pause_requested_at=NULL,
                            lease_owner=NULL, lease_expires_at=NULL,
                            completed_at=NOW(), updated_at=NOW()
                        WHERE id=$1
                        """,
                        row["id"],
                    )
                    await self.append_event(
                        row["id"],
                        "task.cancelled",
                        "cancelled",
                        {"reason": "stale_worker_recovery"},
                        conn=conn,
                    )
                return len(rows)

    async def recover_interrupted_tasks(self) -> int:
        async with self._database.pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """
                    SELECT id, status FROM research_tasks
                    WHERE status IN (
                        'planning','searching','reading',
                        'verifying','writing'
                    )
                      AND cancel_requested_at IS NULL
                      AND (
                          lease_expires_at IS NULL
                          OR lease_expires_at < NOW()
                      )
                    FOR UPDATE SKIP LOCKED
                    """
                )
                for row in rows:
                    await conn.execute(
                        """
                        UPDATE research_tasks
                        SET status='created', progress=0,
                            current_step='等待重试',
                            resume_from=$2,
                            next_attempt_at=NOW(),
                            lease_owner=NULL,
                            lease_expires_at=NULL,
                            updated_at=NOW()
                        WHERE id=$1
                        """,
                        row["id"],
                        row["status"],
                    )
                    await self.append_event(
                        row["id"],
                        "task.recovered",
                        "created",
                        {"previous_status": row["status"]},
                        conn=conn,
                    )
        return len(rows)

    async def mark_paused_if_requested(self, task_id: UUID) -> bool:
        async with self._database.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT status, pause_requested_at
                    FROM research_tasks
                    WHERE id=$1
                    FOR UPDATE
                    """,
                    task_id,
                )
                if row is None or row["pause_requested_at"] is None:
                    return False
                if row["status"] in {
                    ResearchStatus.COMPLETED.value,
                    ResearchStatus.FAILED.value,
                    ResearchStatus.CANCELLED.value,
                }:
                    return False
                require_transition(row["status"], ResearchStatus.PAUSED.value)
                await conn.execute(
                    """
                    UPDATE research_tasks
                    SET status='paused', paused_at=NOW(),
                        pause_requested_at=NULL,
                        lease_owner=NULL, lease_expires_at=NULL,
                        updated_at=NOW()
                    WHERE id=$1
                    """,
                    task_id,
                )
                await self.append_event(
                    task_id,
                    "task.paused",
                    "paused",
                    {"reason": "user_request"},
                    conn=conn,
                )
                return True

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

    async def create_export_job(
        self,
        task_id: UUID,
        owner_id: str,
        kind: str,
    ) -> tuple[UUID, str, bool]:
        """Create or reuse the one durable export job for a task and format."""
        if kind not in {"markdown", "docx", "pdf"}:
            raise ValueError("不支持的导出格式")
        async with self._database.pool.acquire() as conn:
            async with conn.transaction():
                task = await conn.fetchrow(
                    """
                    SELECT status, report
                    FROM research_tasks
                    WHERE id=$1 AND owner_id=$2
                    FOR UPDATE
                    """,
                    task_id,
                    owner_id,
                )
                if task is None:
                    raise LookupError("研究任务不存在")
                if task["status"] != ResearchStatus.COMPLETED.value:
                    raise ValueError("只有已完成的研究任务可以导出")
                if not task["report"]:
                    raise ValueError("研究报告不存在，无法导出")

                existing = await conn.fetchrow(
                    """
                    SELECT job.id, job.status, job.artifact_id,
                           (artifact.expires_at IS NOT NULL
                            AND artifact.expires_at <= NOW()) AS expired
                    FROM research_export_jobs AS job
                    LEFT JOIN research_artifacts AS artifact
                      ON artifact.id=job.artifact_id
                    WHERE job.task_id=$1 AND job.kind=$2
                    FOR UPDATE OF job
                    """,
                    task_id,
                    kind,
                )
                stale_completed = bool(
                    existing
                    and existing["status"] == "completed"
                    and (
                        existing["artifact_id"] is None
                        or existing["expired"]
                    )
                )
                if (
                    existing
                    and existing["status"] != "failed"
                    and not stale_completed
                ):
                    return existing["id"], existing["status"], True
                if existing:
                    row = await conn.fetchrow(
                        """
                        UPDATE research_export_jobs
                        SET status='created', artifact_id=NULL,
                            error_message=NULL, updated_at=NOW(),
                            completed_at=NULL
                        WHERE id=$1
                        RETURNING id, status
                        """,
                        existing["id"],
                    )
                    return row["id"], row["status"], False

                job_id = uuid4()
                await conn.execute(
                    """
                    INSERT INTO research_export_jobs (
                        id, task_id, owner_id, kind, status
                    ) VALUES ($1,$2,$3,$4,'created')
                    """,
                    job_id,
                    task_id,
                    owner_id,
                    kind,
                )
                return job_id, "created", False

    async def get_export_job(
        self,
        job_id: UUID,
        task_id: UUID,
        owner_id: str,
    ) -> dict | None:
        async with self._database.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT job.id, job.task_id, job.kind, job.status,
                       job.artifact_id, job.error_message, job.attempts,
                       job.created_at, job.updated_at, job.completed_at
                FROM research_export_jobs AS job
                JOIN research_tasks AS task ON task.id=job.task_id
                WHERE job.id=$1 AND job.task_id=$2
                  AND job.owner_id=$3 AND task.owner_id=$3
                """,
                job_id,
                task_id,
                owner_id,
            )
        return dict(row) if row else None

    async def list_export_jobs(
        self,
        task_id: UUID,
        owner_id: str,
    ) -> list[dict]:
        async with self._database.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT job.id, job.task_id, job.kind, job.status,
                       job.artifact_id, job.error_message, job.attempts,
                       job.created_at, job.updated_at, job.completed_at
                FROM research_export_jobs AS job
                JOIN research_tasks AS task ON task.id=job.task_id
                WHERE job.task_id=$1
                  AND job.owner_id=$2 AND task.owner_id=$2
                ORDER BY job.created_at
                """,
                task_id,
                owner_id,
            )
        return [dict(row) for row in rows]

    async def claim_export_job(self) -> dict | None:
        """Atomically claim one queued export job for an export worker."""
        async with self._database.pool.acquire() as conn:
            async with conn.transaction():
                job = await conn.fetchrow(
                    """
                    SELECT job.*, task.query, task.report
                    FROM research_export_jobs AS job
                    JOIN research_tasks AS task ON task.id=job.task_id
                    WHERE job.status='created' AND task.status='completed'
                    ORDER BY job.created_at
                    FOR UPDATE OF job SKIP LOCKED
                    LIMIT 1
                    """
                )
                if job is None:
                    return None
                claimed = await conn.fetchrow(
                    """
                    UPDATE research_export_jobs
                    SET status='running', attempts=attempts+1,
                        error_message=NULL, updated_at=NOW()
                    WHERE id=$1 AND status='created'
                    RETURNING id, status, attempts
                    """,
                    job["id"],
                )
                if claimed is None:
                    return None
                result = dict(job)
                result.update(dict(claimed))
                return result

    async def complete_export_job(
        self,
        job_id: UUID,
        *,
        filename: str,
        storage_key: str,
        sha256: str,
        size_bytes: int,
        ttl_days: int,
    ) -> UUID:
        """Create/reuse the artifact and complete its job atomically."""
        artifact_id = uuid4()
        async with self._database.pool.acquire() as conn:
            async with conn.transaction():
                job = await conn.fetchrow(
                    """
                    SELECT task_id, kind, status
                    FROM research_export_jobs
                    WHERE id=$1
                    FOR UPDATE
                    """,
                    job_id,
                )
                if job is None:
                    raise LookupError("导出任务不存在")
                if job["status"] == "completed":
                    existing_id = await conn.fetchval(
                        "SELECT artifact_id FROM research_export_jobs WHERE id=$1",
                        job_id,
                    )
                    if existing_id is None:
                        raise RuntimeError("已完成导出任务缺少制品")
                    return existing_id
                if job["status"] != "running":
                    raise RuntimeError("导出任务不在运行状态")

                artifact = await conn.fetchrow(
                    """
                    INSERT INTO research_artifacts (
                        id, task_id, kind, filename, storage_key,
                        sha256, size_bytes, expires_at
                    ) VALUES (
                        $1,$2,$3,$4,$5,$6,$7,
                        NOW() + make_interval(days => $8)
                    )
                    ON CONFLICT (task_id, kind, sha256) DO UPDATE SET
                        filename=EXCLUDED.filename,
                        storage_key=EXCLUDED.storage_key,
                        size_bytes=EXCLUDED.size_bytes,
                        expires_at=EXCLUDED.expires_at
                    RETURNING id
                    """,
                    artifact_id,
                    job["task_id"],
                    job["kind"],
                    filename,
                    storage_key,
                    sha256,
                    size_bytes,
                    ttl_days,
                )
                artifact_id = artifact["id"]
                await conn.execute(
                    """
                    UPDATE research_export_jobs
                    SET status='completed', artifact_id=$2,
                        error_message=NULL, completed_at=NOW(), updated_at=NOW()
                    WHERE id=$1
                    """,
                    job_id,
                    artifact_id,
                )
                await self.append_event(
                    job["task_id"],
                    "artifact.ready",
                    ResearchStatus.COMPLETED.value,
                    {
                        "artifact_id": str(artifact_id),
                        "kind": job["kind"],
                        "filename": filename,
                        "size_bytes": size_bytes,
                    },
                    conn=conn,
                )
        return artifact_id

    async def fail_export_job(self, job_id: UUID, message: str) -> bool:
        async with self._database.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    UPDATE research_export_jobs
                    SET status='failed', error_message=$2,
                        completed_at=NOW(), updated_at=NOW()
                    WHERE id=$1 AND status IN ('created','running')
                    RETURNING task_id, kind
                    """,
                    job_id,
                    message[:1000],
                )
                if row is None:
                    return False
                await self.append_event(
                    row["task_id"],
                    "artifact.failed",
                    ResearchStatus.COMPLETED.value,
                    {
                        "job_id": str(job_id),
                        "kind": row["kind"],
                        "error_message": message[:1000],
                    },
                    conn=conn,
                )
                return True

    async def recover_export_jobs(self) -> int:
        """Requeue jobs left running by a stopped export worker."""
        async with self._database.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE research_export_jobs
                SET status='created',
                    error_message='导出处理器中断，等待恢复',
                    completed_at=NULL, updated_at=NOW()
                WHERE status='running'
                """
            )
        return int(result.rsplit(" ", 1)[-1])

    async def cleanup_expired_artifacts(self, limit: int) -> list[dict]:
        """Remove expired metadata transactionally and return file keys."""
        async with self._database.pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """
                    SELECT artifact.id, artifact.task_id,
                           artifact.kind, artifact.storage_key
                    FROM research_artifacts AS artifact
                    WHERE artifact.expires_at IS NOT NULL
                      AND artifact.expires_at <= NOW()
                    ORDER BY artifact.expires_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT $1
                    """,
                    limit,
                )
                removed = []
                for row in rows:
                    await conn.execute(
                        """
                        UPDATE research_export_jobs
                        SET artifact_id=NULL, status='failed',
                            error_message='研究制品已过期，请重新导出',
                            completed_at=NOW(), updated_at=NOW()
                        WHERE artifact_id=$1
                        """,
                        row["id"],
                    )
                    deleted = await conn.fetchrow(
                        """
                        DELETE FROM research_artifacts
                        WHERE id=$1
                        RETURNING id, task_id, kind, storage_key
                        """,
                        row["id"],
                    )
                    if deleted is None:
                        continue
                    await self.append_event(
                        row["task_id"],
                        "artifact.expired",
                        ResearchStatus.COMPLETED.value,
                        {
                            "artifact_id": str(row["id"]),
                            "kind": row["kind"],
                        },
                        conn=conn,
                    )
                    removed.append(dict(deleted))
                return removed

    async def list_artifacts(
        self,
        task_id: UUID,
        owner_id: str,
    ) -> list[dict]:
        async with self._database.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT artifact.id, artifact.task_id, artifact.kind,
                       artifact.filename, artifact.sha256,
                       artifact.size_bytes, artifact.expires_at,
                       artifact.created_at,
                       (artifact.expires_at IS NOT NULL
                        AND artifact.expires_at <= NOW()) AS expired
                FROM research_artifacts AS artifact
                JOIN research_tasks AS task ON task.id=artifact.task_id
                WHERE artifact.task_id=$1 AND task.owner_id=$2
                ORDER BY artifact.created_at DESC
                """,
                task_id,
                owner_id,
            )
        return [dict(row) for row in rows]

    async def list_artifact_files(
        self,
        task_id: UUID,
        owner_id: str,
    ) -> list[dict]:
        """Return private storage keys for owned task deletion cleanup."""
        async with self._database.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT artifact.id, artifact.storage_key
                FROM research_artifacts AS artifact
                JOIN research_tasks AS task ON task.id=artifact.task_id
                WHERE artifact.task_id=$1 AND task.owner_id=$2
                """,
                task_id,
                owner_id,
            )
        return [dict(row) for row in rows]

    async def get_artifact(
        self,
        artifact_id: UUID,
        task_id: UUID,
        owner_id: str,
    ) -> dict | None:
        async with self._database.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT artifact.*,
                       (artifact.expires_at IS NOT NULL
                        AND artifact.expires_at <= NOW()) AS expired
                FROM research_artifacts AS artifact
                JOIN research_tasks AS task ON task.id=artifact.task_id
                WHERE artifact.id=$1 AND artifact.task_id=$2
                  AND task.owner_id=$3
                """,
                artifact_id,
                task_id,
                owner_id,
            )
        return dict(row) if row else None

    async def delete_artifact(
        self,
        artifact_id: UUID,
        task_id: UUID,
        owner_id: str,
    ) -> bool:
        async with self._database.pool.acquire() as conn:
            async with conn.transaction():
                owned = await conn.fetchval(
                    """
                    SELECT 1
                    FROM research_artifacts AS artifact
                    JOIN research_tasks AS task ON task.id=artifact.task_id
                    WHERE artifact.id=$1 AND artifact.task_id=$2
                      AND task.owner_id=$3
                    FOR UPDATE OF artifact
                    """,
                    artifact_id,
                    task_id,
                    owner_id,
                )
                if not owned:
                    return False
                await conn.execute(
                    """
                    UPDATE research_export_jobs
                    SET artifact_id=NULL, status='failed',
                        error_message='研究制品已由用户删除',
                        completed_at=NOW(), updated_at=NOW()
                    WHERE artifact_id=$1
                    """,
                    artifact_id,
                )
                result = await conn.execute(
                    "DELETE FROM research_artifacts WHERE id=$1 AND task_id=$2",
                    artifact_id,
                    task_id,
                )
                if result == "DELETE 1":
                    await self.append_event(
                        task_id,
                        "artifact.deleted",
                        ResearchStatus.COMPLETED.value,
                        {"artifact_id": str(artifact_id)},
                        conn=conn,
                    )
                return result == "DELETE 1"

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
