import logging
from collections.abc import AsyncIterator
from typing import Any

import asyncpg

from app.rag.graph import RagGraph, RagModelError, RagRetrievalError
from app.session.session import ChatSession
from domain.chat.modes import ChatMode, is_chat_mode_enabled
from infrastructure.config.settings import Settings
from infrastructure.database.postgres import PostgresDatabase
from infrastructure.knowledge.pg_repository import PgKnowledgeRepository
from interfaces.web.chat_schemas import ChatRequest


logger = logging.getLogger(__name__)


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
    ) -> None:
        self._database = database
        self._rag_graph = rag_graph
        self._settings = settings

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

        if request.mode == ChatMode.NORMAL:
            session.set_agent_mode(request.agent_mode)
            async for event in session.chat_stream(
                request.text,
                user_id=user_id,
            ):
                if event["type"] == "token":
                    yield {
                        "type": "chat.token",
                        "mode": ChatMode.NORMAL.value,
                        "text": event["text"],
                    }
                elif event["type"] == "done":
                    yield {
                        "type": "chat.done",
                        "mode": ChatMode.NORMAL.value,
                        "text": event["text"],
                        "error": bool(event.get("error")),
                        "citations": [],
                    }
            return

        if self._database.pool is None:
            raise ChatProtocolError(
                "database_unavailable",
                "知识库服务暂不可用，请稍后重试",
            )
        if self._rag_graph is None:
            raise ChatProtocolError(
                "rag_unavailable",
                "知识库问答模型暂不可用，请检查配置",
            )

        kb_id = request.knowledge_base_id
        if kb_id is None:
            raise ChatProtocolError(
                "knowledge_base_required",
                "仅知识库模式必须选择知识库",
            )

        repo = PgKnowledgeRepository(self._database, user_id)
        try:
            knowledge_base = await repo.get_knowledge_base(kb_id)
            if knowledge_base is None:
                raise ChatProtocolError(
                    "knowledge_base_not_found",
                    "知识库不存在或无权访问",
                )

            answer = await self._rag_graph.answer(
                repo=repo,
                knowledge_base_id=kb_id,
                query=request.text,
                top_k=self._settings.rag_top_k,
            )
        except ChatProtocolError:
            raise
        except RagModelError as exc:
            logger.exception("RAG model failed for user=%s", user_id)
            raise ChatProtocolError(
                "model_unavailable",
                "回答模型暂不可用，请稍后重试",
            ) from exc
        except (
            RagRetrievalError,
            ConnectionError,
            TimeoutError,
            asyncpg.PostgresConnectionError,
            asyncpg.CannotConnectNowError,
            asyncpg.TooManyConnectionsError,
        ) as exc:
            logger.exception("RAG retrieval failed for user=%s", user_id)
            raise ChatProtocolError(
                "database_unavailable",
                "知识库服务暂不可用，请稍后重试",
            ) from exc
        except Exception as exc:
            logger.exception("Knowledge chat failed for user=%s", user_id)
            raise ChatProtocolError(
                "internal_error",
                "知识库问答处理失败，请稍后重试",
            ) from exc

        yield {
            "type": "chat.done",
            "mode": ChatMode.KNOWLEDGE.value,
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
        }