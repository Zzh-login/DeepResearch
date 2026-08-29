import unittest
from pathlib import Path


HTML_PATH = (
    Path(__file__).resolve().parents[1]
    / "interfaces" / "web" / "frontend" / "index.html"
)


class Stage6FrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = HTML_PATH.read_text(encoding="utf-8")

    def test_deep_research_option_is_enabled(self):
        self.assertIn('<option value="deep_research">深度研究</option>', self.html)
        self.assertIn('"deep_research"', self.html)
        self.assertIn('"deep_research"\n]);', self.html)

    def test_research_uses_rest_not_chat_websocket(self):
        block = self.html.split("async function sendText(text)", 1)[1]
        deep_branch = block.split("if (!ws ||", 1)[0]
        self.assertIn("await startResearch(text)", deep_branch)
        self.assertNotIn("ws.send", deep_branch)

    def test_deep_research_does_not_require_kb(self):
        block = self.html.split("async function sendText(text)", 1)[1]
        required = block.split("if (currentAudio)", 1)[0]
        self.assertIn('["auto", "knowledge", "hybrid"]', required)
        self.assertNotIn('["auto", "knowledge", "hybrid", "deep_research"]', required)

    def test_refresh_restores_research_task_for_active_conversation(self):
        self.assertIn("async function restoreResearchForConversation(items)", self.html)
        self.assertIn("await restoreResearchForConversation(items);", self.html)
        self.assertNotIn('localStorage.getItem("lastResearchTaskId")', self.html)
        self.assertNotIn("async function loadResearchTasks()", self.html)

    def test_completed_report_does_not_create_global_restore_id(self):
        completed_block = self.html.split('if (task.status === "completed")', 1)[1]
        completed_block = completed_block.split(
            'if (["failed", "cancelled"].includes(task.status))', 1
        )[0]
        self.assertNotIn('localStorage.setItem("lastResearchTaskId", task.id)', completed_block)

    def test_research_panel_is_cleared_when_conversation_changes(self):
        self.assertIn("clearResearchPanel();", self.html)
        self.assertIn("await restoreResearchForConversation(items);", self.html)

    def test_research_history_dropdown_loads_and_opens_task_details(self):
        self.assertIn('id="researchHistorySelect"', self.html)
        self.assertIn("async function loadResearchHistory()", self.html)
        self.assertIn('fetch("/api/research-tasks"', self.html)
        self.assertIn("async function openResearchHistory(taskId)", self.html)
        self.assertIn("await pollResearchTask();", self.html)

    def test_research_history_is_only_visible_in_research_mode(self):
        mode_block = self.html.split("function applyChatMode(mode)", 1)[1]
        mode_block = mode_block.split("async function", 1)[0]
        self.assertIn("researchHistoryWrap.hidden = true", mode_block)
        self.assertIn("researchHistoryWrap.hidden = false", mode_block)

    def test_research_history_can_be_deleted(self):
        self.assertIn('id="deleteResearchHistoryBtn"', self.html)
        self.assertIn("async function deleteResearchHistory()", self.html)
        self.assertIn('method: "DELETE"', self.html)
        self.assertIn("研究历史已删除", self.html)

    def test_non_research_mode_hides_research_panel(self):
        mode_block = self.html.split("function applyChatMode(mode)", 1)[1]
        mode_block = mode_block.split("async function", 1)[0]
        self.assertIn('if (mode !== "deep_research")', mode_block)
        self.assertIn("clearResearchPanel();", mode_block)

    def test_terminal_states_stop_polling(self):
        self.assertIn('task.status === "failed"', self.html)
        self.assertIn('task.status === "cancelled"', self.html)
        self.assertIn("stopResearchPolling();", self.html)
        self.assertIn('task.status === "completed"', self.html)

    def test_research_poll_ignores_stale_response_after_switch(self):
        poll_block = self.html.split("async function pollResearchTask", 1)[1]
        poll_block = poll_block.split('\ncancelResearchBtn.addEventListener', 1)[0]
        self.assertIn("taskId = activeResearchTaskId", poll_block)
        self.assertIn("activeResearchTaskId !== taskId", poll_block)
        self.assertIn("() => pollResearchTask(taskId)", poll_block)

    def test_failed_task_reason_is_rendered_in_report_area(self):
        self.assertIn("function renderResearchFailure(message)", self.html)
        self.assertIn('title.textContent = "执行失败"', self.html)
        self.assertIn("失败原因：", self.html)
        self.assertIn('if (task.status === "failed")', self.html)

    def test_sources_are_rendered_safely(self):
        self.assertIn("title.textContent", self.html)
        self.assertIn("excerpt.textContent", self.html)
        self.assertIn('link.rel = "noopener noreferrer"', self.html)
        self.assertIn("safeResearchUrl", self.html)

    def test_research_does_not_lock_normal_chat(self):
        deep_branch = self.html.split(
            'if (activeChatMode === "deep_research")', 1
        )[1].split("return;", 1)[0]
        self.assertNotIn("responseInProgress = true", deep_branch)
        self.assertNotIn("chatModeSelect.disabled = true", deep_branch)

    def test_input_is_above_research_panel(self):
        self.assertLess(
            self.html.index('class="input-row"'),
            self.html.index('id="researchPanel"'),
        )
