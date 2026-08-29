import unittest

from app.chat.memory_policy import build_memory_policy


class MemoryModeIsolationTests(unittest.TestCase):
    def test_normal_policy(self):
        policy = build_memory_policy("normal")

        self.assertTrue(policy["conversation"])
        self.assertTrue(policy["user"])
        self.assertFalse(policy["knowledge"])
        self.assertFalse(policy["research"])

    def test_knowledge_policy_does_not_read_user_facts(self):
        policy = build_memory_policy("knowledge")

        self.assertTrue(policy["conversation"])
        self.assertFalse(policy["user"])
        self.assertTrue(policy["knowledge"])
        self.assertFalse(policy["research"])

    def test_hybrid_policy(self):
        policy = build_memory_policy("hybrid")

        self.assertTrue(policy["conversation"])
        self.assertTrue(policy["user"])
        self.assertTrue(policy["knowledge"])
        self.assertFalse(policy["research"])

    def test_research_policy(self):
        policy = build_memory_policy("deep_research")

        self.assertTrue(policy["conversation"])
        self.assertTrue(policy["user"])
        self.assertTrue(policy["knowledge"])
        self.assertTrue(policy["research"])

    def test_unknown_mode_rejected(self):
        with self.assertRaises(ValueError):
            build_memory_policy("unknown")