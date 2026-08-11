import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from infrastructure.embedding.bge_m3 import embed_query
from infrastructure.knowledge.loaders.text import TextLoader
from infrastructure.knowledge.text_splitter import KnowledgeTextSplitter


class TextLoaderTests(unittest.IsolatedAsyncioTestCase):
    async def _load(self, content: str):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "document.txt"
            path.write_text(content, encoding="utf-8")
            return await TextLoader().load(str(path))

    async def test_markdown_headings_create_sections_with_breadcrumbs(self):
        sections = await self._load(
            "# 第一章\n简介\n\n## 1.1 定义\n定义正文\n\n"
            "## 1.2 数据集划分\n训练集用于学习参数。\n"
        )

        self.assertEqual(len(sections), 3)
        self.assertEqual(sections[0].section_title, "第一章")
        self.assertEqual(sections[1].section_title, "第一章 > 1.1 定义")
        self.assertEqual(sections[2].section_title, "第一章 > 1.2 数据集划分")
        self.assertIn("章节：第一章 > 1.2 数据集划分", sections[2].text)
        self.assertNotIn("# 第一章", sections[2].text)
        self.assertEqual(
            sections[2].metadata["heading_path"],
            ["第一章", "1.2 数据集划分"],
        )

    async def test_plain_text_remains_one_section(self):
        sections = await self._load("普通文本第一行\n普通文本第二行")

        self.assertEqual(len(sections), 1)
        self.assertIsNone(sections[0].section_title)
        self.assertEqual(sections[0].text, "普通文本第一行\n普通文本第二行")

    async def test_heading_inside_code_fence_does_not_split_section(self):
        sections = await self._load(
            "# 文档\n```markdown\n# 代码中的标题\n```\n正文"
        )

        self.assertEqual(len(sections), 1)
        self.assertIn("# 代码中的标题", sections[0].text)

    async def test_heading_without_body_does_not_create_noise_section(self):
        sections = await self._load(
            "# 第一章\n\n## 1.1 定义\n定义正文"
        )

        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].section_title, "第一章 > 1.1 定义")

    async def test_numbered_list_items_remain_atomic(self):
        sections = await self._load(
            "# 第一章\n## 1.2 核心术语\n"
            "1. 样本\n" + "样本说明。" * 100 + "\n"
            "2. 数据集划分\n训练集用于学习参数。\n"
            "验证集用于调参。\n测试集用于最终评估。"
        )

        self.assertEqual(len(sections), 2)
        target = sections[1]
        self.assertIn("2. 数据集划分", target.section_title)
        self.assertIn("训练集用于学习参数", target.text)
        self.assertIn("验证集用于调参", target.text)
        self.assertIn("测试集用于最终评估", target.text)
        self.assertEqual(target.metadata["list_item"], "2. 数据集划分")


class TextSplitterTests(unittest.TestCase):
    def test_default_chunk_size_and_metadata_are_preserved(self):
        sections = TextLoader._parse_sections(
            "# 第一章\n\n## 1.1 很长的章节\n" + "测试内容。" * 200
        )
        splitter = KnowledgeTextSplitter()
        chunks = splitter.split(sections)

        self.assertEqual(splitter.chunk_size, 400)
        self.assertEqual(splitter.chunk_overlap, 60)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk.content) <= 400 for chunk in chunks))
        self.assertTrue(
            all(
                chunk.section_title == "第一章 > 1.1 很长的章节"
                for chunk in chunks
            )
        )
        self.assertEqual(
            [chunk.chunk_index for chunk in chunks],
            list(range(len(chunks))),
        )

    def test_invalid_chunk_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            KnowledgeTextSplitter(chunk_size=0, chunk_overlap=0)
        with self.assertRaises(ValueError):
            KnowledgeTextSplitter(chunk_size=100, chunk_overlap=100)


class FakeEmbeddingModel:
    def __init__(self) -> None:
        self.encoded_text = None

    def encode(self, text, normalize_embeddings):
        self.encoded_text = text
        return [0.1, 0.2]


class EmbeddingTests(unittest.IsolatedAsyncioTestCase):
    async def test_bge_m3_query_is_encoded_without_legacy_prefix(self):
        model = FakeEmbeddingModel()
        with patch(
            "infrastructure.embedding.bge_m3._load_bge_m3",
            return_value=model,
        ):
            embedding = await embed_query("训练集有什么作用？")

        self.assertEqual(model.encoded_text, "训练集有什么作用？")
        self.assertEqual(embedding, [0.1, 0.2])


if __name__ == "__main__":
    unittest.main()
