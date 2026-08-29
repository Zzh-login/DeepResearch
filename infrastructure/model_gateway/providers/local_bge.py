import math

from infrastructure.embedding.bge_m3 import embed_documents, embed_query


def _cosine(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


class LocalBgeProvider:
    name = "local"
    embedding_model = "BAAI/bge-m3"
    rerank_model = "BAAI/bge-m3-cosine"

    async def embed(self, texts: list[str], is_query: bool) -> list[list[float]]:
        if not texts:
            return []
        if is_query:
            if len(texts) != 1:
                raise ValueError("查询向量化一次只能传入一个文本")
            return [await embed_query(texts[0])]
        return await embed_documents(texts)

    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_k: int,
    ) -> list[tuple[int, float]]:
        if not query.strip():
            raise ValueError("重排查询不能为空")
        if not documents:
            return []
        query_vector = (await self.embed([query], is_query=True))[0]
        document_vectors = await self.embed(documents, is_query=False)
        scored = [
            (index, _cosine(query_vector, vector))
            for index, vector in enumerate(document_vectors)
        ]
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[: max(1, min(top_k, len(scored)))]