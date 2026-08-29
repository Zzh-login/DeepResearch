"""知识库 REST API — FastAPI APIRouter，7 个路由。

所有路由由 JWT 保护（get_current_user 依赖注入），
知识库操作强制归属校验（仅允许操作用户自己的知识库）。
"""
from app.rag.graph import RagModelError, RagRetrievalError
from interfaces.web.rag_schemas import (
    CitationResponse,
    RagAskRequest,
    RagAskResponse,
)

import hashlib
import logging
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import aiofiles

from fastapi import APIRouter, Request, Depends, UploadFile, File, BackgroundTasks, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from infrastructure.auth.dependency import get_current_user
from infrastructure.config.settings import get_settings
from infrastructure.knowledge.pg_repository import (
    KnowledgeBaseBusyError,
    PgKnowledgeRepository,
)
from infrastructure.knowledge.text_splitter import KnowledgeTextSplitter
from domain.model_gateway.contracts import ModelRequestContext
from app.knowledge.ingestion_service import IngestionService

# ── 文件上传配置 ──
ALLOWED_EXTENSIONS = {".txt", ".md", ".markdown", ".pdf", ".docx"}

# ── 模块级单例（无状态，线程安全共享） ──
_splitter = KnowledgeTextSplitter()
logger = logging.getLogger(__name__)


DATABASE_UNAVAILABLE_EXCEPTIONS = (
    ConnectionError,
    TimeoutError,
    asyncpg.PostgresConnectionError,
    asyncpg.CannotConnectNowError,
    asyncpg.TooManyConnectionsError,
)


class KnowledgeDatabaseRoute(APIRoute):
    """Map runtime PostgreSQL connectivity failures to a stable HTTP 503."""

    def get_route_handler(self):
        original_handler = super().get_route_handler()

        async def database_error_handler(request: Request):
            try:
                return await original_handler(request)
            except DATABASE_UNAVAILABLE_EXCEPTIONS as exc:
                request.app.state.database_status = "degraded"
                request.app.state.database_error = str(exc)
                logger.exception(
                    "PostgreSQL unavailable during %s %s",
                    request.method,
                    request.url.path,
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="知识库服务暂不可用，请稍后重试",
                ) from None

        return database_error_handler


async def _require_database(request: Request) -> None:
    """检查数据库连接池可用，不可用返回 503。"""
    db = getattr(request.app.state, "database", None)
    if db is None or db.pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="知识库服务暂不可用，请检查 PostgreSQL",
        )


def _parse_uuid(value: str, label: str) -> UUID:
    """安全解析 UUID，非法值返回 400 而不是 500。"""
    try:
        return UUID(value)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"无效的 {label} ID",
        ) from None


router = APIRouter(
    prefix="/api",
    dependencies=[Depends(_require_database)],
    route_class=KnowledgeDatabaseRoute,
)


def _get_repo(request: Request, owner_id: str) -> PgKnowledgeRepository:
    """创建当前用户的知识库仓储实例。

    Args:
        request: FastAPI Request，从中提取 app.state.database。
        owner_id: 当前认证用户的 user_id。

    Returns:
        限定到当前用户的 PgKnowledgeRepository 实例。
    """
    db = request.app.state.database
    return PgKnowledgeRepository(db, owner_id)


def _get_service(repo: PgKnowledgeRepository, request: Request) -> IngestionService:
    """创建入库服务实例。

    Args:
        repo: 已限定用户的 PgKnowledgeRepository 实例。
        request: FastAPI Request，从中提取共享的 model_gateway。

    Returns:
        配置好的 IngestionService 实例。
    """
    return IngestionService(
        repo,
        _splitter,
        request.app.state.services.model_gateway,
    )


async def _verify_kb_ownership(repo: PgKnowledgeRepository, kb_id: UUID) -> None:
    """验证知识库存在且属于当前用户。

    Args:
        repo: 当前用户的仓储实例。
        kb_id: 要验证的知识库 UUID。

    Raises:
        HTTPException 404: 知识库不存在或不属于当前用户。
    """
    kbs = await repo.list_knowledge_bases()
    kb_ids = {kb["id"] for kb in kbs}
    if kb_id not in kb_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="知识库不存在",
        )


