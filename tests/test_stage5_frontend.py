import unittest
from pathlib import Path


INDEX_PATH = (
    Path(__file__).resolve().parents[1]
    / "interfaces"
    / "web"
    / "frontend"
    / "index.html"
)


class Stage5FrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_PATH.read_text(encoding="utf-8")

    def test_initialization_loads_kb_for_every_kb_mode(self):
        init_block = self.html.split("async function init()", 1)[1]
        init_block = init_block.split("\n}\n\ninit();", 1)[0]
        self.assertIn(
            '["auto", "knowledge", "hybrid", "deep_research"].includes(activeChatMode)',
            init_block,
        )
        self.assertIn("await loadKnowledgeBases();", init_block)

    def test_single_knowledge_base_is_selected_automatically(self):
        load_block = self.html.split(
            "async function loadKnowledgeBases()",
            1,
        )[1]
        load_block = load_block.split("\n}\n\nfunction applyChatMode", 1)[0]
        self.assertIn("knowledgeBases.length === 1", load_block)
        self.assertIn(
            'localStorage.setItem("knowledgeBaseId", activeKnowledgeBaseId)',
            load_block,
        )

    def test_auto_route_note_is_inside_done_handler(self):
        done_block = self.html.split(
            'if (data.type === "chat.done")',
            1,
        )[1]
        done_block = done_block.split(
            'if (data.type === "chat.error")',
            1,
        )[0]
        self.assertIn('data.requested_mode === "auto"', done_block)
        self.assertIn("routeNote", done_block)

    def test_hybrid_answers_render_citation_cards(self):
        done_block = self.html.split(
            'if (data.type === "chat.done")',
            1,
        )[1]
        done_block = done_block.split(
            'if (data.type === "chat.error")',
            1,
        )[0]
        self.assertIn('["knowledge", "hybrid"].includes(data.mode)', done_block)
        self.assertIn("appendCitationCards", done_block)


if __name__ == "__main__":
    unittest.main()
