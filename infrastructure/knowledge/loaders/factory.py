from pathlib import Path

from infrastructure.knowledge.loaders.base import DocumentLoader
from infrastructure.knowledge.loaders.text import TextLoader
from infrastructure.knowledge.loaders.pdf import PdfLoader
from infrastructure.knowledge.loaders.docx import DocxLoader

_LOADER_MAP: dict[str, type[DocumentLoader]] = {
    ".txt": TextLoader,
    ".md": TextLoader,
    ".markdown": TextLoader,
    ".pdf": PdfLoader,
    ".docx": DocxLoader,
}


def get_loader(file_path: str | Path) -> DocumentLoader:
    suffix = Path(file_path).suffix.lower()
    loader_cls = _LOADER_MAP.get(suffix)
    if loader_cls is None:
        raise ValueError(f"不支持的文件格式：{suffix}")
    return loader_cls()