def _cleanup_knowledge_base_files(
    storage_paths: list[str],
    knowledge_base_dir: Path,
) -> tuple[int, int]:
    """Delete stored originals without allowing paths outside the KB folder."""
    root = knowledge_base_dir.resolve()
    deleted_count = 0
    failed_count = 0

    for storage_path in storage_paths:
        path = Path(storage_path).resolve()
        if not path.is_relative_to(root):
            failed_count += 1
            logger.warning(
                "Skipped knowledge file outside expected directory: %s",
                path,
            )
            continue

        try:
            existed = path.exists()
            path.unlink(missing_ok=True)
            if existed:
                deleted_count += 1
        except OSError as exc:
            failed_count += 1
            logger.warning("Failed to delete knowledge file %s: %s", path, exc)

    try:
        root.rmdir()
    except (FileNotFoundError, OSError):
        pass

    return deleted_count, failed_count


# ═══════════════════════════════════════════════════════════════
# 1. POST /api/knowledge-bases — 创建知识库
# ═══════════════════════════════════════════════════════════════

@router.post("/knowledge-bases")
async def create_knowledge_base(
    request: Request,
    user_id: str = Depends(get_current_user),
):
    """创建新的知识库。

    请求体:
        {"name": str, "description": str}

    返回 201:
        {"id": str, "name": str, "description": str}
    """
    data = await request.json()
    name = data.get("name", "").strip()
    description = data.get("description", "").strip()

    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="知识库名称不能为空",
        )

    repo = _get_repo(request, user_id)
    try:
        kb_id = await repo.create_knowledge_base(name, description)
    except asyncpg.exceptions.UniqueViolationError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="同名知识库已存在",
        ) from None

    return JSONResponse(
        {"id": str(kb_id), "name": name, "description": description},
        status_code=status.HTTP_201_CREATED,
    )


# ═══════════════════════════════════════════════════════════════
# 2. GET /api/knowledge-bases — 列出知识库
# ═══════════════════════════════════════════════════════════════

@router.get("/knowledge-bases")
async def list_knowledge_bases(
    request: Request,
    user_id: str = Depends(get_current_user),
):
    """列出当前用户的所有知识库。

    返回:
        [{"id": str, "name": str, "description": str, "created_at": str, "updated_at": str}, ...]
    """
    repo = _get_repo(request, user_id)
    kbs = await repo.list_knowledge_bases()

    return [
        {
            "id": str(kb["id"]),
            "name": kb["name"],
            "description": kb.get("description", ""),
            "created_at": str(kb["created_at"]) if kb.get("created_at") else None,
            "updated_at": str(kb["updated_at"]) if kb.get("updated_at") else None,
        }
        for kb in kbs
    ]


@router.delete("/knowledge-bases/{kb_id}")
async def delete_knowledge_base(
    kb_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    """Delete an owned knowledge base, its DB records, and stored originals."""
    repo = _get_repo(request, user_id)
    kb_uuid = _parse_uuid(kb_id, "知识库")

    try:
        storage_paths = await repo.delete_knowledge_base_with_documents(kb_uuid)
    except KnowledgeBaseBusyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"知识库仍有文档正在处理（{exc}），请等待处理完成后再删除",
        ) from None

    if storage_paths is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="知识库不存在",
        )

    settings = get_settings()
    knowledge_base_dir = (
        Path(settings.knowledge_storage_path)
        / "originals"
        / user_id
        / str(kb_uuid)
    )
    deleted_files, cleanup_failed = _cleanup_knowledge_base_files(
        storage_paths,
        knowledge_base_dir,
    )

    return {
        "ok": True,
        "deleted_files": deleted_files,
        "cleanup_failed": cleanup_failed,
    }


# ═══════════════════════════════════════════════════════════════
# 3. POST /api/knowledge-bases/{kb_id}/documents — 上传文档
# ═══════════════════════════════════════════════════════════════

