import asyncio
import logging
import subprocess

from app.research.export_service import (
    ExportService,
    safe_resolve_artifact_path,
)
from infrastructure.research.pg_repository import PgResearchRepository

logger = logging.getLogger(__name__)


def export_failure_message(exc: Exception) -> str:
    if isinstance(exc, (asyncio.TimeoutError, subprocess.TimeoutExpired)):
        return "导出任务执行超时"
    message = str(exc).strip()
    safe_messages = {
        "不支持的导出格式",
        "导出文件为空",
        "导出文件超过大小限制",
        "PDF 导出失败",
        "不支持的 PDF 转换组件",
    }
    if message in safe_messages:
        return message
    if isinstance(exc, FileNotFoundError):
        return "PDF 导出组件不可用"
    return "导出任务执行失败"


class ResearchExportWorker:
    def __init__(self, database, settings) -> None:
        self._database = database
        self._settings = settings
        self._service = ExportService(settings)
        self._task = None
        self._stopping = asyncio.Event()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self._database.pool is None:
            return
        repo = PgResearchRepository(self._database)
        recovered = await repo.recover_export_jobs()
        if recovered:
            logger.warning(
                "Recovered %s interrupted export jobs",
                recovered,
            )
        self._stopping.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _run_loop(self) -> None:
        repo = PgResearchRepository(self._database)
        next_cleanup = 0.0
        while not self._stopping.is_set():
            job = None
            try:
                now = asyncio.get_running_loop().time()
                if now >= next_cleanup:
                    await self._cleanup_expired_artifacts(repo)
                    next_cleanup = (
                        now
                        + self._settings.research_artifact_cleanup_seconds
                    )
                job = await repo.claim_export_job()
                if job is None:
                    await asyncio.sleep(
                        self._settings.research_export_poll_seconds
                    )
                    continue

                plan = await repo.get_plan(job["task_id"])
                sources = await repo.list_sources(job["task_id"])
                artifact = await asyncio.to_thread(
                    self._service.generate,
                    job,
                    scope=(plan or {}).get("scope", ""),
                    sources=sources,
                )
                await repo.complete_export_job(
                    job["id"],
                    filename=artifact.filename,
                    storage_key=artifact.storage_key,
                    sha256=artifact.sha256,
                    size_bytes=artifact.size_bytes,
                    ttl_days=self._settings.research_artifact_ttl_days,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception(
                    "Research export job failed: %s",
                    job["id"] if job else "unclaimed",
                )
                if job is not None:
                    await repo.fail_export_job(
                        job["id"],
                        export_failure_message(exc),
                    )
                else:
                    await asyncio.sleep(
                        self._settings.research_export_poll_seconds
                    )

    async def _cleanup_expired_artifacts(self, repo) -> None:
        artifacts = await repo.cleanup_expired_artifacts(
            self._settings.research_artifact_cleanup_batch_size
        )
        for artifact in artifacts:
            try:
                path = safe_resolve_artifact_path(
                    self._settings.research_artifact_storage_path,
                    artifact["storage_key"],
                )
                await asyncio.to_thread(path.unlink, missing_ok=True)
            except (OSError, ValueError):
                logger.exception(
                    "Expired artifact file cleanup failed: %s",
                    artifact.get("id"),
                )
