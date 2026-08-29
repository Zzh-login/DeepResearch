"""IngestionService — 文档入库编排服务。

将文件解析、分块、向量化、入库四个步骤串成一个完整的 ingestion pipeline。
"""

import logging
from uuid import UUID

from domain.knowledge.models import DocumentStatus
from infrastructure.knowledge.pg_repository import PgKnowledgeRepository
from infrastructure.knowledge.text_splitter import KnowledgeTextSplitter
from infrastructure.knowledge.loaders.factory import get_loader
from domain.model_gateway.contracts import ModelRequestContext

logger = logging.getLogger(__name__)


class IngestionService:
    """文档入库编排服务。

    编排流程：
        查文档 → 解析（状态：parsing）→ 分块 → 向量化（状态：indexing）→ 入库 → 状态：ready。

    任意步骤失败时，状态置为 failed 并 re-raise 异常。
    """

    def __init__(self, repo: PgKnowledgeRepository, splitter: KnowledgeTextSplitter,model_gateway=None) -> None:
        """构造注入。

        Args:
            repo: PostgreSQL 知识库仓储实例。
            splitter: 文本分块器实例（已配置好 chunk_size/chunk_overlap）。
        """
        self._repo = repo
        self._splitter = splitter
        self._model_gateway = model_gateway

    async def ingest_document(
        self,
        document_id: UUID,
        task_id: UUID,
    ) -> None:
        """完整入库流程。

        异常时同时更新文档状态和任务状态为 failed，
        两个更新各自独立 try/except，互不阻塞。
        """
        try:
            # ── 任务开始 ──
            await self._repo.update_ingestion_task(task_id, "running")

            # ── 1. 查询文档记录 ──
            doc = await self._repo.get_document(document_id)
            if doc is None:
                raise ValueError(f"文档不存在: {document_id}")

            # ── 2. 状态 → parsing ──
            await self._repo.update_document_status(document_id, DocumentStatus.PARSING)

            # ── 3. 选择 loader 并解析文件 ──
            loader = get_loader(doc["storage_path"])
            sections = await loader.load(doc["storage_path"])

            # ── 4. 分块 ──
            chunks = self._splitter.split(sections)

            # ── 空文档 → 抛异常 ──
            if not chunks:
                raise ValueError("文档未提取到有效文本")

            # ── 5. 状态 → indexing ──
            await self._repo.update_document_status(document_id, DocumentStatus.INDEXING)

            # ── 6. 批量向量化 ──
            if self._model_gateway is None:
                raise RuntimeError("生产 IngestionService 必须注入 ModelGateway")
            embedding_result = await self._model_gateway.embed(
                [chunk.content for chunk in chunks],
                is_query=False,
                context=ModelRequestContext(
                    owner_id=str(document["owner_id"]),
                    mode="ingestion",
                    operation="knowledge_document_embedding",
                ),
            )
            embeddings = embedding_result.vectors

            # ── 7. 事务替换旧 chunks ──
            await self._repo.replace_chunks(
                doc["knowledge_base_id"],
                document_id,
                chunks,
                embeddings,
            )

            # ── 8. 文档 ready + 任务 done（同一事务）──
            await self._repo.complete_ingestion(document_id, task_id)

        except Exception as exc:
            logger.exception("文档 %s 入库失败", document_id)

            try:
                await self._repo.fail_ingestion(
                    document_id,
                    task_id,
                    str(exc),
                )
            except Exception:
                logger.exception("更新文档与任务失败状态时出错")

            raise
