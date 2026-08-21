import inspect
import json
import unittest
from pathlib import Path

from infrastructure.conversation.pg_repository import PgConversationRepository
from infrastructure.database.codecs import register_json_codecs
from infrastructure.database.postgres import PostgresDatabase
from infrastructure.research.pg_repository import PgResearchRepository
from infrastructure.memory.kv_repo import KVMemory
from infrastructure.memory.vector_repo import VectorMemory


ROOT = Path(__file__).resolve().parents[1]


class JsonbCodecTests(unittest.IsolatedAsyncioTestCase):
    """验证方案 A：json/jsonb 统一由连接 codec 编解码，仓储不再手动 dumps/loads。"""

    def test_connection_delegates_to_shared_json_codec(self):
        source = inspect.getsource(PostgresDatabase._init_connection)
        self.assertIn("register_json_codecs", source)

    async def test_shared_codec_registers_json_and_jsonb(self):
        class FakeConnection:
            def __init__(self):
                self.calls = []

            async def set_type_codec(self, *args, **kwargs):
                self.calls.append((args, kwargs))

        connection = FakeConnection()
        await register_json_codecs(connection)
        calls = connection.calls
        self.assertEqual([call[0][0] for call in calls], ["json", "jsonb"])
        for _, kwargs in calls:
            self.assertEqual(kwargs["schema"], "pg_catalog")
            encoded = kwargs["encoder"]({"name": "中文", "items": [1, 2]})
            self.assertEqual(json.loads(encoded), {"name": "中文", "items": [1, 2]})
            self.assertEqual(kwargs["decoder"](encoded), {"name": "中文", "items": [1, 2]})

    def test_memory_pools_use_json_codec_init(self):
        self.assertIn("register_json_codecs", inspect.getsource(KVMemory._init_conn))
        self.assertIn("init=self._init_conn", inspect.getsource(KVMemory._get_pool))
        self.assertIn("register_json_codecs", inspect.getsource(VectorMemory._init_conn))

    def test_research_repository_has_no_manual_json(self):
        source = inspect.getsource(PgResearchRepository)
        self.assertNotIn("json.dumps", source)
        self.assertNotIn("json.loads", source)

    def test_conversation_repository_has_no_manual_json(self):
        source = inspect.getsource(PgConversationRepository)
        self.assertNotIn("json.dumps", source)
        self.assertNotIn("json.loads", source)

    def test_conversation_repository_dropped_duplicate_plan_methods(self):
        source = inspect.getsource(PgConversationRepository)
        self.assertNotIn("def replace_plan", source)
        self.assertNotIn("def update_subtask", source)
        self.assertNotIn("def get_plan", source)

    def test_report_is_passed_through_as_object(self):
        # _serialize 直接透传 report（codec 已解码为 dict），不做字符串处理
        routes = (
            ROOT / "interfaces" / "web" / "research_routes.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"report": row.get("report")', routes)
        self.assertNotIn("json.loads", routes)
