import hashlib
import json
import logging
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.routing import APIRoute

from infrastructure.auth.dependency import get_current_user
from infrastructure.conversation.pg_repository import PgConversationRepository
from infrastructure.knowledge.pg_repository import PgKnowledgeRepository
from infrastructure.research.pg_repository import (
    IdempotencyConflict,
    PgResearchRepository,
    ResearchQuotaExceeded,
)
from interfaces.web.research_schemas import ResearchTaskCreate


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


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise HTTPException(400, "无效的研究任务 ID") from None


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
        "status": "pending",
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
    if not await repo.cancel_task(task_uuid, user_id):
        raise HTTPException(409, "当前状态不能取消")
    return {"ok": True, "status": "cancelled"}


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_research_task(
    task_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    result = await PgResearchRepository(_database(request)).delete_task(
        _uuid(task_id),
        user_id,
    )
    if result == "not_found":
        raise HTTPException(404, "研究任务不存在")
    if result in {"pending", "running"}:
        raise HTTPException(409, "研究任务正在运行，请完成或取消后再删除")
    return None
