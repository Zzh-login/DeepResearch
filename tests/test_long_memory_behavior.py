import shutil
import unittest
from uuid import uuid4
from unittest.mock import patch

from app.chat.memory_policy import (
    allowed_user_memory_types,
)
from app.chat.memory_service import UnifiedMemoryService
from infrastructure.memory.json_long_memory import JsonLongMemory


class LongMemoryPolicyTests(unittest.TestCase):
    def setUp(self):
        self.source = f"test_long_memory_{uuid4().hex}"
        self.memory = JsonLongMemory(self.source)
        self.memory.add_memories(
            [
                {
                    "type": "preference",
                    "fact": "用户偏好使用中文回答。",
                    "confidence": 0.9,
                    "importance": 0.8,
                },
                {
                    "type": "project",
                    "fact": "用户正在建设一个本地知识库问答项目。",
                    "confidence": 0.9,
                    "importance": 0.9,
                },
                {
                    "type": "goal",
                    "fact": "用户的长期目标是完成工业级智能问答系统。",
                    "confidence": 0.9,
                    "importance": 0.9,
                },
                {
                    "type": "constraint",
                    "fact": "用户要求系统优先使用本地部署方案。",
                    "confidence": 0.9,
                    "importance": 0.8,
                },
                {
                    "type": "decision",
                    "fact": "用户决定后续使用统一模型网关。",
                    "confidence": 0.9,
                    "importance": 0.7,
                },
            ]
        )

    def tearDown(self):
        shutil.rmtree(self.memory._dir, ignore_errors=True)

    def test_normal_can_read_all_user_memory(self):
        types = allowed_user_memory_types("normal")

        self.assertIn("preference", types)
        self.assertIn("project", types)
        self.assertIn("goal", types)

    def test_knowledge_only_reads_preference(self):
        types = allowed_user_memory_types("knowledge")

        self.assertEqual(types, {"preference"})

    def test_hybrid_only_reads_preference(self):
        types = allowed_user_memory_types("hybrid")

        self.assertEqual(types, {"preference"})

    def test_research_reads_goal_and_constraint(self):
        types = allowed_user_memory_types("deep_research")

        self.assertIn("goal", types)
        self.assertIn("constraint", types)
        self.assertNotIn("decision", types)

    def test_render_context_filters_knowledge_mode(self):
        context = self.memory.render_context(
            allowed_types=allowed_user_memory_types("knowledge")
        )

        self.assertIn("用户偏好使用中文回答", context)
        self.assertNotIn("本地知识库问答项目", context)
        self.assertNotIn("工业级智能问答系统", context)

    def test_render_context_filters_hybrid_mode(self):
        context = self.memory.render_context(
            allowed_types=allowed_user_memory_types("hybrid")
        )

        self.assertIn("用户偏好使用中文回答", context)
        self.assertNotIn("本地知识库问答项目", context)
        self.assertNotIn("统一模型网关", context)

    def test_render_context_allows_research_scope(self):
        context = self.memory.render_context(
            allowed_types=allowed_user_memory_types("deep_research")
        )

        self.assertIn("本地知识库问答项目", context)
        self.assertIn("工业级智能问答系统", context)
        self.assertIn("本地部署方案", context)
        self.assertNotIn("统一模型网关", context)

    def test_unified_memory_service_uses_mode_filter(self):
        with patch(
            "app.chat.memory_service.JsonLongMemory",
            return_value=self.memory,
        ):
            service = UnifiedMemoryService("user-a")
            context = service.render_for_mode("knowledge")

        self.assertIn("用户偏好使用中文回答", context)
        self.assertNotIn("工业级智能问答系统", context)
