"""知识库文本分块器。

将 ParsedSection 列表独立切分为 KnowledgeChunk 列表，
每个 section 内部独立切分，禁止跨 section 拼接。
"""

from langchain_text_splitters import RecursiveCharacterTextSplitter

from domain.knowledge.models import KnowledgeChunk, ParsedSection


class KnowledgeTextSplitter:
    """文本分块器：将 ParsedSection 列表切分为 KnowledgeChunk 列表。

    关键约束：每个 section 独立切分，禁止跨 section 拼接。
    这确保 PDF 不同页面的内容不会混入同一个 chunk。
    """

    def __init__(self, chunk_size: int = 400, chunk_overlap: int = 60) -> None:
        """初始化分块器。

        Args:
            chunk_size: 每个 chunk 的最大字符数，默认 400。
            chunk_overlap: 相邻 chunk 之间的重叠字符数，默认 60。
        """
        if chunk_size < 1:
            raise ValueError("chunk_size 必须大于 0")
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap 必须大于等于 0 且小于 chunk_size")

        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=[
                "\n# ",
                "\n## ",
                "\n\n",
                "\n",
                "。",
                "！",
                "？",
                "；",
                "",
            ],
            length_function=len,
        )

    @property
    def chunk_size(self) -> int:
        """返回配置的 chunk_size。"""
        return self._chunk_size

    @property
    def chunk_overlap(self) -> int:
        """返回配置的 chunk_overlap。"""
        return self._chunk_overlap

    def split(self, sections: list[ParsedSection]) -> list[KnowledgeChunk]:
        """将 ParsedSection 列表切分为 KnowledgeChunk 列表。

        每个 section 独立切分，chunk_index 全局连续递增。
        section 的元数据通过浅拷贝带入每个 chunk。

        Args:
            sections: 待分块的 ParsedSection 列表。

        Returns:
            切分后的 KnowledgeChunk 列表。
        """
        chunks: list[KnowledgeChunk] = []
        chunk_index = 0

        for section in sections:
            # 每个 section 独立切分，section 元数据原样带入
            texts = self._splitter.split_text(section.text)
            for text in texts:
                text = text.strip()
                if not text:
                    continue
                chunks.append(
                    KnowledgeChunk(
                        content=text,
                        chunk_index=chunk_index,
                        page_number=section.page_number,
                        section_title=section.section_title,
                        metadata=dict(section.metadata),
                    )
                )
                chunk_index += 1

        return chunks
