from domain.knowledge.models import ParsedSection


class DocumentLoader:
    """文档解析器抽象基类。"""

    async def load(self, path: str) -> list[ParsedSection]:
        raise NotImplementedError
