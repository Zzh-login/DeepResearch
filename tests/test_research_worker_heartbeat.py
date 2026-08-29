import inspect
import unittest
from pathlib import Path

from infrastructure.research.pg_repository import PgResearchRepository


ROOT = Path(__file__).resolve().parents[1]


class ResearchWorkerHeartbeatTests(unittest.TestCase):
    def test_standalone_entry_exists(self):
        path = ROOT / "run_research_worker.py"
        self.assertTrue(path.exists())
        text = path.read_text(encoding="utf-8")
        self.assertIn("ResearchWorker", text)
        self.assertIn("heartbeat_loop", text)

    def test_standalone_entry_starts_export_worker(self):
        text = (ROOT / "run_research_worker.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("ResearchExportWorker", text)
        self.assertIn("await export_worker.start()", text)
        self.assertIn("await export_worker.stop()", text)

    def test_health_check_uses_database_timestamp(self):
        source = inspect.getsource(PgResearchRepository.has_healthy_worker)
        self.assertIn("service_heartbeats", source)
        self.assertIn("updated_at", source)
        self.assertIn("stale_seconds", source)
