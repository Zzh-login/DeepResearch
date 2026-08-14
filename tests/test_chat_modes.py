import unittest
from uuid import uuid4

from pydantic import ValidationError

from domain.chat.modes import ChatMode, is_chat_mode_enabled
from interfaces.web.chat_schemas import ChatRequest


class ChatModeSchemaTests(unittest.TestCase):
    def test_enabled_modes_are_explicit(self):
        self.assertTrue(is_chat_mode_enabled(ChatMode.NORMAL))
        self.assertTrue(is_chat_mode_enabled(ChatMode.KNOWLEDGE))
        self.assertTrue(is_chat_mode_enabled(ChatMode.HYBRID))
        self.assertTrue(is_chat_mode_enabled(ChatMode.AUTO))
        self.assertFalse(is_chat_mode_enabled(ChatMode.DEEP_RESEARCH))

    def test_normal_discards_stale_knowledge_base_id(self):
        request = ChatRequest.model_validate(
            {
                "text": "你好",
                "mode": "normal",
                "knowledge_base_id": str(uuid4()),
            }
        )
        self.assertIsNone(request.knowledge_base_id)

    def test_knowledge_requires_knowledge_base_id(self):
        with self.assertRaises(ValidationError):
            ChatRequest.model_validate(
                {"text": "问题", "mode": "knowledge"}
            )

    def test_hybrid_requires_knowledge_base_id(self):
        with self.assertRaises(ValidationError):
            ChatRequest.model_validate(
                {"text": "问题", "mode": "hybrid"}
            )

    def test_auto_requires_knowledge_base_id(self):
        with self.assertRaises(ValidationError):
            ChatRequest.model_validate(
                {"text": "问题", "mode": "auto"}
            )

    def test_empty_text_is_rejected_after_strip(self):
        with self.assertRaises(ValidationError):
            ChatRequest.model_validate(
                {"text": "   ", "mode": "normal"}
            )

    def test_invalid_tts_mode_is_rejected(self):
        with self.assertRaises(ValidationError):
            ChatRequest.model_validate(
                {"text": "你好", "mode": "normal", "tts_mode": "bad"}
            )


if __name__ == "__main__":
    unittest.main()