import asyncio
import logging

import asyncpg
import httpx
from app.research.graph import (
    CitationValidationError,
    DeepResearchGraph,
    ResearchCancelled,
    ResearchPaused,
)
from infrastructure.knowledge.pg_repository import PgKnowledgeRepository
from infrastructure.research.pg_repository import PgResearchRepository
from infrastructure.conversation.pg_repository import (PgConversationRepository,)
from app.chat.memory_service import UnifiedMemoryService

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


def is_retryable_error(exc: Exception) -> bool:
    """Only retry failures that can plausibly succeed without code changes."""
    if isinstance(
        exc,
        (
            asyncio.TimeoutError,
            ConnectionError,
            OSError,
            asyncpg.PostgresConnectionError,
            asyncpg.InterfaceError,
            httpx.TimeoutException,
            httpx.NetworkError,
        ),
    ):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    if isinstance(exc, (CitationValidationError, ValueError, LookupError)):
        return False
    return False


class ResearchWorker:
    def __init__(self, database, graph, settings, audit_repository=None) -> None:
        self._database = database
        self._graph = graph
        self._settings = settings
        self._audit_repository = audit_repository
        self._task = None
        self._stopping = asyncio.Event()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def _repository(self) -> PgResearchRepository:
        if self._audit_repository is None:
            return PgResearchRepository(self._database)
        return PgResearchRepository(
            self._database,
            audit_repository=self._audit_repository,
        )

    async def start(self) -> None:
        if self._database.pool is None:
            return
        repo = self._repository()
        cancelled = await repo.finalize_stale_cancel_requests()
        if cancelled:
            logger.warning(
                "Finalized %s stale cancelled research tasks",
                cancelled,
            )
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
        repo = self._repository()
        next_recovery_scan = 0.0
        while not self._stopping.is_set():
            try:
                now = asyncio.get_running_loop().time()
                if now >= next_recovery_scan:
                    cancelled = await repo.finalize_stale_cancel_requests()
                    if cancelled:
                        logger.warning(
                            "Finalized %s stale cancelled research tasks",
                            cancelled,
                        )
                    recovered = await repo.recover_interrupted_tasks()
                    if recovered:
                        logger.warning(
                            "Recovered %s interrupted research tasks",
                            recovered,
                        )
                    next_recovery_scan = (
                        now + self._settings.research_recovery_scan_seconds
                    )
                task = await repo.claim_next_task(
                    self._settings.research_max_attempts,
                    self._settings.research_task_lease_seconds,
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

                if await repo.is_cancelled(task["id"]):
                    await repo.finalize_cancel(task["id"])
                    continue

                conversation_context = []

                conversation_id = task.get("conversation_id")
                if conversation_id is not None:
                    conversation_repo = PgConversationRepository(
                        self._database,
                        task["owner_id"],
                    )
                    conversation_context = (
                        await conversation_repo.list_context_messages(
                            conversation_id,
                            limit=10,
                        )
                    )
                user_memory_context = UnifiedMemoryService(
                    str(task["owner_id"])
                ).render_for_mode("deep_research")
                try:
                    checkpoint = await repo.get_latest_checkpoint(task["id"], task["owner_id"])
                    resume_state = checkpoint["state"] if checkpoint else None
                    report = await asyncio.wait_for(
                        self._run_with_lease(
                            task,
                            repo,
                            knowledge_repo,
                            conversation_context,
                            user_memory_context,
                            resume_state,
                        ),
                        timeout=self._settings.research_task_timeout_seconds,
                    )
                    outcome = await repo.complete_task(task["id"], report)
                    if outcome == "completed":
                        logger.info("Research task %s completed", task["id"])
                    elif outcome == "paused":
                        logger.info(
                            "Research task %s paused at completion boundary",
                            task["id"],
                        )
                    elif outcome == "cancelled":
                        logger.info(
                            "Research task %s cancelled at completion boundary",
                            task["id"],
                        )
                    else:
                        raise RuntimeError(
                            "研究任务完成时状态已被其他事务修改"
                        )
                except ResearchCancelled:
                    await repo.finalize_cancel(task["id"])
                    continue
                except ResearchPaused:
                    await repo.mark_paused_if_requested(task["id"])
                    continue
                except ResearchLeaseLost:
                    logger.warning(
                        "Research task %s lease was lost; leaving recovery "
                        "to the current lease owner",
                        task["id"],
                    )
                    continue
                except Exception as exc:
                    logger.exception("Research task %s failed", task["id"])
                    if await repo.is_cancelled(task["id"]):
                        await repo.finalize_cancel(task["id"])
                    elif await repo.is_pause_requested(task["id"]):
                        await repo.mark_paused_if_requested(task["id"])
                    else:
                        await repo.handle_failure(
                            task_id=task["id"],
                            message=user_failure_message(exc),
                            retryable=is_retryable_error(exc),
                            max_attempts=self._settings.research_max_attempts,
                            retry_base_seconds=self._settings.research_retry_base_seconds,
                            retry_max_seconds=self._settings.research_retry_max_seconds,
                            retry_jitter_seconds=self._settings.research_retry_jitter_seconds,
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Research worker loop failed")
                await asyncio.sleep(
                    self._settings.research_worker_poll_seconds
                )

    async def _run_with_lease(
        self,
        task,
        repo,
        knowledge_repo,
        conversation_context,
        user_memory_context,
        resume_state,
    ):
        graph_task = asyncio.create_task(
            self._graph.run(
                task,
                repo,
                knowledge_repo,
                conversation_context=conversation_context,
                user_memory_context=user_memory_context,
                resume_state=resume_state,
            )
        )
        lease_task = asyncio.create_task(
            self._renew_lease_loop(repo, task["id"], task["lease_owner"])
        )
        try:
            done, _ = await asyncio.wait(
                {graph_task, lease_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if lease_task in done:
                lease_task.result()
                raise ResearchLeaseLost()
            return await graph_task
        finally:
            for pending in (graph_task, lease_task):
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(graph_task, lease_task, return_exceptions=True)

    async def _renew_lease_loop(self, repo, task_id, lease_owner):
        while True:
            await asyncio.sleep(self._settings.research_lease_renew_seconds)
            renewed = await repo.renew_lease(
                task_id,
                lease_owner,
                self._settings.research_task_lease_seconds,
            )
            if not renewed:
                raise ResearchLeaseLost()


class ResearchLeaseLost(RuntimeError):
    """The database lease changed owner while this worker was running."""
