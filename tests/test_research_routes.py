import unittest
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError

from interfaces.web.research_routes import _database, _serialize, _uuid, router
from interfaces.web.research_schemas import ResearchTaskCreate
from infrastructure.research.pg_repository import PgResearchRepository


class ResearchRouteTests(unittest.TestCase):
    def test_create_route_returns_202(self):
        route = next(
            item for item in router.routes
            if item.path == "/api/research-tasks" and "POST" in item.methods
        )
        self.assertEqual(route.status_code, 202)

    def test_delete_route_returns_204(self):
        route = next(
            item for item in router.routes
            if item.path == "/api/research-tasks/{task_id}"
            and "DELETE" in item.methods
        )
        self.assertEqual(route.status_code, 204)

    def test_task_detail_exposes_audit_sections(self):
        import inspect

        source = inspect.getsource(__import__(
            "interfaces.web.research_routes",
            fromlist=["get_research_task"],
        ).get_research_task)
        self.assertIn('result["usage"]', source)
        self.assertIn('result["usage_records"]', source)
        self.assertIn('result["evidence_snapshots"]', source)

    def test_delete_removes_linked_research_messages(self):
        import inspect

        source = inspect.getsource(PgResearchRepository.delete_task)
        self.assertIn(
            "DELETE FROM chat_messages WHERE research_task_id=$1",
            source,
        )
        self.assertIn("DELETE FROM research_tasks", source)

    def test_invalid_uuid_returns_400(self):
        with self.assertRaises(HTTPException) as caught:
            _uuid("not-a-uuid")
        self.assertEqual(caught.exception.status_code, 400)

    def test_query_is_trimmed(self):
        body = ResearchTaskCreate(query="   一个研究问题   ", conversation_id=uuid4())
        self.assertEqual(body.query, "一个研究问题")

    def test_short_query_is_rejected(self):
        with self.assertRaises(ValidationError):
            ResearchTaskCreate(query="  a ", conversation_id=uuid4())

    def test_database_offline_returns_503(self):
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(database=SimpleNamespace(pool=None))
            )
        )
        with self.assertRaises(HTTPException) as caught:
            _database(request)
        self.assertEqual(caught.exception.status_code, 503)

    def test_create_requires_running_worker(self):
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    database=SimpleNamespace(pool=object()),
                    research_worker=SimpleNamespace(running=False),
                )
            )
        )
        with self.assertRaises(HTTPException) as caught:
            _database(request, require_worker=True)
        self.assertEqual(caught.exception.status_code, 503)

    def test_serializer_does_not_expose_owner(self):
        row = {
            "id": "id", "owner_id": "secret", "query": "q",
            "status": "pending", "progress": 0,
            "current_step": "等待", "knowledge_base_id": None,
        }
        result = _serialize(row)
        self.assertNotIn("owner_id", result)