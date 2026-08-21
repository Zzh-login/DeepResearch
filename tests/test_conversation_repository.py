import inspect
import unittest

from infrastructure.conversation.pg_repository import PgConversationRepository


class ConversationRepositoryTests(unittest.TestCase):
    def test_has_owner_scoped_crud_methods(self):
        for name in (
            "create",
            "get_owned",
            "list",
            "rename",
            "delete",
            "add_message",
            "list_messages",
        ):
            self.assertTrue(hasattr(PgConversationRepository, name), name)

    def test_queries_filter_by_owner(self):
        source = inspect.getsource(PgConversationRepository)
        self.assertIn("owner_id=$2", source)

    def test_add_message_is_transactional_and_writes_citations(self):
        source = inspect.getsource(PgConversationRepository.add_message)
        self.assertIn("conn.transaction()", source)
        self.assertIn("message_citations", source)
        self.assertIn("route_metadata", source)

    def test_list_messages_aggregates_citations(self):
        source = inspect.getsource(PgConversationRepository.list_messages)
        self.assertIn("jsonb_agg", source)
        self.assertIn("message_citations", source)
