from __future__ import annotations

from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage

from app.rag.graph import RagGraph
from app.rag.hybrid_graph import HybridGraph
from domain.chat.modes import ChatMode
from domain.model_gateway.contracts import ModelProfile, ModelRequestContext
from infrastructure.conversation.pg_repository import PgConversationRepository
from infrastructure.database.postgres import PostgresDatabase
from infrastructure.knowledge.pg_repository import PgKnowledgeRepository
from infrastructure.model_gateway import ModelGateway


async def answer_case(
    settings,
    query: str,
    mode: ChatMode,
    owner_id: str,
    knowledge_base_id: str | None,
    conversation_id: str | None,
) -> dict:
    database = PostgresDatabase(settings.pg_dsn)
    await database.connect()
    try:
        gateway = ModelGateway(database, settings)
        conversation_uuid = UUID(conversation_id) if conversation_id else None
        if mode in {ChatMode.KNOWLEDGE, ChatMode.HYBRID}:
            if knowledge_base_id is None:
                raise ValueError("knowledge/hybrid 评测必须传 --knowledge-base-id")
            kb_uuid = UUID(knowledge_base_id)
            repo = PgKnowledgeRepository(database, owner_id)
            if await repo.get_knowledge_base(kb_uuid) is None:
                raise ValueError("知识库不存在或不属于 --owner-id")
            context_messages = []
            if conversation_uuid is not None:
                context_messages = await PgConversationRepository(
                    database, owner_id
                ).list_context_messages(conversation_uuid, limit=10)
            if mode == ChatMode.KNOWLEDGE:
                result = await RagGraph(settings, model_gateway=gateway).answer(
                    repo=repo,
                    knowledge_base_id=kb_uuid,
                    query=query,
                    top_k=settings.rag_top_k,
                    conversation_context=context_messages,
                    owner_id=owner_id,
                    conversation_id=conversation_uuid,
                )
            else:
                result = await HybridGraph(settings, model_gateway=gateway).answer(
                    repo=repo,
                    knowledge_base_id=kb_uuid,
                    query=query,
                    top_k=settings.rag_top_k,
                    conversation_context=context_messages,
                    owner_id=owner_id,
                    conversation_id=conversation_uuid,
                )
            return {
                "text": result.answer,
                "citations": [item.source_id for item in result.citations],
            }

        result = await gateway.complete(
            [
                SystemMessage(content="使用中文直接回答，不要输出知识库引用。"),
                HumanMessage(content=query),
            ],
            ModelProfile(
                operation="eval_normal_chat",
                temperature=0.1,
                max_tokens=2048,
                timeout_seconds=settings.model_gateway_timeout_seconds,
            ),
            ModelRequestContext(
                owner_id=owner_id,
                mode="normal",
                operation="eval_normal_chat",
                conversation_id=conversation_uuid,
            ),
        )
        return {"text": result.text, "citations": []}
    finally:
        await database.close()
