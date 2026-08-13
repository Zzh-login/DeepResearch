import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from app.chat.orchestrator import ChatProtocolError, ManualChatOrchestrator
from domain.rag.models import Citation, RagAnswer, RagAnswerStatus
from infrastructure.config.settings import get_settings
from interfaces.web.chat_schemas import ChatRequest


class FakeSession:
    def __init__(self):
        self.called = False
        self.agent_mode = None

    def set_agent_mode(self, value):
        self.agent_mode = value

    async def chat_stream(self, text, user_id):
        self.called = True
        yield {"type": "token", "text": "普通"}
        yield {"type": "done", "text": "普通回答", "error": ""}


class FakeRagGraph:
    def __init__(self):
        self.called = False

    async def answer(self, repo, knowledge_base_id, query, top_k):
        self.called = True
        return RagAnswer(
            query=query,
            answer="知识库回答",
            status=RagAnswerStatus.GROUNDED,
            citations=[],
            retrieved_count=1,
            citation_valid=True,
        )


class FakePool:
    pass


class FakeOwnedRepository:
    def __init__(self, database, owner_id):
        self.database = database
        self.owner_id = owner_id

    async def get_knowledge_base(self, kb_id):
        return {"id": kb_id, "owner_id": self.owner_id, "name": "测试库"}


class FakeMissingRepository(FakeOwnedRepository):
    async def get_knowledge_base(self, kb_id):
        return None


class FakeRefusedRepository(FakeOwnedRepository):
    async def get_knowledge_base(self, kb_id):
        raise ConnectionRefusedError("database stopped")


def _answer_with_citation(chunk_id, document_id):
    async def answer(repo, knowledge_base_id, query, top_k):
        return RagAnswer(
            query=query,
            answer="知识库回答 [S1]",
            status=RagAnswerStatus.GROUNDED,
            citations=[
                Citation(
                    source_id="S1",
                    chunk_id=chunk_id,
                    document_id=document_id,
                    chunk_index=0,
                    filename="test.txt",
                    excerpt="测试证据",
                    score=0.88,
                )
            ],
            retrieved_count=1,
            citation_valid=True,
        )

    return answer


class ManualChatOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_normal_mode_calls_session_and_never_calls_rag(self):
        rag = FakeRagGraph()
        orchestrator = ManualChatOrchestrator(
            database=SimpleNamespace(pool=FakePool()),
            rag_graph=rag,
            settings=get_settings(),
        )
        session = FakeSession()
        request = ChatRequest(text="你好", mode="normal")

        events = [
            item
            async for item in orchestrator.run(request, session, "user-a")
        ]

        self.assertTrue(session.called)
        self.assertFalse(rag.called)
        self.assertEqual(
            [item["type"] for item in events],
            ["chat.token", "chat.done"],
        )

    async def test_disabled_mode_is_rejected_before_backend_call(self):
        rag = FakeRagGraph()
        orchestrator = ManualChatOrchestrator(
            database=SimpleNamespace(pool=FakePool()),
            rag_graph=rag,
            settings=get_settings(),
        )
        session = FakeSession()
        request = ChatRequest(text="研究这个问题", mode="deep_research")

        with self.assertRaises(ChatProtocolError) as context:
            _ = [
                item
                async for item in orchestrator.run(
                    request,
                    session,
                    "user-a",
                )
            ]

        self.assertEqual(context.exception.code, "mode_not_available")
        self.assertFalse(session.called)
        self.assertFalse(rag.called)

    async def test_database_offline_does_not_call_chat_session(self):
        orchestrator = ManualChatOrchestrator(
            database=SimpleNamespace(pool=None),
            rag_graph=FakeRagGraph(),
            settings=get_settings(),
        )
        session = FakeSession()
        request = ChatRequest(
            text="文档说了什么",
            mode="knowledge",
            knowledge_base_id=uuid4(),
        )

        with self.assertRaises(ChatProtocolError) as context:
            _ = [
                item
                async for item in orchestrator.run(
                    request,
                    session,
                    "user-a",
                )
            ]

        self.assertEqual(context.exception.code, "database_unavailable")
        self.assertFalse(session.called)

    async def test_knowledge_mode_calls_rag_and_not_session(self):
        kb_id = uuid4()
        chunk_id = uuid4()
        document_id = uuid4()
        rag = FakeRagGraph()
        rag.answer = _answer_with_citation(chunk_id, document_id)

        with patch(
            "app.chat.orchestrator.PgKnowledgeRepository",
            FakeOwnedRepository,
        ):
            orchestrator = ManualChatOrchestrator(
                database=SimpleNamespace(pool=FakePool()),
                rag_graph=rag,
                settings=get_settings(),
            )
            session = FakeSession()
            request = ChatRequest(
                text="文档说了什么",
                mode="knowledge",
                knowledge_base_id=kb_id,
            )
            events = [
                item
                async for item in orchestrator.run(
                    request,
                    session,
                    "user-a",
                )
            ]

        self.assertFalse(session.called)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "chat.done")
        self.assertEqual(events[0]["mode"], "knowledge")
        self.assertEqual(
            events[0]["citations"][0]["chunk_id"],
            str(chunk_id),
        )
        self.assertEqual(
            events[0]["citations"][0]["document_id"],
            str(document_id),
        )

    async def test_unowned_knowledge_base_is_rejected(self):
        with patch(
            "app.chat.orchestrator.PgKnowledgeRepository",
            FakeMissingRepository,
        ):
            orchestrator = ManualChatOrchestrator(
                database=SimpleNamespace(pool=FakePool()),
                rag_graph=FakeRagGraph(),
                settings=get_settings(),
            )
            session = FakeSession()
            request = ChatRequest(
                text="读取别人的资料",
                mode="knowledge",
                knowledge_base_id=uuid4(),
            )

            with self.assertRaises(ChatProtocolError) as context:
                _ = [
                    item
                    async for item in orchestrator.run(
                        request,
                        session,
                        "user-b",
                    )
                ]

        self.assertEqual(
            context.exception.code,
            "knowledge_base_not_found",
        )
        self.assertFalse(session.called)

    async def test_connection_refused_maps_to_database_unavailable(self):
        with patch(
            "app.chat.orchestrator.PgKnowledgeRepository",
            FakeRefusedRepository,
        ):
            orchestrator = ManualChatOrchestrator(
                database=SimpleNamespace(pool=FakePool()),
                rag_graph=FakeRagGraph(),
                settings=get_settings(),
            )
            session = FakeSession()
            request = ChatRequest(
                text="数据库中断了",
                mode="knowledge",
                knowledge_base_id=uuid4(),
            )

            with self.assertRaises(ChatProtocolError) as context:
                _ = [
                    item
                    async for item in orchestrator.run(
                        request,
                        session,
                        "user-a",
                    )
                ]

        self.assertEqual(context.exception.code, "database_unavailable")
        self.assertFalse(session.called)


if __name__ == "__main__":
    unittest.main()