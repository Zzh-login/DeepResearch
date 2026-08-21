import asyncio
import logging

from app.research.graph import (
    CitationValidationError,
    DeepResearchGraph,
    ResearchCancelled,
)
from infrastructure.knowledge.pg_repository import PgKnowledgeRepository
from infrastructure.research.pg_repository import PgResearchRepository


logger = logging.getLogger(__name__)


SAFE_FAILURE_MESSAGES = {
    "研究计划不是有效 JSON",
    "研究计划必须是 JSON 对象",
    "研究模型返回空内容",
    "研究计划的 queries 必须是数组",
    "研究计划必须包含 3 到 5 个不同搜索词",
    "没有找到可用的网页来源",
    "上下文预算中没有可用网页来源",
    "研究报告未通过引用校验",
    "研究报告内容过长被截断，请缩小研究范围后重试",
}


def user_failure_message(exc: Exception) -> str:
    """Map internal failures to safe, actionable messages for the UI."""
    if isinstance(exc, asyncio.TimeoutError):
        return "研究任务执行超时，请缩小问题范围后重试"
    if isinstance(exc, CitationValidationError):
        issues = exc.issues or []
        if not issues:
            return "研究报告未通过引用校验"
        preview = "；".join(issues[:3])
        if len(issues) > 3:
            preview += f" 等共 {len(issues)} 条"
        return "研究报告未通过引用校验：" + preview
    message = str(exc).strip()
    if message in SAFE_FAILURE_MESSAGES:
        return message
    if message == "研究报告不是有效 JSON":
        return message
    if message.startswith("研究报告结构无效"):
        return " ".join(message[:240].split())
    return "研究任务执行失败，请稍后重试"


class ResearchWorker:
    def __init__(self, database, graph, settings) -> None:
        self._database = database
        self._graph = graph
        self._settings = settings
        self._task = None
        self._stopping = asyncio.Event()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self._database.pool is None:
            return
        repo = PgResearchRepository(self._database)
        recovered = await repo.recover_interrupted_tasks()
        if recovered:
            logger.warning("Recovered %s interrupted research tasks", recovered)
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
        while not self._stopping.is_set():
            try:
                task = await repo.claim_next_task(
                    self._settings.research_max_attempts
                )
                if task is None:
                    await asyncio.sleep(
                        self._settings.research_worker_poll_seconds
                    )
                    continue

                knowledge_repo = PgKnowledgeRepository(
                    self._database,
                    task["owner_id"],
                )
                try:
                    report = await asyncio.wait_for(
                        self._graph.run(task, repo, knowledge_repo),
                        timeout=self._settings.research_task_timeout_seconds,
                    )
                    if not await repo.is_cancelled(task["id"]):
                        await repo.complete_task(task["id"], report)
                except ResearchCancelled:
                    continue
                except Exception as exc:
                    logger.exception("Research task %s failed", task["id"])
                    await repo.fail_task(
                        task["id"],
                        user_failure_message(exc),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Research worker loop failed")
                await asyncio.sleep(
                    self._settings.research_worker_poll_seconds
                )
