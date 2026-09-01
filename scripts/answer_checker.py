#!/usr/bin/env python
"""独立答案校验：只执行测试，不调用任何 LLM。

用于快速验证"这段代码到底过不过"，以及单独调试某个题集用例。

    python scripts/answer_checker.py --id medium-002
    python scripts/answer_checker.py --id medium-002 --suite adversarial
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.datasets import load_all, load_by_id  # noqa: E402
from app.evaluator.splitter import extract_code, split_sections  # noqa: E402
from app.llm.mock import MockLLM  # noqa: E402
from app.sandbox.runner import run_suite  # noqa: E402
from app.solver.solver import Solver  # noqa: E402

_STATUS_CN = {
    "ok": "通过",
    "wrong_answer": "答案错误",
    "timeout": "超时",
    "runtime_error": "运行时错误",
    "import_error": "载入失败",
    "missing_entry": "缺少入口函数",
    "not_run": "未执行",
    "unserializable": "返回值无法序列化",
}


def main() -> int:
    ap = argparse.ArgumentParser(description="执行测试并校验答案")
    ap.add_argument("--id", required=True)
    ap.add_argument("--suite", choices=["public", "adversarial", "both"], default="both")
    args = ap.parse_args()

    problem = load_by_id(args.id)
    if problem is None:
        print(f"未找到 id={args.id}；可用：{[p['id'] for p in load_all()]}", file=sys.stderr)
        return 1

    # 这里只关心代码，不需要 Critic，用 Mock 后端取预置解答即可
    raw = Solver(MockLLM()).solve(problem)
    parsed = split_sections(raw)
    code = extract_code(parsed.get("代码实现").content if parsed.get("代码实现") else raw)
    if not code:
        code = extract_code(raw)
    entry = problem["entry_point"]

    print(f"题目：{problem['id']} {problem['title']}（入口 {entry}）\n")

    suites = (
        [("public", problem.get("public_tests", [])), ("adversarial", problem.get("adversarial_tests", []))]
        if args.suite == "both"
        else [(args.suite, problem.get(f"{args.suite}_tests", []))]
    )

    for name, tests in suites:
        suite = run_suite(code, entry, tests)
        print(f"=== {name}：{suite.passed}/{suite.total} 通过 ===")
        for r in suite.results:
            mark = "PASS" if r.passed else "FAIL"
            line = f"  [{mark}] {_STATUS_CN.get(r.status, r.status)}  args={r.args}"
            if r.status == "wrong_answer":
                line += f"  expected={r.expected!r} actual={r.actual!r}"
            if r.error:
                line += f"  {r.error[:120]}"
            print(line)
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
