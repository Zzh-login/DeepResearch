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
from app.chat.memory_policy import build_memory_policy
from domain.memory.context import MemoryContext
from infrastructure.conversation.pg_repository import ( PgConversationRepository, )
from app.chat.memory_service import UnifiedMemoryService
from domain.model_gateway.contracts import ModelRequestContext


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
        audit_repository=None,
    ) -> None:
        self._database = database
        self._rag_graph = rag_graph
        self._hybrid_graph = hybrid_graph
        self._auto_router = auto_router
        self._audit = audit_repository
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
        async for event in session.chat_stream(
            request.text,
            user_id=user_id,
            conversation_id=request.conversation_id,
        ):
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
            memory_context, conversation_context = (
                await self._build_memory_context(
                    request=request,
                    user_id=user_id,
                    resolved_mode=ResolvedChatMode.KNOWLEDGE.value,
                )
            )

            answer = await self._rag_graph.answer(
                repo=repo,
                knowledge_base_id=kb_id,
                query=request.text,
                top_k=self._settings.rag_top_k,
                conversation_context=conversation_context,
                user_memory_context=memory_context.user_memory_context,
                owner_id=user_id,
                conversation_id=request.conversation_id,
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

        yield await self._answer_event(
            answer=answer,
            resolved_mode=ResolvedChatMode.KNOWLEDGE,
            requested_mode=requested_mode,
            decision=decision,
            owner_id=user_id,
            conversation_id=request.conversation_id,
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
            memory_context, conversation_context = (
                await self._build_memory_context(
                    request=request,
                    user_id=user_id,
                    resolved_mode=ResolvedChatMode.HYBRID.value,
                )
            )

            answer = await self._hybrid_graph.answer(
                repo=repo,
                knowledge_base_id=kb_id,
                query=request.text,
                top_k=self._settings.rag_top_k,
                conversation_context=conversation_context,
                user_memory_context=memory_context.user_memory_context,
                owner_id=user_id,
                conversation_id=request.conversation_id,
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

        yield await self._answer_event(
            answer=answer,
            resolved_mode=ResolvedChatMode.HYBRID,
            requested_mode=requested_mode,
            decision=decision,
            owner_id=user_id,
            conversation_id=request.conversation_id,
        )

    async def _build_memory_context(
        self,
        request,
        user_id: str,
        resolved_mode: str,
    ) -> tuple[MemoryContext, list[dict]]:
        conversation_repo = PgConversationRepository(
            self._database,
            user_id,
        )

        messages = await conversation_repo.list_context_messages(
            request.conversation_id,
            limit=10,
        )

        policy = build_memory_policy(resolved_mode)

        user_memory_context = UnifiedMemoryService(
            user_id
        ).render_for_mode(resolved_mode)

        context = MemoryContext(
            user_id=user_id,
            conversation_id=request.conversation_id,
            mode=resolved_mode,
            knowledge_base_id=request.knowledge_base_id,
            include_user_memory=bool(user_memory_context),
            include_conversation=policy["conversation"],
            user_memory_context=user_memory_context,
        )

        return context, messages
    async def _answer_event(
        self,
        answer,
        resolved_mode: ResolvedChatMode,
        requested_mode: ChatMode,
        decision: RouteDecision | None,
        owner_id: str,
        conversation_id,
    ) -> dict[str, Any]:
        show_diag = self._settings.show_hybrid_diagnostics
        raw_issues = getattr(answer, "validation_issues", []) or []
        repair_attempts = getattr(answer, "repair_attempts", 0)
        max_repair_attempts = getattr(answer, "max_repair_attempts", 1)
        status_str = answer.status.value

        text = answer.answer
        if (
            not show_diag
            and status_str == "citation_rejected"
            and resolved_mode == ResolvedChatMode.HYBRID
        ):
            text = "混合回答暂时无法生成，请稍后重试"

        diagnostics = {
            "validation_issues": raw_issues if show_diag else [],
            "citation_valid": answer.citation_valid,
            "status": status_str,
            "repair_attempts": repair_attempts,
            "max_repair_attempts": max_repair_attempts,
        }

        if self._audit is not None:
            try:
                await self._audit.write(
                    ModelRequestContext(
                        owner_id=owner_id,
                        mode=resolved_mode.value,
                        operation="chat_answer",
                        conversation_id=conversation_id,
                    ),
                    "chat.answer",
                    status_str,
                    {
                        "requested_mode": requested_mode.value,
                        "resolved_mode": resolved_mode.value,
                        "retrieved_count": answer.retrieved_count,
                        "citation_valid": answer.citation_valid,
                        "repair_attempts": repair_attempts,
                        "issue_count": len(raw_issues),
                    },
                    severity="warning" if status_str == "citation_rejected" else "info",
                )
            except Exception:
                logger.exception("Chat audit write failed for user=%s", owner_id)

        return {
            "type": "chat.done",
            "mode": resolved_mode.value,
            "requested_mode": requested_mode.value,
            "text": text,
            "status": status_str,
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
            "diagnostics": diagnostics,
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
                decision = await self._auto_router.route(
                    request.text, owner_id=user_id
                )
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
