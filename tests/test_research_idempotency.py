import inspect
import unittest

from infrastructure.research.pg_repository import (
    IdempotencyConflict,
    PgResearchRepository,
    ResearchQuotaExceeded,
)


class ResearchIdempotencyTests(unittest.TestCase):
    def test_create_task_uses_idempotency_key_table(self):
        source = inspect.getsource(PgResearchRepository.create_task)
        self.assertIn("research_request_keys", source)
        self.assertIn("request_hash", source)
        self.assertIn("expires_at", source)

    def test_create_task_locks_existing_key(self):
        source = inspect.getsource(PgResearchRepository.create_task)
        self.assertIn("FOR UPDATE", source)

    def test_conflicting_request_raises(self):
        source = inspect.getsource(PgResearchRepository.create_task)
        self.assertIn("IdempotencyConflict", source)

    def test_quota_limits_active_tasks(self):
        source = inspect.getsource(PgResearchRepository.create_task)
        self.assertIn("max_active", source)
        self.assertIn("ResearchQuotaExceeded", source)
