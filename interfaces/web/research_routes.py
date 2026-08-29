import asyncio
import hashlib
import json
import logging
from typing import AsyncIterator
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.routing import APIRoute

from app.research.export_service import safe_resolve_artifact_path
from infrastructure.auth.dependency import get_current_user
from infrastructure.conversation.pg_repository import PgConversationRepository
from infrastructure.knowledge.pg_repository import PgKnowledgeRepository
from infrastructure.research.pg_repository import (
    IdempotencyConflict,
    PgResearchRepository,
    ResearchQuotaExceeded,
)
from interfaces.web.research_schemas import ExportRequest, ResearchTaskCreate

logger = logging.getLogger(__name__)


class DatabaseUnavailableRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def guarded(request: Request):
            try:
                return await original(request)
            except (asyncpg.PostgresError, OSError, ConnectionError) as exc:
                logger.exception("Research database request failed")
                raise HTTPException(
                    status_code=503,
                    detail="研究服务暂不可用，请稍后重试",
                ) from exc

        return guarded


router = APIRouter(
    prefix="/api/research-tasks",
    tags=["research"],
    route_class=DatabaseUnavailableRoute,
)


def _database(request: Request, require_worker: bool = False):
    database = getattr(request.app.state, "database", None)
    if database is None or database.pool is None:
        raise HTTPException(503, "研究服务暂不可用，请检查 PostgreSQL")
    worker = getattr(request.app.state, "research_worker", None)
    if require_worker and (worker is None or not worker.running):
        raise HTTPException(503, "研究任务处理器尚未就绪")
    return database


