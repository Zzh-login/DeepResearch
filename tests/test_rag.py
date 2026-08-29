import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from langchain_core.messages import AIMessage

from app.rag.citations import (
    INSUFFICIENT_CONTEXT_ANSWER,
    build_citations,
    prepare_rag_context,
)
from app.rag.graph import RagGraph
from domain.rag.models import RagAnswerStatus, RetrievedSource
from infrastructure.config.settings import get_settings


def make_row(score: float = 0.88) -> dict:
    return {
        "chunk_id": uuid4(),
        "document_id": uuid4(),
        "chunk_index": 0,
        "filename": "机器学习基础.pdf",
        "content": "监督学习使用带标签的数据训练模型。",
        "page_number": 12,
        "section_title": "监督学习",
        "score": score,
    }


class FakeRepo:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    async def search_chunks(self, kb_id, embedding, top_k):
        return self._rows[:top_k]


class SequenceModel:
    def __init__(self, answers: list[str]) -> None:
        self._answers = answers
        self.calls = 0

    async def ainvoke(self, messages):
        answer = self._answers[self.calls]
        self.calls += 1
        return AIMessage(content=answer)


class CitationTests(unittest.TestCase):
    def setUp(self):
        self.source = RetrievedSource(
            source_id="S1",
            chunk_id=uuid4(),
            document_id=uuid4(),
            chunk_index=0,
            filename="资料.pdf",
            content="证据正文",
            score=0.8,
            page_number=3,
            section_title="章节",
        )

    def test_context_contains_only_included_sources(self):
        context, included = prepare_rag_context([self.source], 1000, 500)
        self.assertIn("[S1]", context)
        self.assertEqual(included, [self.source])

    def test_truncated_context_and_citation_use_same_evidence(self):
        long_source = RetrievedSource(
            source_id="S1",
            chunk_id=uuid4(),
            document_id=uuid4(),
            chunk_index=0,
            filename="长文档.txt",
            content="甲" * 1000,
            score=0.9,
        )
        context, included = prepare_rag_context([long_source], 500, 100)
        self.assertEqual(len(included), 1)
        self.assertEqual(included[0].content, "甲" * 100)
        citations, valid = build_citations("结论。[S1]", included)
        self.assertTrue(valid)
        self.assertEqual(citations[0].excerpt, "甲" * 100)
        self.assertIn("甲" * 100, context)

    def test_valid_citation(self):
        citations, valid = build_citations("结论。[S1]", [self.source])
        self.assertTrue(valid)
        self.assertEqual(len(citations), 1)

    def test_missing_citation_is_invalid(self):
        citations, valid = build_citations("没有引用的结论。", [self.source])
        self.assertFalse(valid)
        self.assertEqual(citations, [])

    def test_fake_citation_is_invalid(self):
        citations, valid = build_citations("结论。[S99]", [self.source])
        self.assertFalse(valid)
        self.assertEqual(citations, [])

    def test_malformed_citation_is_invalid(self):
        citations, valid = build_citations("部分正确。[S1] 但还有伪造来源。[Sabc]", [self.source])
        self.assertFalse(valid)
        self.assertEqual(len(citations), 1)

    def test_insufficient_context_clears_model_added_citations(self):
        citations, valid = build_citations(
            "根据当前知识库资料无法确定。知识库证据中未包含量子纠缠的解释内容[S1][S2]。",
            [self.source],
        )
        self.assertTrue(valid)
        self.assertEqual(citations, [])


class RagGraphTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _fake_gateway():
        gateway = AsyncMock()
        gateway.embed = AsyncMock(
            return_value=SimpleNamespace(vectors=[[1.0] + [0.0] * 1023])
        )
        return gateway

    async def _answer(self, rows: list[dict], model: SequenceModel):
        graph = RagGraph(
            get_settings(),
            model=model,
            model_gateway=self._fake_gateway(),
        )
        return await graph.answer(
            repo=FakeRepo(rows),
            knowledge_base_id=uuid4(),
            query="什么是监督学习？",
            top_k=5,
        )

    async def test_grounded_answer(self):
        model = SequenceModel(["监督学习使用带标签的数据。[S1]"])
        result = await self._answer([make_row()], model)
        self.assertEqual(result.status, RagAnswerStatus.GROUNDED)
        self.assertTrue(result.citation_valid)
        self.assertEqual(len(result.citations), 1)
        self.assertEqual(model.calls, 1)

    async def test_knowledge_retrieval_uses_explicit_gateway_context(self):
        gateway = self._fake_gateway()
        graph = RagGraph(
            get_settings(),
            model=SequenceModel([]),
            model_gateway=gateway,
        )
        await graph._retrieve(
            {
                "repo": FakeRepo([]),
                "knowledge_base_id": uuid4(),
                "query": "问题",
                "top_k": 5,
                "owner_id": "owner-a",
            }
        )
        context = gateway.embed.await_args.kwargs["context"]
        self.assertEqual(context.mode, "knowledge")
        self.assertEqual(context.operation, "knowledge_query_embedding")

    async def test_no_context_does_not_call_model(self):
        model = SequenceModel([])
        result = await self._answer([], model)
        self.assertEqual(result.status, RagAnswerStatus.INSUFFICIENT_CONTEXT)
        self.assertEqual(model.calls, 0)

    async def test_low_score_does_not_call_model(self):
        model = SequenceModel([])
        result = await self._answer([make_row(score=0.36)], model)
        self.assertEqual(result.status, RagAnswerStatus.INSUFFICIENT_CONTEXT)
        self.assertEqual(model.calls, 0)

    async def test_invalid_citation_is_repaired_once(self):
        model = SequenceModel([
            "错误编号。[S99]",
            "监督学习使用带标签的数据。[S1]",
        ])
        result = await self._answer([make_row()], model)
        self.assertEqual(result.status, RagAnswerStatus.GROUNDED)
        self.assertTrue(result.citation_valid)
        self.assertEqual(model.calls, 2)

    async def test_second_invalid_citation_is_rejected(self):
        model = SequenceModel(["错误。[S99]", "仍然错误。[S88]"])
        result = await self._answer([make_row()], model)
        self.assertEqual(result.status, RagAnswerStatus.CITATION_REJECTED)
        self.assertFalse(result.citation_valid)
        self.assertEqual(result.citations, [])
        self.assertEqual(model.calls, 2)

    async def test_insufficient_context_is_normalized_without_citations(self):
        model = SequenceModel([
            "根据当前知识库资料无法确定。知识库证据中未包含量子纠缠的解释内容[S1]。",
        ])
        result = await self._answer([make_row()], model)
        self.assertEqual(result.status, RagAnswerStatus.INSUFFICIENT_CONTEXT)
        self.assertEqual(result.answer, INSUFFICIENT_CONTEXT_ANSWER)
        self.assertTrue(result.citation_valid)
        self.assertEqual(result.citations, [])


if __name__ == "__main__":
    unittest.main()
