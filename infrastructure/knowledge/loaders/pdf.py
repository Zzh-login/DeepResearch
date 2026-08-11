from pathlib import Path

from pypdf import PdfReader

from infrastructure.knowledge.loaders.base import DocumentLoader
from domain.knowledge.models import ParsedSection


class PdfLoader(DocumentLoader):
    """PDF 解析器：每页一个 ParsedSection。"""

    async def load(self, path: str) -> list[ParsedSection]:
        reader = PdfReader(str(path))
        sections: list[ParsedSection] = []
        for i, page in enumerate(reader.pages, start=1):
            text = page.extract_text()
            if text and text.strip():
                sections.append(ParsedSection(text=text.strip(), page_number=i))
        if not sections:
            raise ValueError("扫描 PDF 暂不支持：所有页面均无法提取文本")
        return sections
