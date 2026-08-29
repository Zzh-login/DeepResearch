from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from domain.chat.modes import ChatMode
from infrastructure.config.settings import get_settings


def load_cases(path: Path) -> list[dict]:
    cases = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number} 不是有效 JSONL") from exc
        for key in ("id", "mode", "query", "expected"):
            if key not in case:
                raise ValueError(f"{path}:{line_number} 缺少字段 {key}")
        ChatMode(case["mode"])
        cases.append(case)
    if not cases:
        raise ValueError("评测集为空")
    return cases


def check_result(case: dict, result: dict) -> list[str]:
    expected = case["expected"]
    answer = str(result.get("text", ""))
    failures = []
    for item in expected.get("must_contain", []):
        if item not in answer:
            failures.append(f"缺少文本: {item}")
    for item in expected.get("must_not_contain", []):
        if item in answer:
            failures.append(f"出现禁用文本: {item}")
    has_citation = bool(result.get("citations"))
    if expected.get("must_have_citation") is True and not has_citation:
        failures.append("应有引用但 citations 为空")
    if expected.get("must_have_citation") is False and has_citation:
        failures.append("不应有引用但 citations 非空")
    if expected.get("model_supplement_without_citation"):
        marker = "## 模型补充"
        supplement = answer.split(marker, 1)[1] if marker in answer else ""
        if "[S" in supplement:
            failures.append("模型补充包含知识库引用")
    return failures


async def run() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default="evals/stage9_smoke.jsonl")
    parser.add_argument("--out", default="data/evals/stage9_latest.json")
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--knowledge-base-id")
    parser.add_argument("--conversation-id")
    args = parser.parse_args()
    cases = load_cases(Path(args.file))

    from scripts.eval_runtime import answer_case

    settings = get_settings()
    results = []
    for case in cases:
        try:
            result = await answer_case(
                settings=settings,
                query=case["query"],
                mode=ChatMode(case["mode"]),
                owner_id=args.owner_id,
                knowledge_base_id=args.knowledge_base_id,
                conversation_id=args.conversation_id,
            )
            failures = check_result(case, result)
            results.append({
                "id": case["id"],
                "ok": not failures,
                "failures": failures,
                "answer": result.get("text", ""),
                "citations": result.get("citations", []),
            })
        except Exception as exc:
            results.append({
                "id": case["id"],
                "ok": False,
                "failures": [f"{type(exc).__name__}: {exc}"],
                "answer": "",
                "citations": [],
            })

    output = {
        "total": len(results),
        "passed": sum(item["ok"] for item in results),
        "failed": sum(not item["ok"] for item in results),
        "results": results,
    }
    output_path = Path(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
