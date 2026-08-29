import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from langchain_core.messages import AIMessage

from app.rag.hybrid_graph import HybridGraph
from domain.rag.hybrid_models import HybridAnswerStatus
from infrastructure.config.settings import get_settings


class FakeRepo:
    def __init__(self, rows):
        self.rows = rows

    async def search_chunks(self, kb_id, embedding, top_k):
        return self.rows[:top_k]


class SequenceModel:
    def __init__(self, answers):
        self.answers = answers
        self.calls = 0

    async def ainvoke(self, messages):
        answer = self.answers[self.calls]
        self.calls += 1
        return AIMessage(content=answer)


def make_row():
    return {
        "chunk_id": uuid4(),
        "document_id": uuid4(),
        "chunk_index": 0,
        "filename": "机器学习.txt",
        "content": "训练集用于拟合模型参数。",
        "page_number": None,
        "section_title": "数据集",
        "score": 0.88,
    }


class HybridGraphTests(unittest.IsolatedAsyncioTestCase):
    async def _answer(self, rows, answers):
        model = SequenceModel(answers)
        gateway = AsyncMock()
        gateway.embed = AsyncMock(
            return_value=SimpleNamespace(vectors=[[0.0] * 1024])
        )
        graph = HybridGraph(
            get_settings(),
            model=model,
            model_gateway=gateway,
        )
        result = await graph.answer(
            repo=FakeRepo(rows),
            knowledge_base_id=uuid4(),
            query="训练集有什么作用，并给出实践建议",
            top_k=5,
        )
        return result, model

    async def test_blended_answer_with_valid_citation(self):
        answer = (
            "## 知识库结论\n"
            "训练集用于拟合模型参数。[S1]\n\n"
            "## 模型补充\n"
            "[模型补充] 应注意训练数据代表性。"
        )
        result, model = await self._answer([make_row()], [answer])
        self.assertEqual(result.status, HybridAnswerStatus.BLENDED)
        self.assertTrue(result.citation_valid)
        self.assertEqual(len(result.citations), 1)
        self.assertEqual(model.calls, 1)

    async def test_no_evidence_returns_labeled_model_only(self):
        answer = (
            "## 知识库结论\n"
            "未检索到可靠的知识库证据\n\n"
            "## 模型补充\n"
            "[模型补充] 一般而言训练集用于参数拟合。"
        )
        result, model = await self._answer([], [answer])
        self.assertEqual(result.status, HybridAnswerStatus.MODEL_ONLY)
        self.assertTrue(result.citation_valid)
        self.assertEqual(result.citations, [])
        self.assertEqual(model.calls, 1)

    async def test_invalid_answer_is_repaired_once(self):
        bad = "没有分区，也没有引用。"
        repaired = (
            "## 知识库结论\n训练集用于拟合参数。[S1]\n\n"
            "## 模型补充\n[模型补充] 注意样本代表性。"
        )
        result, model = await self._answer([make_row()], [bad, repaired])
        self.assertTrue(result.citation_valid)
        self.assertEqual(model.calls, 2)

    async def test_second_invalid_answer_is_rejected(self):
        result, model = await self._answer(
            [make_row()],
            ["错误回答", "仍然错误"],
        )
        self.assertEqual(
            result.status,
            HybridAnswerStatus.CITATION_REJECTED,
        )
        self.assertFalse(result.citation_valid)
        self.assertEqual(result.citations, [])
        self.assertEqual(model.calls, 2)

    async def test_citation_in_model_supplement_is_rejected(self):
        bad = (
            "## 知识库结论\n训练集用于拟合参数。[S1]\n\n"
            "## 模型补充\n[模型补充] 这条通用建议伪造引用。[S1]"
        )
        result, model = await self._answer(
            [make_row()],
            [bad, bad],
        )
        self.assertEqual(
            result.status,
            HybridAnswerStatus.CITATION_REJECTED,
        )
        self.assertFalse(result.citation_valid)
        self.assertEqual(model.calls, 2)

    async def test_rejection_exposes_structured_diagnostics(self):
        result, model = await self._answer(
            [make_row()],
            ["错误回答", "仍然错误"],
        )
        self.assertEqual(
            result.status,
            HybridAnswerStatus.CITATION_REJECTED,
        )
        self.assertIsInstance(result.validation_issues, list)
        self.assertGreater(len(result.validation_issues), 0)
        codes = {item.get("code") for item in result.validation_issues}
        self.assertIn("missing_sections", codes)
        self.assertIn("empty_model_supplement", codes)
        self.assertIn("问题：", result.answer)
        self.assertEqual(result.repair_attempts, 1)
        self.assertEqual(result.max_repair_attempts, 1)

    async def test_valid_answer_has_no_issues(self):
        answer = (
            "## 知识库结论\n"
            "训练集用于拟合模型参数。[S1]\n\n"
            "## 模型补充\n"
            "[模型补充] 应注意训练数据代表性。"
        )
        result, model = await self._answer([make_row()], [answer])
        self.assertEqual(result.status, HybridAnswerStatus.BLENDED)
        self.assertEqual(result.validation_issues, [])

    async def test_model_supplement_accepts_bulleted_lines(self):
        answer = (
            "## 知识库结论\n"
            "训练集用于拟合模型参数。[S1]\n\n"
            "## 模型补充\n"
            "- [模型补充] Cross-Encoder 通常更重视排序精度。\n"
            "- [模型补充] BGE Reranker 可以在延迟和效果之间进行权衡。"
        )
        result, model = await self._answer([make_row()], [answer])
        self.assertTrue(result.citation_valid)
        self.assertEqual(result.status, HybridAnswerStatus.BLENDED)
        self.assertEqual(model.calls, 1)

    async def test_model_supplement_accepts_numbered_lines(self):
        answer = (
            "## 知识库结论\n"
            "训练集用于拟合模型参数。[S1]\n\n"
            "## 模型补充\n"
            "1. [模型补充] Cross-Encoder 通常更重视排序精度。\n"
            "2) [模型补充] BGE Reranker 可以在延迟和效果之间进行权衡。"
        )
        result, model = await self._answer([make_row()], [answer])
        self.assertTrue(result.citation_valid)
        self.assertEqual(result.status, HybridAnswerStatus.BLENDED)
        self.assertEqual(model.calls, 1)


if __name__ == "__main__":
    unittest.main()
