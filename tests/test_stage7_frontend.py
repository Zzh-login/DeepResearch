import unittest
from pathlib import Path


HTML = (
    Path(__file__).resolve().parents[1]
    / "interfaces" / "web" / "frontend" / "index.html"
).read_text(encoding="utf-8")


class Stage7FrontendTests(unittest.TestCase):
    def test_conversation_is_sent_with_chat(self):
        self.assertIn("conversation_id: activeConversationId", HTML)

    def test_conversation_title_can_be_renamed(self):
        self.assertIn("async function renameConversation", HTML)
        self.assertIn('method: "PATCH"', HTML)
        self.assertIn("编辑会话名称", HTML)
        self.assertIn("event.stopPropagation()", HTML)

    def test_conversation_can_be_deleted(self):
        self.assertIn("async function deleteConversation", HTML)
        self.assertIn('method: "DELETE"', HTML)
        self.assertIn("删除会话", HTML)
        self.assertIn("clearChatMessages();", HTML)

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

    def test_new_research_resets_previous_content(self):
        self.assertIn("function resetResearchTaskContent()", HTML)
        block = HTML.split("function resetResearchTaskContent()", 1)[1]
        block = block.split("function showResearchTask", 1)[0]
        self.assertIn('researchSources.textContent = ""', block)
        self.assertIn('researchSubtasks.textContent = ""', block)
        self.assertIn("resetResearchTaskContent();", HTML)

    def test_running_snapshot_replaces_sources(self):
        block = HTML.split("function showResearchTask(task)", 1)[1]
        block = block.split("async function startResearch", 1)[0]
        self.assertIn("renderResearchSources(task.sources || [])", block)

    def test_sse_phase_change_refreshes_plan_snapshot(self):
        self.assertIn("researchSnapshotEvents", HTML)
        self.assertIn('"phase.changed"', HTML)
        self.assertIn("refreshResearchTaskSnapshot(taskId)", HTML)
        self.assertIn("researchSnapshotRefreshes.get(taskId)", HTML)

    def test_sse_error_closes_stream_before_polling(self):
        block = HTML.split("researchEventSource.onerror", 1)[1]
        block = block.split("function stopResearchPolling", 1)[0]
        self.assertLess(
            block.index("closeResearchEvents();"),
            block.index("pollResearchTask();"),
        )

    def test_export_and_download_reject_rapid_duplicate_clicks(self):
        self.assertIn("exportRequestsInFlight.has(kind)", HTML)
        self.assertIn("activeExportKinds.has(kind)", HTML)
        self.assertIn("artifactDownloadsInFlight.has(artifact.id)", HTML)
        self.assertIn("downloadArtifactOnce(taskId, art, link)", HTML)
