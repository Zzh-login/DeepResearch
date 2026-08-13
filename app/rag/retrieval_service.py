from uuid import UUID

from domain.knowledge.repository import KnowledgeRepository
from domain.rag.models import RetrievedSource
from infrastructure.embedding.bge_m3 import embed_query


class RetrievalService:
    def __init__(self, min_score: float = 0.45) -> None:
        self._min_score = min_score

    async def retrieve(
        self,
        repo: KnowledgeRepository,
        knowledge_base_id: UUID,
        query: str,
        top_k: int,
    ) -> list[RetrievedSource]:
        clean_query = query.strip()
        if not clean_query:
            raise ValueError("查询内容不能为空")
        if not 1 <= top_k <= 10:
            raise ValueError("top_k 必须在 1 到 10 之间")

        query_embedding = await embed_query(clean_query)
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