@router.post("/knowledge-bases/{kb_id}/documents")
async def upload_document(
    kb_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user),
):
    settings = get_settings()
    repo = _get_repo(request, user_id)

    # ── 校验 kb_id ──
    kb_uuid = _parse_uuid(kb_id, "知识库")

    # a) 检查扩展名
    filename = file.filename or "untitled"
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的文件类型: {suffix}。支持: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    # b) 文件大小限制在流式读取时执行，避免 multipart 边界造成误判
    max_bytes = settings.upload_max_size_mb * 1024 * 1024

    # c) 验证知识库归属
    await _verify_kb_ownership(repo, kb_uuid)

    # d) 创建存储目录（settings + user_id 隔离）
    originals_dir = (
        Path(settings.knowledge_storage_path)
        / "originals"
        / user_id
        / str(kb_uuid)
    ).resolve()
    originals_dir.mkdir(parents=True, exist_ok=True)

    # e) 流式保存文件 + SHA-256
    file_uuid = uuid4()
    storage_path = str((originals_dir / f"{file_uuid}{suffix}").resolve())

    sha256_hash = hashlib.sha256()
    file_size = 0

    try:
        async with aiofiles.open(storage_path, "wb") as output:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                file_size += len(chunk)
                if file_size > max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"文件过大，最大允许 {settings.upload_max_size_mb}MB",
                    )
                await output.write(chunk)
                sha256_hash.update(chunk)
    except Exception:
        Path(storage_path).unlink(missing_ok=True)
        raise

    content_hash = sha256_hash.hexdigest()
    mime_type = file.content_type or "application/octet-stream"

    # f) 事务创建文档+任务（重复文件 → 409）
    try:
        doc_id, task_id = await repo.create_document_with_task(
            kb_uuid, filename, mime_type, storage_path, content_hash, file_size
        )
    except Exception as exc:
        # 清理磁盘文件
        Path(storage_path).unlink(missing_ok=True)
        # UniqueViolationError → 409
        if isinstance(exc, asyncpg.exceptions.UniqueViolationError):
            raise HTTPException(
                status_code=409,
                detail="该文件已存在于当前知识库",
            )
        raise

    # g) 后台异步入库
    service = _get_service(repo, request)
    background_tasks.add_task(service.ingest_document, doc_id, task_id)

    # h) 立即返回（含 task_id）
    return JSONResponse(
        {"document_id": str(doc_id), "task_id": str(task_id), "status": "uploaded"},
        status_code=status.HTTP_201_CREATED,
    )


# ═══════════════════════════════════════════════════════════════
# 4. GET /api/knowledge-bases/{kb_id}/documents — 列出文档
# ═══════════════════════════════════════════════════════════════

@router.get("/knowledge-bases/{kb_id}/documents")
async def list_documents(
    kb_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    """列出指定知识库下的所有文档。

    返回:
        [{"id": str, "filename": str, "mime_type": str,
          "file_size": int, "status": str,
          "created_at": str, "updated_at": str}, ...]
    """
    repo = _get_repo(request, user_id)

    kb_uuid = _parse_uuid(kb_id, "知识库")
    await _verify_kb_ownership(repo, kb_uuid)
    docs = await repo.list_documents(kb_uuid)

    return [
        {
            "id": str(doc["id"]),
            "filename": doc["filename"],
            "mime_type": doc.get("mime_type", ""),
            "file_size": doc.get("file_size", 0),
            "status": doc.get("status", ""),
            "created_at": str(doc["created_at"]) if doc.get("created_at") else None,
            "updated_at": str(doc["updated_at"]) if doc.get("updated_at") else None,
        }
        for doc in docs
    ]


# ═══════════════════════════════════════════════════════════════
# 5. GET /api/documents/{doc_id} — 获取文档详情
# ═══════════════════════════════════════════════════════════════

@router.get("/documents/{doc_id}")
async def get_document_detail(
    doc_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    """获取文档详情。

    repo 在构造时已限定 owner_id，因此自动实现用户隔离：
    其他用户的文档直接返回 None → 404。

    返回:
        {"id": str, "knowledge_base_id": str, "filename": str, "mime_type": str,
         "file_size": int, "content_hash": str, "status": str, "error_message": str|null,
         "created_at": str, "updated_at": str}
    """
    repo = _get_repo(request, user_id)
    doc = await repo.get_document(_parse_uuid(doc_id, "文档"))

    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="文档不存在",
        )

    return {
        "id": str(doc["id"]),
        "knowledge_base_id": str(doc["knowledge_base_id"]),
        "filename": doc["filename"],
        "mime_type": doc.get("mime_type", ""),
        "file_size": doc.get("file_size", 0),
        "content_hash": doc.get("content_hash", ""),
        "status": doc.get("status", ""),
        "error_message": doc.get("error_message"),
        "created_at": str(doc["created_at"]) if doc.get("created_at") else None,
        "updated_at": str(doc["updated_at"]) if doc.get("updated_at") else None,
    }


# ═══════════════════════════════════════════════════════════════
# 6. DELETE /api/documents/{doc_id} — 删除文档
# ═══════════════════════════════════════════════════════════════

@router.delete("/documents/{doc_id}")
async def delete_document(
    doc_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    """删除文档及其关联的磁盘文件和数据库记录。

    先确认文档存在（repo 已限定 owner_id 自动隔离），
    再删除磁盘文件，最后清理数据库记录。

    返回:
        {"ok": True}
    """
    repo = _get_repo(request, user_id)
    doc_uuid = _parse_uuid(doc_id, "文档")

    # 确认文档存在（repo 已限定 owner_id，自动实现用户隔离）
    doc = await repo.get_document(doc_uuid)
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="文档不存在",
        )

    # 先删除数据库记录，避免数据库失败后留下指向不存在文件的记录
    storage_path = doc.get("storage_path", "")
    deleted = await repo.delete_document(doc_uuid)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="文档不存在",
        )

    # 数据库已清理后再删除磁盘文件；残留文件可由后台任务安全清理
    if storage_path:
        try:
            Path(storage_path).unlink(missing_ok=True)
        except OSError:
            pass

    return {"ok": True}


