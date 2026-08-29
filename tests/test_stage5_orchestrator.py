import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.chat.orchestrator import ManualChatOrchestrator
from domain.chat.routing import (
    ResolvedChatMode,
    RouteDecision,
    RouteSource,
)
from domain.rag.hybrid_models import HybridAnswer, HybridAnswerStatus
from domain.rag.models import RagAnswer, RagAnswerStatus
from infrastructure.config.settings import get_settings
from interfaces.web.chat_schemas import ChatRequest
from domain.memory.context import MemoryContext


class FakePool:
    pass


class FakeOwnedRepository:
    def __init__(self, database, owner_id):
        self.owner_id = owner_id

    async def get_knowledge_base(self, kb_id):
        return {"id": kb_id, "owner_id": self.owner_id}


class FakeSession:
    def __init__(self):
        self.called = False

    def set_agent_mode(self, value):
        self.agent_mode = value

    async def chat_stream(self, text, user_id, conversation_id=None):
        self.called = True
        yield {"type": "token", "text": "普通"}
        yield {"type": "done", "text": "普通回答", "error": ""}


class FakeRagGraph:
    def __init__(self):
        self.called = False

    async def answer(
        self,
        repo,
        knowledge_base_id,
        query,
        top_k,
        conversation_context=None,
        user_memory_context=None,
        owner_id="system",
        conversation_id=None,
    ):
        self.called = True
        return RagAnswer(
            query=query,
            answer="严格知识回答",
            status=RagAnswerStatus.GROUNDED,
            citations=[],
            retrieved_count=1,
            citation_valid=True,
        )


class FakeHybridGraph:
    def __init__(self):
        self.called = False

    async def answer(
        self,
        repo,
        knowledge_base_id,
        query,
        top_k,
        conversation_context=None,
        user_memory_context=None,
        owner_id="system",
        conversation_id=None,
    ):
        self.called = True
        return HybridAnswer(
            query=query,
            answer="混合回答",
            status=HybridAnswerStatus.BLENDED,
            citations=[],
            retrieved_count=1,
            citation_valid=True,
        )


class FakeAutoRouter:
    def __init__(self, mode=ResolvedChatMode.KNOWLEDGE):
        self.mode = mode
        self.calls = 0

    async def route(self, query, owner_id="system"):
        self.calls += 1
        return RouteDecision(
            mode=self.mode,
            source=RouteSource.RULE,
            confidence=0.98,
            reason="测试路由",
        )


class Stage5OrchestratorTests(unittest.IsolatedAsyncioTestCase):
    def build(self, rag=None, hybrid=None, router=None):
        return ManualChatOrchestrator(
            database=SimpleNamespace(pool=FakePool()),
            rag_graph=rag,
            settings=get_settings(),
            hybrid_graph=hybrid,
            auto_router=router,
        )

    async def collect(self, orchestrator, request, session):
        return [
            event
            async for event in orchestrator.run(
                request,
                session,
                "user-a",
            )
        ]

    async def test_manual_normal_never_calls_auto_router(self):
        router = FakeAutoRouter()
        session = FakeSession()
        events = await self.collect(
            self.build(router=router),
            ChatRequest(text="你好", mode="normal", conversation_id=uuid4()),
            session,
        )
        self.assertTrue(session.called)
        self.assertEqual(router.calls, 0)
        self.assertEqual(events[-1]["requested_mode"], "normal")
        self.assertEqual(events[-1]["mode"], "normal")

    async def test_manual_hybrid_only_calls_hybrid_graph(self):
        rag = FakeRagGraph()
        hybrid = FakeHybridGraph()
        router = FakeAutoRouter()
        session = FakeSession()
        request = ChatRequest(
            text="结合资料回答",
            mode="hybrid",
            knowledge_base_id=uuid4(), conversation_id=uuid4(),
        )
        with patch(
            "app.chat.orchestrator.PgKnowledgeRepository",
            FakeOwnedRepository,
        ):
            orchestrator = self.build(rag, hybrid, router)
            with patch.object(
                orchestrator,
                "_build_memory_context",
                new=AsyncMock(
                    return_value=(
                        MemoryContext(
                            user_id="user-a",
                            conversation_id=request.conversation_id,
                            mode="knowledge",
                            knowledge_base_id=request.knowledge_base_id,
                            user_memory_context="",
                        ),
                        [],
                    )
                ),
            ):
                events = await self.collect(
                    orchestrator,
                    request,
                    session,
                )

        self.assertTrue(hybrid.called)
        self.assertFalse(rag.called)
        self.assertFalse(session.called)
        self.assertEqual(router.calls, 0)
        self.assertEqual(events[0]["mode"], "hybrid")

    async def test_auto_knowledge_calls_rag_with_route_metadata(self):
        rag = FakeRagGraph()
        hybrid = FakeHybridGraph()
        router = FakeAutoRouter(ResolvedChatMode.KNOWLEDGE)
        session = FakeSession()
        request = ChatRequest(
            text="资料问题",
            mode="auto",
            knowledge_base_id=uuid4(), conversation_id=uuid4(),
        )
        with patch(
            "app.chat.orchestrator.PgKnowledgeRepository",
            FakeOwnedRepository,
        ):
            orchestrator = self.build(rag, hybrid, router)
            with patch.object(
                orchestrator,
                "_build_memory_context",
                new=AsyncMock(
                    return_value=(
                        MemoryContext(
                            user_id="user-a",
                            conversation_id=request.conversation_id,
                            mode="knowledge",
                            knowledge_base_id=request.knowledge_base_id,
                            user_memory_context="",
                        ),
                        [],
                    )
                ),
            ):
                events = await self.collect(
                    orchestrator,
                    request,
                    session,
                )

        self.assertTrue(rag.called)
        self.assertFalse(hybrid.called)
        self.assertFalse(session.called)
        self.assertEqual(router.calls, 1)
        self.assertEqual(events[0]["requested_mode"], "auto")
        self.assertEqual(events[0]["mode"], "knowledge")
        self.assertEqual(events[0]["route_source"], "rule")
        self.assertEqual(events[0]["route_reason"], "测试路由")


if __name__ == "__main__":
    unittest.main()
