import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT / "interfaces" / "web" / "conversation_routes.py"
).read_text(encoding="utf-8")


class ConversationRoutesTests(unittest.TestCase):
    def test_router_prefix_and_handlers(self):
        self.assertIn('prefix="/api/conversations"', SOURCE)
        for name in (
            "create_conversation",
            "list_conversations",
            "list_messages",
            "rename_conversation",
            "delete_conversation",
        ):
            self.assertIn(f"def {name}", SOURCE)

    def test_owner_isolation_via_current_user(self):
        self.assertIn("get_current_user", SOURCE)
        self.assertIn("PgConversationRepository", SOURCE)

    def test_database_failure_maps_to_503(self):
        self.assertIn("ConversationDatabaseRoute", SOURCE)
        self.assertIn("503", SOURCE)

    def test_websocket_does_not_replay_user_global_history(self):
        app_source = (
            ROOT / "interfaces" / "web" / "app.py"
        ).read_text(encoding="utf-8")
        websocket_block = app_source.split(
            '@app.websocket("/ws")',
            1,
        )[1].split(
            '@app.websocket("/ws/asr")',
            1,
        )[0]
        self.assertNotIn(
            "for message in session.get_history()",
            websocket_block,
        )
        self.assertIn("conversation_id", websocket_block)
