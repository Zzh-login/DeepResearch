from uuid import UUID

from domain.knowledge.repository import KnowledgeRepository
from domain.rag.models import RetrievedSource
from domain.model_gateway.contracts import ModelRequestContext


class RetrievalService:
    def __init__(self, min_score: float = 0.45, model_gateway=None) -> None:
        self._min_score = min_score
        self._model_gateway = model_gateway
    async def retrieve(
        self,
        repo: KnowledgeRepository,
        knowledge_base_id: UUID,
        query: str,
        top_k: int,
        owner_id: str = "system",
        conversation_id: UUID | None = None,
        mode: str = "knowledge",
        operation: str = "knowledge_query_embedding",
    ) -> list[RetrievedSource]:
        clean_query = query.strip()
        if not clean_query:
            raise ValueError("查询内容不能为空")
        if not 1 <= top_k <= 10:
            raise ValueError("top_k 必须在 1 到 10 之间")
        if self._model_gateway is None:
            raise RuntimeError("生产 RetrievalService 必须注入 ModelGateway")
        embedding = await self._model_gateway.embed(
            [clean_query],
            is_query=True,
            context=ModelRequestContext(
                owner_id=owner_id,
                mode=mode,
                operation=operation,
                conversation_id=conversation_id,
            ),
        )
        query_embedding = embedding.vectors[0]
        rows = await repo.search_chunks(
            knowledge_base_id,
            query_embedding,
            top_k,
        )

        sources: list[RetrievedSource] = []
        for row in rows:
            score = float(row["score"])
            content = str(row["content"] or "").strip()
            if score < self._min_score or not content:
                continue

            sources.append(
                RetrievedSource(
                    source_id=f"S{len(sources) + 1}",
                    chunk_id=row["chunk_id"],
                    document_id=row["document_id"],
                    chunk_index=int(row["chunk_index"]),
                    filename=str(row["filename"]),
                    content=content,
                    score=score,
                    page_number=row.get("page_number"),
                    section_title=row.get("section_title"),
                )
            )

        return sources
