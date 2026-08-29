import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from infrastructure.research.pg_repository import PgResearchRepository
from domain.research.checkpoint import ResearchCheckpointState


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
        self.fetch_result = []
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
        return self.fetch_result

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
        self.assertIn("status", insert_sql)
        self.assertIn("'created'", insert_sql)

    async def test_get_task_always_filters_owner(self):
        await self.repo.get_task(uuid4(), "owner-a")
        sql = self.connection.calls[0][1]
        self.assertIn("owner_id=$2", sql)

    async def test_latest_checkpoint_filters_by_task_owner(self):
        await self.repo.get_latest_checkpoint(uuid4(), "owner-a")
        sql = self.connection.calls[0][1]
        self.assertIn("research_checkpoints", sql)
        self.assertIn("research_tasks", sql)
        self.assertIn("task.owner_id=$2", sql)


    async def test_list_events_filters_owner_and_sequence(self):
        task_id = uuid4()
        await self.repo.list_events(
            task_id,
            "owner-a",
            after_sequence=7,
            limit=50,
        )

        sql = self.connection.calls[0][1]

        self.assertIn("research_events", sql)
        self.assertIn("research_tasks", sql)
        self.assertIn("task.owner_id=$2", sql)
        self.assertIn("event.sequence > $3", sql)
        self.assertIn("LIMIT $4", sql)
    async def test_claim_is_atomic_and_skip_locked(self):
        task_id = uuid4()
        self.connection.row = {
            "id": task_id,
            "owner_id": "owner-a",
            "query": "q",
            "status": "planning",
        }
        self.connection.value = 1
        task = await self.repo.claim_next_task(2)
        all_sql = "\n".join(call[1] for call in self.connection.calls)
        self.assertEqual(task["status"], "planning")
        self.assertIn("FOR UPDATE SKIP LOCKED", all_sql)
        self.assertIn("attempts=attempts+1", all_sql)
        self.assertIn("attempts >= $1", all_sql)

    async def test_cancel_uses_safe_cancellation_request(self):
        await self.repo.cancel_task(uuid4(), "owner-a")
        sql = self.connection.calls[0][1]
        self.assertIn("owner_id=$2", sql)
        self.assertIn("FOR UPDATE", sql)
        self.assertIn("cancel_requested_at", "\n".join(
            call[1] for call in self.connection.calls
        ))

    async def test_complete_finalizes_pending_cancel_at_completion_boundary(self):
        self.connection.value = 1
        self.connection.row = {
            "status": "verifying",
            "owner_id": "owner-a",
            "conversation_id": None,
            "cancel_requested_at": "2026-08-27T00:00:00+00:00",
            "pause_requested_at": None,
        }

        outcome = await self.repo.complete_task(uuid4(), {"markdown": "ok"})

        self.assertEqual(outcome, "cancelled")
        sql = "\n".join(call[1] for call in self.connection.calls)
        self.assertIn("status='cancelled'", sql)
        self.assertNotIn("status='completed'", sql)

    async def test_complete_does_not_overwrite_terminal_cancelled_task(self):
        self.connection.row = {
            "status": "cancelled",
            "owner_id": "owner-a",
            "conversation_id": None,
            "cancel_requested_at": None,
            "pause_requested_at": None,
        }

        outcome = await self.repo.complete_task(uuid4(), {"markdown": "ok"})

        self.assertEqual(outcome, "state_changed")
        sql = "\n".join(call[1] for call in self.connection.calls)
        self.assertNotIn("status='completed'", sql)

    # async def test_complete_cannot_overwrite_cancelled(self):
    #     task_id = uuid4()
    #     # append_event 内部用 fetchval 读 last_event_sequence，需要非 None
    #     self.connection.value = 1

    #     # 1) verifying 且未被取消/暂停 -> 写成 completed 并清空租约
    #     self.connection.row = {
    #         "status": "verifying",
    #         "owner_id": "owner-a",
    #         "conversation_id": None,
    #         "cancel_requested_at": None,
    #         "pause_requested_at": None,
    #     }
    #     outcome = await self.repo.complete_task(task_id, {"markdown": "ok"})
    #     self.assertEqual(outcome, "completed")
    #     update_sql = [
    #         c[1] for c in self.connection.calls
    #         if "status='completed'" in c[1]
    #     ][0]
    #     self.assertIn("status='verifying'", update_sql)
    #     self.assertIn("lease_owner=NULL", update_sql)
    #     self.assertIn("lease_expires_at=NULL", update_sql)

    #     # 2) 已被请求取消 -> 不应覆盖成 completed，而是置为 cancelled
    #     self.connection.calls = []
    #     self.connection.row = {
    #         "status": "verifying",
    #         "owner_id": "owner-a",
    #         "conversation_id": None,
    #         "cancel_requested_at": "2026-08-27T00:00:00+00:00",
    #         "pause_requested_at": None,
    #     }
    #     outcome = await self.repo.complete_task(task_id, {"markdown": "ok"})
    #     self.assertEqual(outcome, "cancelled")
    #     cancel_sql = [
    #         c[1] for c in self.connection.calls
    #         if "status='cancelled'" in c[1]
    #     ][0]
    #     self.assertIn("cancel_requested_at=NULL", cancel_sql)
    #     self.assertIn("lease_owner=NULL", cancel_sql)

    async def test_fail_clears_lease_and_records_failure_event(self):
        task_id = uuid4()
        self.connection.row = {"id": task_id}
        self.connection.fetch_result = [{"id": task_id}]
        self.connection.value = 1

        await self.repo.fail_task(task_id, "引用校验失败")

        sql = "\n".join(call[1] for call in self.connection.calls)
        calls = repr(self.connection.calls)
        self.assertIn("status='failed'", sql)
        self.assertIn("lease_owner=NULL", sql)
        self.assertIn("lease_expires_at=NULL", sql)
        self.assertIn("task.failed", calls)
        self.assertIn("worker_failure", calls)

    async def test_max_attempts_failure_clears_lease_and_records_event(self):
        task_id = uuid4()
        self.connection.fetch_result = [{"id": task_id}]
        self.connection.value = 1

        task = await self.repo.claim_next_task(2)

        self.assertIsNone(task)
        sql = "\n".join(call[1] for call in self.connection.calls)
        calls = repr(self.connection.calls)
        self.assertIn("attempts >= $1", sql)
        self.assertIn("RETURNING id", sql)
        self.assertIn("lease_owner=NULL", sql)
        self.assertIn("lease_expires_at=NULL", sql)
        self.assertIn("task.failed", calls)
        self.assertIn("max_attempts_exceeded", calls)

    async def test_recovery_resets_interrupted_states_and_clears_lease(self):
        self.connection.value = 1
        self.connection.fetch_result = [
            {"id": uuid4(), "status": "searching"},
            {"id": uuid4(), "status": "writing"},
        ]
        count = await self.repo.recover_interrupted_tasks()
        sql = "\n".join(call[1] for call in self.connection.calls)
        self.assertEqual(count, 2)
        self.assertIn("'searching'", sql)
        self.assertIn("'writing'", sql)
        self.assertIn("lease_owner=NULL", sql)
        self.assertIn("lease_expires_at=NULL", sql)

    async def test_transition_status_allows_repair_return(self):
        # verifying → writing：修复流程必须放行
        self.connection.row = {"status": "verifying"}
        self.connection.value = 1
        changed = await self.repo.transition_status(
            uuid4(), "verifying", "writing", "修复研究报告", 88,
        )
        self.assertTrue(changed)

    async def test_transition_status_rejects_terminal_migration(self):
        # completed 之后不能再迁移
        self.connection.row = {"status": "completed"}
        with self.assertRaises(ValueError):
            await self.repo.transition_status(
                uuid4(), "completed", "planning", "x", 10,
            )

    async def test_save_checkpoint_persists_state_and_hash(self):
        task_id = uuid4()
        self.connection.value = 1
        checkpoint = ResearchCheckpointState(
            query="测试研究问题",
            scope="测试范围",
            plan={"queries": ["问题一", "问题二", "问题三"]},
            current_node="plan",
            attempt=0,
        )

        await self.repo.save_checkpoint(task_id, checkpoint)

        sql = "\n".join(
            call[1] for call in self.connection.calls
        )
        self.assertIn("INSERT INTO research_checkpoints", sql)
        self.assertIn("state_hash", sql)
        self.assertIn("node_name", sql)

    async def test_checkpoint_and_transition_share_one_transaction(self):
        self.connection.row = {"status": "planning"}
        self.connection.value = 1
        checkpoint = ResearchCheckpointState(
            query="q",
            plan={"queries": ["a", "b", "c"]},
            current_node="plan",
        )

        await self.repo.save_checkpoint_and_transition(
            uuid4(), checkpoint, "planning", "searching", "检索本地知识库", 20,
        )

        sql = "\n".join(call[1] for call in self.connection.calls)
        calls = repr(self.connection.calls)
        self.assertIn("INSERT INTO research_checkpoints", sql)
        self.assertIn("SET status=$2", sql)
        self.assertIn("checkpoint.saved", calls)
        self.assertIn("phase.changed", calls)

    async def test_request_pause_handles_created_task(self):
        self.connection.row = {"status": "created", "pause_requested_at": None}
        self.connection.value = 1

        result = await self.repo.request_pause(uuid4(), "owner-a")

        self.assertEqual(result, "paused")
        sql = "\n".join(call[1] for call in self.connection.calls)
        self.assertIn("status='paused'", sql)
        self.assertIn("lease_owner=NULL", sql)

    async def test_resume_only_accepts_paused_task(self):
        self.connection.row = {"status": "paused"}
        self.connection.value = 1

        result = await self.repo.resume_task(uuid4(), "owner-a")

        self.assertEqual(result, "created")
        sql = "\n".join(call[1] for call in self.connection.calls)
        self.assertIn("status='created'", sql)
        self.assertIn("next_attempt_at=NOW()", sql)

    async def test_retryable_failure_schedules_backoff_and_clears_lease(self):
        self.connection.row = {
            "status": "searching",
            "attempts": 1,
            "cancel_requested_at": None,
            "pause_requested_at": None,
        }
        self.connection.value = 1

        with patch("infrastructure.research.pg_repository.random.uniform", return_value=0.25):
            result = await self.repo.handle_failure(
                uuid4(), "网络暂时不可用", True, 3, 2.0, 60.0, 0.5,
            )

        self.assertEqual(result, "retry_scheduled")
        sql = "\n".join(call[1] for call in self.connection.calls)
        calls = repr(self.connection.calls)
        self.assertIn("status='created'", sql)
        self.assertIn("next_attempt_at=NOW()", sql)
        self.assertIn("lease_owner=NULL", sql)
        self.assertIn("task.retry_scheduled", calls)

    async def test_non_retryable_failure_is_terminal(self):
        self.connection.row = {
            "status": "writing",
            "attempts": 1,
            "cancel_requested_at": None,
            "pause_requested_at": None,
        }
        self.connection.value = 1

        result = await self.repo.handle_failure(
            uuid4(), "报告结构无效", False, 3, 2.0, 60.0, 0.5,
        )

        self.assertEqual(result, "failed")
        sql = "\n".join(call[1] for call in self.connection.calls)
        self.assertIn("status='failed'", sql)
        self.assertIn("lease_expires_at=NULL", sql)

    def test_report_audit_persists_hash_and_block_mapping(self):
        import inspect

        source = inspect.getsource(PgResearchRepository._persist_report_audit)
        self.assertIn("research_report_blocks", source)
        self.assertIn("research_evidence_snapshots", source)
        self.assertIn("hashlib.sha256", source)
        self.assertIn("cited_by_source", source)

    def test_repository_does_not_create_its_own_pool(self):
        source = inspect.getsource(PgResearchRepository)
        self.assertNotIn("create_pool", source)

    async def test_complete_task_returns_state_changed_instead_of_silent_none(self):
        self.connection.row = {
            "status": "writing",
            "owner_id": "owner-a",
            "conversation_id": None,
            "cancel_requested_at": None,
            "pause_requested_at": None,
        }
        result = await self.repo.complete_task(uuid4(), {"markdown": "x"})
        self.assertEqual(result, "state_changed")

    def test_stale_cancel_recovery_clears_lease_and_emits_event(self):
        import inspect

        source = inspect.getsource(
            PgResearchRepository.finalize_stale_cancel_requests
        )
        self.assertIn("cancel_requested_at IS NOT NULL", source)
        self.assertIn("lease_expires_at < NOW()", source)
        self.assertIn("lease_owner=NULL", source)
        self.assertIn("task.cancelled", source)

    def test_checkpoint_idempotency_returns_persisted_id(self):
        import inspect

        source = inspect.getsource(
            PgResearchRepository.save_checkpoint_and_transition
        )
        self.assertIn("SELECT id", source)
        self.assertIn("state_hash=$3", source)
        self.assertNotIn("return checkpoint_id\n                    raise", source)

    def test_repository_accepts_shared_audit_dependency(self):
        source = inspect.getsource(PgResearchRepository.__init__)
        self.assertIn("audit_repository=None", source)
        self.assertIn("self._audit_repository", source)

    def test_completion_audit_does_not_store_report(self):
        source = inspect.getsource(PgResearchRepository.complete_task)
        self.assertIn('"research.complete"', source)
        self.assertIn('"progress": 100', source)
        self.assertNotIn('"report": report', source)
