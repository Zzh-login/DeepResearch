import unittest
from pathlib import Path


HTML = (
    Path(__file__).resolve().parents[1]
    / "interfaces" / "web" / "frontend" / "index.html"
).read_text(encoding="utf-8")


class Stage7FrontendTests(unittest.TestCase):
    def test_conversation_is_sent_with_chat(self):
        self.assertIn("conversation_id: activeConversationId", HTML)

    def test_research_uses_idempotency_key(self):
        self.assertIn('"Idempotency-Key": pendingResearchKey', HTML)
        self.assertIn("crypto.randomUUID()", HTML)

    def test_plan_is_rendered_with_text_content(self):
        self.assertIn("researchSubtasks.textContent", HTML)
        self.assertIn("item.textContent", HTML)

    def test_init_restores_conversation_before_research(self):
        block = HTML.split("async function init()", 1)[1]
        self.assertLess(
            block.index("await ensureConversation()"),
            block.index("await openConversation(activeConversationId)"),
        )

    def test_research_restore_is_conversation_scoped(self):
        self.assertIn("research_task_id", HTML)
        self.assertIn("restoreResearchForConversation(items)", HTML)
        self.assertNotIn('localStorage.getItem("lastResearchTaskId")', HTML)
        self.assertNotIn('localStorage.setItem("lastResearchTaskId", task.id)', HTML)

    def test_old_research_history_is_selectable(self):
        self.assertIn("researchHistorySelect", HTML)
        self.assertIn("loadResearchHistory", HTML)
        self.assertIn("openResearchHistory", HTML)
