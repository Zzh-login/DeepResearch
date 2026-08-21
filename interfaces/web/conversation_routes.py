from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.routing import APIRoute

from infrastructure.auth.dependency import get_current_user
from infrastructure.conversation.pg_repository import PgConversationRepository
from interfaces.web.conversation_schemas import (
    ConversationCreate,
    ConversationRename,
)


class ConversationDatabaseRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def guarded(request: Request):
            try:
                return await original(request)
            except (asyncpg.PostgresError, OSError, ConnectionError) as exc:
                raise HTTPException(503, "会话服务暂不可用") from exc

        return guarded


router = APIRouter(
    prefix="/api/conversations",
    tags=["conversations"],
    route_class=ConversationDatabaseRoute,
)


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise HTTPException(400, "无效的会话 ID") from None


def _repo(request: Request, user_id: str) -> PgConversationRepository:
    database = getattr(request.app.state, "database", None)
    if database is None or database.pool is None:
        raise HTTPException(503, "会话服务暂不可用")
    return PgConversationRepository(database, user_id)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: ConversationCreate,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    conversation_id = await _repo(request, user_id).create(body.title)
    return {"id": str(conversation_id), "title": body.title}


@router.get("")
async def list_conversations(
    request: Request,
    user_id: str = Depends(get_current_user),
):
    settings = request.app.state.settings
    rows = await _repo(request, user_id).list(settings.conversation_page_size)
    return [
        {
            "id": str(row["id"]),
            "title": row["title"],
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }
        for row in rows
    ]


@router.get("/{conversation_id}/messages")
async def list_messages(
    conversation_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    try:
        rows = await _repo(request, user_id).list_messages(
            _uuid(conversation_id),
            request.app.state.settings.message_page_size,
        )
    except LookupError:
        raise HTTPException(404, "会话不存在") from None
    return [
        {
            **row,
            "id": str(row["id"]),
            "conversation_id": str(row["conversation_id"]),
            "knowledge_base_id": (
                str(row["knowledge_base_id"])
                if row.get("knowledge_base_id") else None
            ),
            "research_task_id": (
                str(row["research_task_id"])
                if row.get("research_task_id") else None
            ),
            "created_at": str(row["created_at"]),
        }
        for row in rows
    ]


@router.patch("/{conversation_id}")
async def rename_conversation(
    conversation_id: str,
    body: ConversationRename,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    if not await _repo(request, user_id).rename(
        _uuid(conversation_id), body.title
    ):
        raise HTTPException(404, "会话不存在")
    return {"ok": True, "title": body.title}


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    if not await _repo(request, user_id).delete(_uuid(conversation_id)):
        raise HTTPException(404, "会话不存在")