from pathlib import Path

from docx import Document

from infrastructure.knowledge.loaders.base import DocumentLoader
from domain.knowledge.models import ParsedSection


class DocxLoader(DocumentLoader):
    """DOCX 解析器：按标题/正文分块。"""

    async def load(self, path: str) -> list[ParsedSection]:
        doc = Document(str(path))
        sections: list[ParsedSection] = []
        buf: list[str] = []
        current_title: str | None = None

        def _flush() -> None:
            text = "\n".join(buf).strip()
            if text:
                sections.append(ParsedSection(text=text, section_title=current_title))
            buf.clear()

        for para in doc.paragraphs:
            style_name = para.style.name if para.style else ""
            is_heading = style_name.startswith("Heading") if style_name else False
            text = para.text.strip()

            if not text:
                continue

            if is_heading:
                _flush()
                current_title = text
                continue

            buf.append(text)

        _flush()
        if not sections:
            raise ValueError("Word 文档未提取到任何文本内容")
        return sections
