import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import asyncpg
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient

from infrastructure.auth.dependency import get_current_user
from interfaces.web.knowledge_routes import (
    KnowledgeDatabaseRoute,
    router as knowledge_router,
)


def build_test_app(error: Exception) -> FastAPI:
    app = FastAPI()
    app.state.database_status = "ok"
    app.state.database_error = None
    router = APIRouter(route_class=KnowledgeDatabaseRoute)

    @router.get("/probe")
    async def probe():
        raise error

    app.include_router(router)
    return app


class KnowledgeDatabaseRouteTests(unittest.TestCase):
    def test_real_knowledge_base_list_route_maps_database_outage_to_503(self):
        app = FastAPI()
        app.state.database = SimpleNamespace(pool=object())
        app.state.database_status = "ok"
        app.state.database_error = None
        app.dependency_overrides[get_current_user] = lambda: "test-user"
        app.include_router(knowledge_router)

        with patch(
            "interfaces.web.knowledge_routes.PgKnowledgeRepository.list_knowledge_bases",
            new=AsyncMock(side_effect=ConnectionRefusedError("database stopped")),
        ):
            with TestClient(app, raise_server_exceptions=False) as client:
                response = client.get("/api/knowledge-bases")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": "知识库服务暂不可用，请稍后重试"},
        )

    def test_connection_refused_is_mapped_to_503(self):
        app = build_test_app(ConnectionRefusedError("database stopped"))

        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/probe")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": "知识库服务暂不可用，请稍后重试"},
        )
        self.assertEqual(app.state.database_status, "degraded")
        self.assertEqual(app.state.database_error, "database stopped")

    def test_asyncpg_connection_error_is_mapped_to_503(self):
        app = build_test_app(
            asyncpg.ConnectionDoesNotExistError("connection was closed")
        )

        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/probe")

        self.assertEqual(response.status_code, 503)

    def test_existing_http_exception_keeps_its_status(self):
        app = build_test_app(HTTPException(status_code=409, detail="busy"))

        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/probe")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {"detail": "busy"})

    def test_unrelated_programming_error_is_not_hidden_as_503(self):
        app = build_test_app(RuntimeError("bug"))

        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/probe")

        self.assertEqual(response.status_code, 500)


if __name__ == "__main__":
    unittest.main()
