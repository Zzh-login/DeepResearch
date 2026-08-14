import unittest

from langchain_core.messages import AIMessage

from app.chat.auto_router import AutoChatRouter, AutoRouterError
from domain.chat.routing import ResolvedChatMode, RouteSource
from infrastructure.config.settings import get_settings


class FakeModel:
    def __init__(self, answer: str):
        self.answer = answer
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        return AIMessage(content=self.answer)


class AutoChatRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_hybrid_rule_does_not_call_model(self):
        model = FakeModel("not used")
        router = AutoChatRouter(get_settings(), model=model)
        result = await router.route("结合知识库和你的知识给出建议")
        self.assertEqual(result.mode, ResolvedChatMode.HYBRID)
        self.assertEqual(result.source, RouteSource.RULE)
        self.assertEqual(model.calls, 0)

    async def test_knowledge_rule_does_not_call_model(self):
        model = FakeModel("not used")
        router = AutoChatRouter(get_settings(), model=model)
        result = await router.route("根据文档说明训练集的作用")
        self.assertEqual(result.mode, ResolvedChatMode.KNOWLEDGE)
        self.assertEqual(model.calls, 0)

    async def test_normal_rule_does_not_call_model(self):
        model = FakeModel("not used")
        router = AutoChatRouter(get_settings(), model=model)
        result = await router.route("你好，你是谁")
        self.assertEqual(result.mode, ResolvedChatMode.NORMAL)
        self.assertEqual(model.calls, 0)

    async def test_ambiguous_query_uses_model_once(self):
        model = FakeModel(
            '{"mode":"knowledge","confidence":0.88,"reason":"需要资料事实"}'
        )
        router = AutoChatRouter(get_settings(), model=model)
        result = await router.route("训练集具体有什么作用")
        self.assertEqual(result.mode, ResolvedChatMode.KNOWLEDGE)
        self.assertEqual(result.source, RouteSource.MODEL)
        self.assertEqual(model.calls, 1)

    async def test_markdown_json_is_accepted(self):
        model = FakeModel(
            '```json\n{"mode":"normal","confidence":0.9,"reason":"普通问题"}\n```'
        )
        router = AutoChatRouter(get_settings(), model=model)
        result = await router.route("一个没有规则关键词的问题")
        self.assertEqual(result.mode, ResolvedChatMode.NORMAL)

    async def test_unknown_mode_is_rejected(self):
        model = FakeModel(
            '{"mode":"delete_database","confidence":0.99,"reason":"恶意"}'
        )
        router = AutoChatRouter(get_settings(), model=model)
        with self.assertRaises(AutoRouterError):
            await router.route("模糊问题")

    async def test_low_confidence_is_rejected(self):
        model = FakeModel(
            '{"mode":"normal","confidence":0.2,"reason":"不确定"}'
        )
        router = AutoChatRouter(get_settings(), model=model)
        with self.assertRaises(AutoRouterError):
            await router.route("模糊问题")


if __name__ == "__main__":
    unittest.main()