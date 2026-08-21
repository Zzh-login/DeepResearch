import inspect
import unittest

from app.research.graph import DeepResearchGraph
from infrastructure.research.pg_repository import PgResearchRepository


class ResearchPlanPersistenceTests(unittest.TestCase):
    def test_plan_is_persisted_after_validation(self):
        source = inspect.getsource(DeepResearchGraph._plan)
        self.assertIn("replace_plan", source)
        self.assertIn("plan_queries", source)

    def test_each_search_updates_subtask(self):
        source = inspect.getsource(DeepResearchGraph._search_web)
        self.assertIn("update_subtask", source)
        self.assertIn('"running"', source)
        self.assertIn('"completed"', source)
        self.assertIn('"failed"', source)

    def test_repository_replaces_plan_transactionally(self):
        source = inspect.getsource(PgResearchRepository.replace_plan)
        self.assertIn("conn.transaction()", source)
        self.assertIn("DELETE FROM research_subtasks", source)
        self.assertIn("INSERT INTO research_subtasks", source)
