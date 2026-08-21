import logging
from collections.abc import AsyncIterator
from typing import Any

import asyncpg

from app.chat.auto_router import AutoChatRouter, AutoRouterError
from app.rag.graph import RagGraph, RagModelError, RagRetrievalError
from app.rag.hybrid_graph import HybridGraph
from app.session.session import ChatSession
from domain.chat.modes import ChatMode, is_chat_mode_enabled
from domain.chat.routing import ResolvedChatMode, RouteDecision
from infrastructure.config.settings import Settings
from infrastructure.database.postgres import PostgresDatabase
from infrastructure.knowledge.pg_repository import PgKnowledgeRepository
from interfaces.web.chat_schemas import ChatRequest


logger = logging.getLogger(__name__)


DATABASE_ERRORS = (
    ConnectionError,
    TimeoutError,
    asyncpg.PostgresConnectionError,
    asyncpg.CannotConnectNowError,
    asyncpg.TooManyConnectionsError,
)


class ChatProtocolError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ManualChatOrchestrator:
    def __init__(
        self,
        database: PostgresDatabase,
        rag_graph: RagGraph | None,
        settings: Settings,
        hybrid_graph: HybridGraph | None = None,
        auto_router: AutoChatRouter | None = None,
    ) -> None:
        self._database = database
        self._rag_graph = rag_graph
        self._hybrid_graph = hybrid_graph
        self._auto_router = auto_router
        self._settings = settings

    @staticmethod
    def _route_payload(decision: RouteDecision | None) -> dict[str, Any]:
        if decision is None:
            return {}
        return {
            "route_source": decision.source.value,
            "route_confidence": decision.confidence,
            "route_reason": decision.reason,
        }

    async def _run_normal(
        self,
        request: ChatRequest,
        session: ChatSession,
        user_id: str,
        requested_mode: ChatMode,
        decision: RouteDecision | None,
    ) -> AsyncIterator[dict[str, Any]]:
        session.set_agent_mode(request.agent_mode)
        route_payload = self._route_payload(decision)
        async for event in session.chat_stream(request.text, user_id=user_id):
            if event["type"] == "token":
                yield {
                    "type": "chat.token",
                    "mode": ResolvedChatMode.NORMAL.value,
                    "requested_mode": requested_mode.value,
                    "text": event["text"],
                    **route_payload,
                }
            elif event["type"] == "done":
                yield {
                    "type": "chat.done",
                    "mode": ResolvedChatMode.NORMAL.value,
                    "requested_mode": requested_mode.value,
                    "text": event["text"],
                    "error": bool(event.get("error")),
                    "citations": [],
                    **route_payload,
                }

    def _require_knowledge_dependencies(self) -> None:
        if self._database.pool is None:
            raise ChatProtocolError(
                "database_unavailable",
                "知识库服务暂不可用，请稍后重试",
            )

    async def _get_owned_repo(
        self,
        user_id: str,
        knowledge_base_id,
    ) -> PgKnowledgeRepository:
        self._require_knowledge_dependencies()
        repo = PgKnowledgeRepository(self._database, user_id)
        try:
            knowledge_base = await repo.get_knowledge_base(knowledge_base_id)
        except DATABASE_ERRORS as exc:
            raise ChatProtocolError(
                "database_unavailable",
                "知识库服务暂不可用，请稍后重试",
            ) from exc
        if knowledge_base is None:
            raise ChatProtocolError(
                "knowledge_base_not_found",
                "知识库不存在或无权访问",
            )
        return repo

    async def _run_knowledge(
        self,
        request: ChatRequest,
        user_id: str,
        requested_mode: ChatMode,
        decision: RouteDecision | None,
    ) -> AsyncIterator[dict[str, Any]]:
        if self._rag_graph is None:
            raise ChatProtocolError(
                "rag_unavailable",
                "严格知识库问答暂不可用，请检查模型配置",
            )
        kb_id = request.knowledge_base_id
        if kb_id is None:
            raise ChatProtocolError(
                "knowledge_base_required",
                "当前回答方式必须选择知识库",
            )

        repo = await self._get_owned_repo(user_id, kb_id)
        try:
            answer = await self._rag_graph.answer(
                repo=repo,
                knowledge_base_id=kb_id,
                query=request.text,
                top_k=self._settings.rag_top_k,
            )
        except RagModelError as exc:
            raise ChatProtocolError(
                "model_unavailable",
                "回答模型暂不可用，请稍后重试",
            ) from exc
        except (RagRetrievalError, *DATABASE_ERRORS) as exc:
            raise ChatProtocolError(
                "database_unavailable",
                "知识库服务暂不可用，请稍后重试",
            ) from exc

        yield self._answer_event(
            answer=answer,
            resolved_mode=ResolvedChatMode.KNOWLEDGE,
            requested_mode=requested_mode,
            decision=decision,
        )

    async def _run_hybrid(
        self,
        request: ChatRequest,
        user_id: str,
        requested_mode: ChatMode,
        decision: RouteDecision | None,
    ) -> AsyncIterator[dict[str, Any]]:
        if self._hybrid_graph is None:
            raise ChatProtocolError(
                "hybrid_unavailable",
                "混合问答暂不可用，请检查模型配置",
            )
        kb_id = request.knowledge_base_id
        if kb_id is None:
            raise ChatProtocolError(
                "knowledge_base_required",
                "当前回答方式必须选择知识库",
            )

        repo = await self._get_owned_repo(user_id, kb_id)
        try:
            answer = await self._hybrid_graph.answer(
                repo=repo,
                knowledge_base_id=kb_id,
                query=request.text,
                top_k=self._settings.rag_top_k,
            )
        except RagModelError as exc:
            raise ChatProtocolError(
                "model_unavailable",
                "混合回答模型暂不可用，请稍后重试",
            ) from exc
        except (RagRetrievalError, *DATABASE_ERRORS) as exc:
            raise ChatProtocolError(
                "database_unavailable",
                "知识库服务暂不可用，请稍后重试",
            ) from exc

        yield self._answer_event(
            answer=answer,
            resolved_mode=ResolvedChatMode.HYBRID,
            requested_mode=requested_mode,
            decision=decision,
        )

    def _answer_event(
        self,
        answer,
        resolved_mode: ResolvedChatMode,
        requested_mode: ChatMode,
        decision: RouteDecision | None,
    ) -> dict[str, Any]:
        return {
            "type": "chat.done",
            "mode": resolved_mode.value,
            "requested_mode": requested_mode.value,
            "text": answer.answer,
            "status": answer.status.value,
            "citations": [
                {
                    "source_id": item.source_id,
                    "chunk_id": str(item.chunk_id),
                    "document_id": str(item.document_id),
                    "chunk_index": item.chunk_index,
                    "filename": item.filename,
                    "excerpt": item.excerpt,
                    "score": item.score,
                    "page_number": item.page_number,
                    "section_title": item.section_title,
                }
                for item in answer.citations
            ],
            "retrieved_count": answer.retrieved_count,
            "citation_valid": answer.citation_valid,
            **self._route_payload(decision),
        }

    async def run(
        self,
        request: ChatRequest,
        session: ChatSession,
        user_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        if not is_chat_mode_enabled(request.mode):
            raise ChatProtocolError(
                "mode_not_available",
                "该回答方式尚未开放",
            )

        requested_mode = request.mode
        if requested_mode == ChatMode.DEEP_RESEARCH:
            raise ChatProtocolError(
                "mode_not_available",
                "深度研究请通过研究任务接口发起",
            )
        decision = None
        resolved_mode = ResolvedChatMode(requested_mode.value) \
            if requested_mode != ChatMode.AUTO \
            else None

        if requested_mode == ChatMode.AUTO:
            if self._auto_router is None:
                raise ChatProtocolError(
                    "router_unavailable",
                    "自动回答方式暂不可用，请稍后重试",
                )
            try:
                decision = await self._auto_router.route(request.text)
            except AutoRouterError as exc:
                logger.exception("Auto router failed for user=%s", user_id)
                raise ChatProtocolError(
                    "router_unavailable",
                    "自动分类失败，请手动选择回答方式",
                ) from exc
            resolved_mode = decision.mode

        if resolved_mode == ResolvedChatMode.NORMAL:
            async for event in self._run_normal(
                request,
                session,
                user_id,
                requested_mode,
                decision,
            ):
                yield event
            return

        if resolved_mode == ResolvedChatMode.KNOWLEDGE:
            async for event in self._run_knowledge(
                request,
                user_id,
                requested_mode,
                decision,
            ):
                yield event
            return

        if resolved_mode == ResolvedChatMode.HYBRID:
            async for event in self._run_hybrid(
                request,
                user_id,
                requested_mode,
                decision,
            ):
                yield event
            return

        raise ChatProtocolError("invalid_route", "自动分类结果不可执行")