def _resource_uuid(value: str, label: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise HTTPException(400, f"无效的{label} ID") from None


def _uuid(value: str) -> UUID:
    return _resource_uuid(value, "研究任务")


def _serialize_export_job(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "task_id": str(row["task_id"]),
        "kind": row["kind"],
        "status": row["status"],
        "artifact_id": (
            str(row["artifact_id"])
            if row.get("artifact_id") else None
        ),
        "error_message": row.get("error_message"),
        "attempts": row.get("attempts", 0),
        "created_at": str(row.get("created_at")),
        "updated_at": str(row.get("updated_at")),
        "completed_at": (
            str(row["completed_at"])
            if row.get("completed_at") else None
        ),
    }


def _serialize_artifact(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "task_id": str(row["task_id"]),
        "kind": row["kind"],
        "filename": row["filename"],
        "sha256": row["sha256"],
        "size_bytes": row["size_bytes"],
        "expires_at": (
            str(row["expires_at"])
            if row.get("expires_at") else None
        ),
        "expired": bool(row.get("expired", False)),
        "created_at": str(row.get("created_at")),
    }


def _serialize(row: dict, sources=None) -> dict:
    return {
        "id": str(row["id"]),
        "query": row["query"],
        "status": row["status"],
        "progress": row["progress"],
        "current_step": row["current_step"],
        "knowledge_base_id": (
            str(row["knowledge_base_id"])
            if row.get("knowledge_base_id") else None
        ),
        "report": row.get("report"),
        "error_message": row.get("error_message"),
        "created_at": str(row.get("created_at")),
        "completed_at": (
            str(row.get("completed_at"))
            if row.get("completed_at") else None
        ),
        "sources": sources or [],
    }


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_research_task(
    body: ResearchTaskCreate,
    request: Request,
    idempotency_key: str = Header(
        min_length=16,
        max_length=100,
        alias="Idempotency-Key",
    ),
    user_id: str = Depends(get_current_user),
):
    database = _database(request)
    settings = request.app.state.settings

    repo = PgResearchRepository(database)
    if not await repo.has_healthy_worker(
        settings.research_worker_stale_seconds
    ):
        raise HTTPException(503, "研究任务处理器尚未就绪")

    conversation_repo = PgConversationRepository(database, user_id)
    if await conversation_repo.get_owned(body.conversation_id) is None:
        raise HTTPException(404, "会话不存在或无权访问")

    if body.knowledge_base_id is not None:
        kb_repo = PgKnowledgeRepository(database, user_id)
        if await kb_repo.get_knowledge_base(body.knowledge_base_id) is None:
            raise HTTPException(404, "知识库不存在或无权访问")

    normalized = json.dumps(
        {
            "query": body.query,
            "knowledge_base_id": (
                str(body.knowledge_base_id)
                if body.knowledge_base_id else None
            ),
            "conversation_id": str(body.conversation_id),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    request_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    try:
        task_id, reused = await repo.create_task(
            owner_id=user_id,
            query=body.query,
            knowledge_base_id=body.knowledge_base_id,
            conversation_id=body.conversation_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            max_active=settings.research_max_active_tasks_per_user,
            ttl_hours=settings.research_idempotency_ttl_hours,
        )
    except LookupError:
        raise HTTPException(404, "会话不存在或无权访问") from None
    except ResearchQuotaExceeded as exc:
        raise HTTPException(409, str(exc)) from exc
    except IdempotencyConflict as exc:
        raise HTTPException(409, str(exc)) from exc

    return {
        "id": str(task_id),
        "status": "created",
        "reused": reused,
    }


@router.get("")
async def list_research_tasks(
    request: Request,
    user_id: str = Depends(get_current_user),
):
    repo = PgResearchRepository(_database(request))
    return [_serialize(row) for row in await repo.list_tasks(user_id)]


@router.get("/{task_id}")
async def get_research_task(
    task_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    task_uuid = _uuid(task_id)
    repo = PgResearchRepository(_database(request))
    row = await repo.get_task(task_uuid, user_id)
    if row is None:
        raise HTTPException(404, "研究任务不存在")
    sources = await repo.list_sources(task_uuid)
    for source in sources:
        source["metadata"] = source.get("metadata") or {}
    plan = await repo.get_plan(task_uuid)
    result = _serialize(row, sources)
    result["plan"] = plan
    result["usage"] = await repo.get_usage_summary(task_uuid, user_id)
    result["usage_records"] = await repo.list_usage(task_uuid, user_id)
    result["evidence_snapshots"] = [
        {
            key: value
            for key, value in snapshot.items()
            if key != "content"
        }
        for snapshot in await repo.list_evidence_snapshots(task_uuid, user_id)
    ]
    return result


@router.post("/{task_id}/cancel")
async def cancel_research_task(
    task_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    repo = PgResearchRepository(_database(request))
    task_uuid = _uuid(task_id)
    existing = await repo.get_task(task_uuid, user_id)
    if existing is None:
        raise HTTPException(404, "研究任务不存在")
    cancel_status = await repo.cancel_task(task_uuid, user_id)
    if cancel_status is None:
        raise HTTPException(409, "当前状态不能取消")
    return {
        "ok": True,
        "status": cancel_status,
        "message": (
            "任务将在当前安全步骤结束后取消"
            if cancel_status == "cancel_requested"
            else "任务已取消"
        ),
    }


@router.post("/{task_id}/pause")
async def pause_research_task(
    task_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    task_uuid = _uuid(task_id)
    repo = PgResearchRepository(_database(request))
    if await repo.get_task(task_uuid, user_id) is None:
        raise HTTPException(404, "研究任务不存在")
    pause_status = await repo.request_pause(task_uuid, user_id)
    if pause_status is None:
        raise HTTPException(409, "当前状态不能暂停")
    return {
        "ok": True,
        "status": pause_status,
        "message": (
            "任务将在当前安全步骤结束后暂停"
            if pause_status == "pause_requested"
            else "任务已暂停"
        ),
    }


@router.post("/{task_id}/resume")
async def resume_research_task(
    task_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    task_uuid = _uuid(task_id)
    repo = PgResearchRepository(_database(request))
    if await repo.get_task(task_uuid, user_id) is None:
        raise HTTPException(404, "研究任务不存在")
    resume_status = await repo.resume_task(task_uuid, user_id)
    if resume_status is None:
        raise HTTPException(409, "只有已暂停任务可以恢复")
    return {
        "ok": True,
        "status": resume_status,
        "message": "任务已恢复，等待 Worker 继续执行",
    }


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_research_task(
    task_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    task_uuid = _uuid(task_id)
    repo = PgResearchRepository(_database(request))
    artifact_files = await repo.list_artifact_files(
        task_uuid,
        user_id,
    )
    result = await repo.delete_task(task_uuid, user_id)
    if result == "not_found":
        raise HTTPException(404, "研究任务不存在")
    if result in {
        "created",
        "planning",
        "searching",
        "reading",
        "verifying",
        "writing",
        "paused",
    }:
        raise HTTPException(409, "研究任务正在运行，请完成或取消后再删除")

    artifact_root = (
        request.app.state.settings.research_artifact_storage_path
    )
    parent_dirs = set()
    for artifact in artifact_files:
        try:
            file_path = safe_resolve_artifact_path(
                artifact_root,
                artifact["storage_key"],
            )
        except ValueError:
            logger.error(
                "Skipped unsafe artifact during task deletion: artifact=%s",
                artifact["id"],
            )
            continue
        parent_dirs.add(file_path.parent)
        try:
            file_path.unlink(missing_ok=True)
        except OSError:
            logger.exception(
                "Task deleted but artifact cleanup failed: %s",
                file_path,
            )
    for parent in parent_dirs:
        try:
            parent.rmdir()
        except OSError:
            pass
    return None

def _safe_research_event_payload(
    event_type: str,
    payload,
) -> dict:
    """
    只向浏览器发送安全摘要。
    不把内部异常堆栈、完整网页内容或敏感字段发送给前端。
    """
    if not isinstance(payload, dict):
        return {}

    allowed_keys = {
        "progress",
        "current_step",
        "reason",
        "error_message",
        "previous_status",
        "node_name",
        "attempt",
        "max_attempts",
        "delay_seconds",
        "resume_from",
        "retryable",
    }

    return {
        key: value
        for key, value in payload.items()
        if key in allowed_keys
    }


async def _research_event_stream(
    request: Request,
    task_id: UUID,
    owner_id: str,
    after_sequence: int,
) -> AsyncIterator[str]:
    repo = PgResearchRepository(request.app.state.database)
    cursor = max(0, int(after_sequence))
    idle_total_seconds = 0
    heartbeat_seconds = 0
    max_idle_seconds = 60 * 30

    while idle_total_seconds < max_idle_seconds:
        if await request.is_disconnected():
            return

        events = await repo.list_events(
            task_id=task_id,
            owner_id=owner_id,
            after_sequence=cursor,
            limit=200,
        )

        if events:
            idle_total_seconds = 0
            heartbeat_seconds = 0

            for event in events:
                cursor = int(event["sequence"])

                created_at = event.get("created_at")
                if hasattr(created_at, "isoformat"):
                    created_at = created_at.isoformat()

                data = {
                    "sequence": cursor,
                    "event_type": event["event_type"],
                    "status": event["status"],
                    "payload": _safe_research_event_payload(
                        event["event_type"],
                        event.get("payload") or {},
                    ),
                    "created_at": created_at,
                }

                yield f"id: {cursor}\n"
                yield f"event: research\n"
                yield (
                    "data: "
                    + json.dumps(
                        data,
                        ensure_ascii=False,
                        default=str,
                    )
                    + "\n\n"
                )

                if event["status"] in {
                    "completed",
                    "failed",
                    "cancelled",
                }:
                    return

        else:
            idle_total_seconds += 2
            heartbeat_seconds += 2

            if heartbeat_seconds >= 15:
                yield ": heartbeat\n\n"
                heartbeat_seconds = 0

            await asyncio.sleep(2)


@router.get("/{task_id}/events")
async def research_events(
    task_id: str,
    request: Request,
    after_sequence: int = 0,
    last_event_id: str | None = Header(
        default=None,
        alias="Last-Event-ID",
    ),
    user_id: str = Depends(get_current_user),
):
    task_uuid = _uuid(task_id)
    repo = PgResearchRepository(_database(request))

    if await repo.get_task(task_uuid, user_id) is None:
        raise HTTPException(404, "研究任务不存在")

    cursor = max(0, after_sequence)

    if last_event_id and last_event_id.strip().isdigit():
        cursor = max(cursor, int(last_event_id.strip()))

    return StreamingResponse(
        _research_event_stream(
            request=request,
            task_id=task_uuid,
            owner_id=user_id,
            after_sequence=cursor,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/{task_id}/exports",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_export(
    task_id: str,
    body: ExportRequest,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    task_uuid = _uuid(task_id)
    repo = PgResearchRepository(_database(request))

    try:
        job_id, job_status, reused = await repo.create_export_job(
            task_uuid,
            user_id,
            body.kind,
        )
    except LookupError:
        raise HTTPException(404, "研究任务不存在") from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc

    return {
        "id": str(job_id),
        "status": job_status,
        "kind": body.kind,
        "reused": reused,
    }


@router.get("/{task_id}/exports")
async def list_exports(
    task_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    task_uuid = _uuid(task_id)
    repo = PgResearchRepository(_database(request))
    if await repo.get_task(task_uuid, user_id) is None:
        raise HTTPException(404, "研究任务不存在")
    return [
        _serialize_export_job(row)
        for row in await repo.list_export_jobs(task_uuid, user_id)
    ]


@router.get("/{task_id}/exports/{job_id}")
async def get_export(
    task_id: str,
    job_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    row = await PgResearchRepository(_database(request)).get_export_job(
        _resource_uuid(job_id, "导出任务"),
        _uuid(task_id),
        user_id,
    )
    if row is None:
        raise HTTPException(404, "导出任务不存在")
    return _serialize_export_job(row)


@router.get("/{task_id}/artifacts")
async def list_research_artifacts(
    task_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    task_uuid = _uuid(task_id)
    repo = PgResearchRepository(_database(request))
    if await repo.get_task(task_uuid, user_id) is None:
        raise HTTPException(404, "研究任务不存在")
    return [
        _serialize_artifact(row)
        for row in await repo.list_artifacts(task_uuid, user_id)
    ]


@router.get("/{task_id}/artifacts/{artifact_id}")
async def download_artifact(
    task_id: str,
    artifact_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    task_uuid = _uuid(task_id)
    artifact_uuid = _resource_uuid(artifact_id, "研究制品")
    artifact = await PgResearchRepository(
        _database(request)
    ).get_artifact(artifact_uuid, task_uuid, user_id)
    if artifact is None:
        raise HTTPException(404, "研究制品不存在")
    artifact_expired = bool(artifact.get("expired"))
    if artifact_expired:
        raise HTTPException(410, "研究制品已过期，请重新导出")

    try:
        file_path = safe_resolve_artifact_path(
            request.app.state.settings.research_artifact_storage_path,
            artifact["storage_key"],
        )
    except ValueError:
        logger.error(
            "Rejected unsafe artifact storage key: artifact=%s",
            artifact_uuid,
        )
        raise HTTPException(410, "研究制品文件不可用") from None

    if not file_path.is_file():
        raise HTTPException(410, "研究制品文件已过期或被清理")

    media_types = {
        "markdown": "text/markdown; charset=utf-8",
        "docx": (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        "pdf": "application/pdf",
    }
    return FileResponse(
        path=file_path,
        filename=artifact["filename"],
        media_type=media_types[artifact["kind"]],
    )


@router.delete(
    "/{task_id}/artifacts/{artifact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_research_artifact(
    task_id: str,
    artifact_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    task_uuid = _uuid(task_id)
    artifact_uuid = _resource_uuid(artifact_id, "研究制品")
    repo = PgResearchRepository(_database(request))
    artifact = await repo.get_artifact(
        artifact_uuid,
        task_uuid,
        user_id,
    )
    if artifact is None:
        raise HTTPException(404, "研究制品不存在")

    try:
        file_path = safe_resolve_artifact_path(
            request.app.state.settings.research_artifact_storage_path,
            artifact["storage_key"],
        )
    except ValueError:
        file_path = None
        logger.error(
            "Skipped unsafe artifact path during deletion: artifact=%s",
            artifact_uuid,
        )

    if not await repo.delete_artifact(
        artifact_uuid,
        task_uuid,
        user_id,
    ):
        raise HTTPException(404, "研究制品不存在")

    if file_path is not None:
        try:
            file_path.unlink(missing_ok=True)
        except OSError:
            logger.exception(
                "Artifact metadata deleted but file cleanup failed: %s",
                file_path,
            )
    return None
