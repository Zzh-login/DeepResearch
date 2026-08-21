import asyncio
import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.research.worker import ResearchWorker, user_failure_message


class _FakeRepository:
    recovered = 0

    def __init__(self, database):
        self.database = database

    async def recover_interrupted_tasks(self):
        type(self).recovered += 1
        return 1

    async def claim_next_task(self, max_attempts):
        return None


class ResearchWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _FakeRepository.recovered = 0
        self.database = SimpleNamespace(pool=object())
        self.graph = SimpleNamespace(run=AsyncMock())
        self.settings = SimpleNamespace(
            research_worker_poll_seconds=0.01,
            research_max_attempts=2,
            research_task_timeout_seconds=1,
        )

    async def test_start_recovers_and_marks_running(self):
        worker = ResearchWorker(self.database, self.graph, self.settings)
        with patch("app.research.worker.PgResearchRepository", _FakeRepository):
            await worker.start()
            self.assertTrue(worker.running)
            self.assertEqual(_FakeRepository.recovered, 1)
            await worker.stop()

    async def test_stop_is_idempotent(self):
        worker = ResearchWorker(self.database, self.graph, self.settings)
        with patch("app.research.worker.PgResearchRepository", _FakeRepository):
            await worker.start()
            await worker.stop()
            await worker.stop()
        self.assertFalse(worker.running)

    async def test_offline_database_does_not_start(self):
        worker = ResearchWorker(
            SimpleNamespace(pool=None), self.graph, self.settings
        )
        await worker.start()
        self.assertFalse(worker.running)

    def test_each_task_has_total_timeout(self):
        source = inspect.getsource(ResearchWorker._run_loop)
        self.assertIn("asyncio.wait_for", source)
        self.assertIn("research_task_timeout_seconds", source)

    def test_user_error_is_sanitized(self):
        source = inspect.getsource(ResearchWorker._run_loop)
        self.assertIn("user_failure_message(exc)", source)
        self.assertNotIn("fail_task(task[\"id\"], str(exc))", source)

    def test_known_failure_is_safe_and_actionable(self):
        self.assertEqual(
            user_failure_message(RuntimeError("研究报告未通过引用校验")),
            "研究报告未通过引用校验",
        )
        self.assertEqual(
            user_failure_message(RuntimeError("secret=/private/path")),
            "研究任务执行失败，请稍后重试",
        )

    def test_timeout_has_specific_message(self):
        self.assertIn("超时", user_failure_message(asyncio.TimeoutError()))

    def test_loop_survives_database_error(self):
        source = inspect.getsource(ResearchWorker._run_loop)
        self.assertIn("Research worker loop failed", source)
        self.assertIn("asyncio.CancelledError", source)