# ═══════════════════════════════════════════════════════════════
# 7. POST /api/knowledge-bases/{kb_id}/search — 语义搜索
# ═══════════════════════════════════════════════════════════════

@router.post("/knowledge-bases/{kb_id}/search")
async def search_knowledge_base(
    kb_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    """在指定知识库中执行语义搜索。

    请求体:
        {"query": str, "top_k": int}

    处理流程:
        1. 校验 query 非空、top_k 在 1-100
        2. 验证知识库归属
        3. query → gateway.embed → 查询向量
        4. repo.search_chunks → 向量检索

    返回:
        [{"document_id": str, "filename": str, "content": str,
          "page_number": int|null, "section_title": str|null, "score": float}, ...]
    """
    repo = _get_repo(request, user_id)

    data = await request.json()
    query = data.get("query", "").strip()
    top_k = data.get("top_k", 5)

    if not query:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="搜索查询不能为空",
        )

    if not isinstance(top_k, int) or top_k < 1 or top_k > 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="top_k 必须在 1 到 100 之间",
        )

    kb_uuid = _parse_uuid(kb_id, "知识库")
    await _verify_kb_ownership(repo, kb_uuid)

    # 查询向量化（统一走模型网关：预算/审计/统计）
    gateway = request.app.state.services.model_gateway
    embedding_result = await gateway.embed(
        [query],
        is_query=True,
        context=ModelRequestContext(
            owner_id=user_id,
            mode="knowledge",
            operation="knowledge_query_embedding",
        ),
    )
    query_embedding = embedding_result.vectors[0]

    # 向量检索
    results = await repo.search_chunks(kb_uuid, query_embedding, top_k)

    return [
        {
            "document_id": str(r["document_id"]),
            "filename": r["filename"],
            "content": r["content"],
            "page_number": r.get("page_number"),
            "section_title": r.get("section_title"),
            "score": float(r["score"]) if r.get("score") is not None else None,
        }
        for r in results
    ]
@router.post(
    "/knowledge-bases/{kb_id}/ask",
    response_model=RagAskResponse,
)
async def ask_knowledge_base(
    kb_id: str,
    payload: RagAskRequest,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> RagAskResponse:
    repo = _get_repo(request, user_id)
    kb_uuid = _parse_uuid(kb_id, "知识库")
    await _verify_kb_ownership(repo, kb_uuid)

    rag_graph = getattr(request.app.state, "rag_graph", None)
    if rag_graph is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RAG 服务暂不可用，请检查 DeepSeek 配置",
        )

    try:
        result = await rag_graph.answer(
            repo=repo,
            knowledge_base_id=kb_uuid,
            query=payload.query,
            top_k=(
                payload.top_k
                if payload.top_k is not None
                else get_settings().rag_top_k
            ),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from None
    except RagRetrievalError:
        logger.exception("RAG retrieval failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="知识库检索服务暂不可用",
        ) from None
    except RagModelError:
        logger.exception("RAG model generation failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="大模型回答生成失败",
        ) from None

    citations = [
        CitationResponse(
            source_id=item.source_id,
            chunk_id=item.chunk_id,
            document_id=item.document_id,
            chunk_index=item.chunk_index,
            filename=item.filename,
            excerpt=item.excerpt,
            score=item.score,
            page_number=item.page_number,
            section_title=item.section_title,
        )
        for item in result.citations
    ]

    return RagAskResponse(
        query=result.query,
        answer=result.answer,
        status=result.status,
        citations=citations,
        retrieved_count=result.retrieved_count,
        citation_valid=result.citation_valid,
    )
