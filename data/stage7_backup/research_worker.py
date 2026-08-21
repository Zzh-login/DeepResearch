import asyncio
import logging

from app.research.graph import DeepResearchGraph, ResearchCancelled
from infrastructure.knowledge.pg_repository import PgKnowledgeRepository
from infrastructure.research.pg_repository import PgResearchRepository


logger = logging.getLogger(__name__)


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
                except Exception:
                    logger.exception("Research task %s failed", task["id"])
                    await repo.fail_task(
                        task["id"],
                        "研究任务执行失败，请稍后重试",
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Research worker loop failed")
                await asyncio.sleep(
                    self._settings.research_worker_poll_seconds
                )