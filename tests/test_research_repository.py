import inspect
import unittest
from types import SimpleNamespace
from uuid import uuid4

from infrastructure.research.pg_repository import PgResearchRepository


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Connection:
    def __init__(self):
        self.calls = []
        self.row = None
        self.value = None
        self.execute_result = "UPDATE 1"

    def transaction(self):
        return _AsyncContext(self)

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return self.execute_result

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return self.row

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return []

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        return self.value


class _Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _AsyncContext(self.connection)


class ResearchRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.connection = _Connection()
        database = SimpleNamespace(pool=_Pool(self.connection))
        self.repo = PgResearchRepository(database)

    async def test_create_task_uses_database_defaults(self):
        self.connection.value = 1
        task_id, reused = await self.repo.create_task(
            "owner-a", "问题", None, uuid4(),
            "k" * 16, "request-hash", 2, 24,
        )
        self.assertIsNotNone(task_id)
        self.assertFalse(reused)
        insert_sql = [
            call[1] for call in self.connection.calls
            if "INSERT INTO research_tasks" in call[1]
        ][0]
        self.assertNotIn("status", insert_sql)

    async def test_get_task_always_filters_owner(self):
        await self.repo.get_task(uuid4(), "owner-a")
        sql = self.connection.calls[0][1]
        self.assertIn("owner_id=$2", sql)

    async def test_claim_is_atomic_and_skip_locked(self):
        task_id = uuid4()
        self.connection.row = {
            "id": task_id,
            "owner_id": "owner-a",
            "query": "q",
        }
        task = await self.repo.claim_next_task(2)
        all_sql = "\n".join(call[1] for call in self.connection.calls)
        self.assertEqual(task["status"], "running")
        self.assertIn("FOR UPDATE SKIP LOCKED", all_sql)
        self.assertIn("attempts=attempts+1", all_sql)
        self.assertIn("attempts >= $1", all_sql)

    async def test_cancel_only_changes_pending_or_running(self):
        await self.repo.cancel_task(uuid4(), "owner-a")
        sql = self.connection.calls[0][1]
        self.assertIn("owner_id=$2", sql)
        self.assertIn("status IN ('pending', 'running')", sql)

    async def test_complete_cannot_overwrite_cancelled(self):
        await self.repo.complete_task(uuid4(), {"markdown": "ok"})
        sql = self.connection.calls[0][1]
        self.assertIn("status='running'", sql)

    async def test_recovery_only_resets_running(self):
        self.connection.execute_result = "UPDATE 2"
        count = await self.repo.recover_interrupted_tasks()
        sql = self.connection.calls[0][1]
        self.assertEqual(count, 2)
        self.assertIn("WHERE status='running'", sql)
        self.assertNotIn("completed', 'failed", sql)

    def test_repository_does_not_create_its_own_pool(self):
        source = inspect.getsource(PgResearchRepository)
        self.assertNotIn("create_pool", source)