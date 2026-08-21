import inspect
import unittest
from pathlib import Path

from infrastructure.conversation.pg_repository import PgConversationRepository
from infrastructure.research.pg_repository import PgResearchRepository


ROOT = Path(__file__).resolve().parents[1]


class Stage7ContractTests(unittest.TestCase):
    def test_conversation_repository_has_owner_filters_and_transactions(self):
        source = inspect.getsource(PgConversationRepository)
        self.assertGreaterEqual(source.count("owner_id"), 8)
        self.assertIn("conn.transaction()", source)
        self.assertIn("message_citations", source)

    def test_research_repository_has_idempotency_and_quota(self):
        source = inspect.getsource(PgResearchRepository.create_task)
        self.assertIn("research_request_keys", source)
        self.assertIn("request_hash", source)
        self.assertIn("max_active", source)
        self.assertIn("IdempotencyConflict", source)

    def test_worker_entry_uses_advisory_lock(self):
        source = (ROOT / "run_research_worker.py").read_text(encoding="utf-8")
        self.assertIn("pg_try_advisory_lock", source)
        self.assertIn("pg_advisory_unlock", source)

    def test_research_route_checks_database_worker_health(self):
        source = (ROOT / "interfaces" / "web" / "research_routes.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("has_healthy_worker", source)
        self.assertIn("Idempotency-Key", source)
        self.assertIn("PgConversationRepository", source)

    def test_frontend_has_inflight_guard_and_backoff(self):
        source = (ROOT / "interfaces" / "web" / "frontend" / "index.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("researchSubmitInFlight", source)
        self.assertIn("MAX_RESEARCH_POLL_FAILURES", source)
        self.assertIn("crypto.randomUUID()", source)

    def test_history_migration_is_idempotent(self):
        source = (ROOT / "scripts" / "migrate_json_history.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("IMPORT_TITLE", source)
        self.assertIn("已导入，跳过", source)
        self.assertIn("chat_history.json", source)
