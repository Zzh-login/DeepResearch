"""知识库仓储抽象接口 —— 只定义"能做什么"，不写 SQL。"""

from abc import ABC, abstractmethod
from typing import Optional
from uuid import UUID

from domain.knowledge.models import DocumentStatus, KnowledgeChunk


class KnowledgeRepository(ABC):
    """知识库数据访问契约。

    infrastructure 层实现具体存储（Postgres / 内存 / 其他），
    domain 层只依赖这个接口，不关心底层是什么数据库。
    """

    # ═══════════════════════════════════════════
    # 知识库（knowledge_bases 表）
    # ═══════════════════════════════════════════

    @abstractmethod
    async def create_knowledge_base(
        self, name: str, description: str = ""
    ) -> UUID:
        """新建一个知识库，返回它的 ID。"""
        ...

    @abstractmethod
    async def list_knowledge_bases(self) -> list[dict]:
        """列出某个用户的所有知识库。"""
        ...

    @abstractmethod
    async def get_knowledge_base(self, kb_id: UUID) -> Optional[dict]:
        """查询当前 owner 的单个知识库；不存在或不属于该用户时返回 None。"""
        ...
        
    @abstractmethod
    async def delete_knowledge_base(self, kb_id: UUID) -> bool:
        """删除知识库（级联删文档和切片）。返回是否成功。"""
        ...

    # ═══════════════════════════════════════════
    # 文档（knowledge_documents 表）
    # ═══════════════════════════════════════════

    @abstractmethod
    async def create_document(
        self,
        kb_id: UUID,
        owner_id: str,
        filename: str,
        mime_type: str,
        storage_path: str,
        content_hash: str,
        file_size: int,
    ) -> UUID:
        """记录一个新上传的文档，返回文档 ID。"""
        ...

    @abstractmethod
    async def update_document_status(
        self,
        doc_id: UUID,
        status: DocumentStatus,
        error_message: Optional[str] = None,
    ) -> None:
        """更新文档处理状态（uploaded → parsing → indexing → ready/failed）。"""
        ...

    @abstractmethod
    async def get_document(self, doc_id: UUID) -> Optional[dict]:
        """查单个文档信息。"""
        ...

    @abstractmethod
    async def list_documents(self, kb_id: UUID) -> list[dict]:
        """列出某个知识库下的所有文档。"""
        ...

    @abstractmethod
    async def delete_document(self, doc_id: UUID) -> bool:
        """删除文档及其关联切片。返回是否成功。"""
        ...

    # ═══════════════════════════════════════════
    # 切片（knowledge_chunks 表）—— 核心！
    # ═══════════════════════════════════════════

    @abstractmethod
    async def save_chunks(
        self,
        kb_id: UUID,
        doc_id: UUID,
        chunks: list[KnowledgeChunk],
        embeddings: list[list[float]],
    ) -> None:
        """批量保存文档切片 + 对应的向量（一条 INSERT 对应一个 chunk + embedding）。"""
        ...

    @abstractmethod
    async def search_chunks(
        self,
        kb_id: UUID,
        embedding: list[float],
        top_k: int = 5,
    ) -> list[dict]:
        """向量语义检索：拿一个 embedding 去 knowledge_chunks 里找最相似的 top_k 条。"""
        ...

    @abstractmethod
    async def delete_chunks(self, doc_id: UUID) -> None:
        """删除某个文档的所有切片。"""
        ...

    # ═══════════════════════════════════════════
    # ingestion 任务（ingestion_tasks 表）
    # ═══════════════════════════════════════════

    @abstractmethod
    async def create_ingestion_task(self, doc_id: UUID) -> UUID:
        """为文档创建一个 ingestion 任务，返回任务 ID。"""
        ...

    @abstractmethod
    async def update_ingestion_task(
        self,
        task_id: UUID,
        status: str,
        error_message: Optional[str] = None,
        attempts: Optional[int] = None,
    ) -> None:
        """更新 ingestion 任务状态（pending → running → done/failed）。"""
        ...
