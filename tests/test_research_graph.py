import inspect
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.research.graph import (
    DeepResearchGraph,
    MAX_CITATION_REPAIRS,
    REPORT_PROMPT,
    REPAIR_PROMPT,
    _citation_ids,
    _parse_plan_json,
    _parse_report_json,
    _render_markdown,
    _report_issues,
)
from domain.research.report_schema import ResearchReport


def _block(
    block_id,
    section,
    kind,
    text="",
    citation_ids=None,
    based_on=None,
    columns=None,
    rows=None,
):
    return {
        "id": block_id,
        "section": section,
        "kind": kind,
        "text": text,
        "citation_ids": citation_ids or [],
        "based_on_block_ids": based_on or [],
        "columns": columns or [],
        "rows": rows or [],
    }


def _valid_blocks():
    return [
        _block("s1", "summary", "synthesis", "摘要概述", based_on=["f1"]),
        _block("f1", "findings", "evidence", "主要发现事实", citation_ids=["W1"]),
        _block("u1", "uncertainties", "limitation", "局限说明"),
        _block("c1", "conclusion", "synthesis", "结论", based_on=["f1"]),
    ]


def _report(blocks):
    return ResearchReport.model_validate(
        {"schema_version": 1, "blocks": blocks}
    )


class ResearchGraphTests(unittest.TestCase):
    # ---- 计划解析（沿用） ----
    def test_plan_parser_accepts_json_fence(self):
        data = _parse_plan_json(
            '```json\n{"queries":["a","b","c"],"scope":"s"}\n```'
        )
        self.assertEqual(data["queries"], ["a", "b", "c"])

    def test_plan_parser_rejects_missing_object(self):
        with self.assertRaises(RuntimeError):
            _parse_plan_json("不是 JSON")

    # ---- _parse_report_json ----
    def test_parse_report_json_accepts_valid(self):
        payload = {"schema_version": 1, "blocks": _valid_blocks()}
        report = _parse_report_json(json.dumps(payload, ensure_ascii=False))
        self.assertEqual(len(report.blocks), 4)

    def test_parse_report_json_strips_code_fence(self):
        payload = {"schema_version": 1, "blocks": _valid_blocks()}
        text = "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
        report = _parse_report_json(text)
        self.assertEqual(len(report.blocks), 4)

    def test_parse_report_json_rejects_non_json(self):
        with self.assertRaises(RuntimeError):
            _parse_report_json("这是一段 Markdown，不是 JSON")

    def test_parse_report_json_rejects_missing_section(self):
        blocks = _valid_blocks()[:-1]  # 去掉 conclusion
        payload = {"schema_version": 1, "blocks": blocks}
        with self.assertRaises(RuntimeError):
            _parse_report_json(json.dumps(payload, ensure_ascii=False))

    # ---- _report_issues ----
    def test_valid_report_has_no_issue(self):
        report = _report(_valid_blocks())
        self.assertEqual(_report_issues(report, {"W1"}), [])

    def test_evidence_without_citation_is_flagged(self):
        blocks = _valid_blocks()
        blocks[1] = _block("f1", "findings", "evidence", "没有引用的事实")
        issues = _report_issues(_report(blocks), {"W1"})
        self.assertTrue(any("缺少引用" in item for item in issues))

    def test_unknown_citation_is_rejected(self):
        blocks = _valid_blocks()
        blocks[1] = _block(
            "f1", "findings", "evidence", "引用不存在", citation_ids=["W99"]
        )
        issues = _report_issues(_report(blocks), {"W1"})
        self.assertTrue(any("无效引用" in item for item in issues))

    def test_limitation_without_citation_is_ok(self):
        # 关键的“诚实说明不罚”场景：局限/不确定性无需引用
        report = _report(_valid_blocks())
        self.assertEqual(_report_issues(report, {"W1"}), [])

    def test_synthesis_requires_based_on(self):
        blocks = _valid_blocks()
        blocks[0] = _block("s1", "summary", "synthesis", "没有依据的综合判断")
        issues = _report_issues(_report(blocks), {"W1"})
        self.assertTrue(any("based_on" in item for item in issues))

    def test_synthesis_referencing_unknown_block(self):
        blocks = _valid_blocks()
        blocks[0] = _block(
            "s1", "summary", "synthesis", "指向不存在块", based_on=["ghost"]
        )
        issues = _report_issues(_report(blocks), {"W1"})
        self.assertTrue(any("不存在的依据块" in item for item in issues))

    def test_synthesis_without_any_cited_evidence_is_flagged(self):
        blocks = _valid_blocks()
        blocks[1] = _block("f1", "findings", "evidence", "没有引用的事实")
        issues = _report_issues(_report(blocks), {"W1"})
        self.assertTrue(any("未指向任何带引用的证据块" in item for item in issues))

    def test_synthesis_can_reference_limitation_and_evidence(self):
        blocks = [
            _block("f1", "findings", "evidence", "事实", citation_ids=["W1"]),
            _block("u1", "uncertainties", "limitation", "局限"),
            _block("s1", "summary", "synthesis", "综合", based_on=["f1", "u1"]),
            _block("c1", "conclusion", "synthesis", "结论", based_on=["f1", "u1"]),
        ]
        self.assertEqual(_report_issues(_report(blocks), {"W1"}), [])

    def test_table_without_citation_is_flagged(self):
        blocks = _valid_blocks()
        blocks[1] = _block(
            "f1",
            "findings",
            "table",
            columns=["模型", "特点"],
            rows=[["A", "快"]],
        )
        issues = _report_issues(_report(blocks), {"W1"})
        self.assertTrue(any("缺少引用" in item for item in issues))

    def test_report_without_any_citation(self):
        blocks = _valid_blocks()
        blocks[1] = _block("f1", "findings", "evidence", "无引用")
        issues = _report_issues(_report(blocks), set())
        self.assertTrue(any("报告没有引用" in item for item in issues))

    # ---- _render_markdown / _citation_ids ----
    def test_render_markdown_has_all_sections(self):
        report = _report(_valid_blocks())
        markdown = _render_markdown(report)
        for title in ("## 摘要", "## 主要发现", "## 分歧与不确定性", "## 结论"):
            self.assertIn(title, markdown)

    def test_render_markdown_renders_table_with_source(self):
        blocks = _valid_blocks()
        blocks[1] = _block(
            "f1",
            "findings",
            "table",
            columns=["模型", "特点"],
            rows=[["A", "快"]],
            citation_ids=["W1"],
        )
        markdown = _render_markdown(_report(blocks))
        self.assertIn("| 模型 | 特点 |", markdown)
        self.assertIn("数据来源：[W1]", markdown)

    def test_citation_ids_dedup_and_order(self):
        blocks = _valid_blocks()
        blocks[1] = _block(
            "f1", "findings", "evidence", "事实", citation_ids=["W2", "W1", "W2"]
        )
        self.assertEqual(_citation_ids(_report(blocks)), ["W2", "W1"])

    # ---- 提示词 / 图结构约束 ----
    def test_prompts_require_json_structure(self):
        for prompt in (REPORT_PROMPT, REPAIR_PROMPT):
            self.assertIn("JSON", prompt)
        self.assertIn("schema_version", REPORT_PROMPT)
        self.assertIn("citation_ids", REPORT_PROMPT)
        self.assertIn("based_on_block_ids", REPORT_PROMPT)

    def test_deep_research_injects_model_gateway_into_retrieval(self):
        gateway = object()
        graph = DeepResearchGraph(
            SimpleNamespace(rag_min_score=0.45),
            model=object(),
            model_gateway=gateway,
        )
        self.assertIs(graph._retrieval._model_gateway, gateway)

    def test_deep_research_local_retrieval_uses_research_context(self):
        graph = object.__new__(DeepResearchGraph)
        graph._settings = SimpleNamespace(
            rag_top_k=5,
            research_model_input_cost_per_1k_usd=0.0,
            research_model_output_cost_per_1k_usd=0.0,
        )
        graph._retrieval = SimpleNamespace(retrieve=AsyncMock(return_value=[]))
        state = {
            "task_id": "task",
            "owner_id": "owner-a",
            "query": "研究问题",
            "knowledge_base_id": "kb",
            "knowledge_repo": object(),
            "repo": SimpleNamespace(
                update_progress=AsyncMock(),
                record_usage=AsyncMock(),
            ),
        }

        # 跳过状态迁移检查，只验证传给检索服务的业务上下文。
        graph._check_cancelled = AsyncMock()
        import asyncio
        asyncio.run(graph._retrieve_local(state))

        kwargs = graph._retrieval.retrieve.await_args.kwargs
        self.assertEqual(kwargs["owner_id"], "owner-a")
        self.assertEqual(kwargs["mode"], "deep_research")
        self.assertEqual(
            kwargs["operation"],
            "deep_research_query_embedding",
        )

    def test_repair_increments_repair_count(self):
        source = inspect.getsource(DeepResearchGraph._repair)
        self.assertIn('state.get("repair_count", 0) + 1', source)

    def test_repair_limit_is_explicit(self):
        self.assertEqual(MAX_CITATION_REPAIRS, 2)

    def test_graph_checks_cancel_before_expensive_steps(self):
        for method_name in (
            "_plan",
            "_retrieve_local",
            "_search_web",
            "_write_report",
            "_repair",
        ):
            with self.subTest(method=method_name):
                source = inspect.getsource(getattr(DeepResearchGraph, method_name))
                self.assertIn("_check_cancelled", source)

    def test_resume_reuses_saved_plan_sources_and_report(self):
        for method_name, marker in (
            ("_plan", "复用已保存研究计划"),
            ("_search_web", "复用已保存来源"),
            ("_write_report", 'state.get("report")'),
        ):
            source = inspect.getsource(getattr(DeepResearchGraph, method_name))
            self.assertIn("resume_state", source)
            self.assertIn(marker, source)

    def test_graph_requires_real_web_source(self):
        source = inspect.getsource(DeepResearchGraph._search_web)
        self.assertIn("if not web_sources", source)
        self.assertIn("replace_sources", source)


if __name__ == "__main__":
    unittest.main()
