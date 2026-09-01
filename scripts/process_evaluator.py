#!/usr/bin/env python
"""独立过程评估：对已有的解题文本做评估，跳过生成阶段。

适合评估人工撰写或外部模型产出的解题过程。

    python scripts/process_evaluator.py --id medium-002
    python scripts/process_evaluator.py --id medium-002 --file path/to/solution.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.datasets import load_all, load_by_id  # noqa: E402
from app.evaluator.critic import Critic  # noqa: E402
from app.evaluator.pipeline import EvaluationResult  # noqa: E402
from app.evaluator.rules import analyze  # noqa: E402
from app.evaluator.splitter import extract_code, split_sections  # noqa: E402
from app.evaluator.taxonomy import label_of  # noqa: E402
from app.llm.factory import get_llm  # noqa: E402
from app.sandbox.runner import run_suite  # noqa: E402


def evaluate_text(problem: dict, raw: str, backend: str = "auto") -> EvaluationResult:
    """对给定解题文本执行完整评估（不含生成）。"""
    llm = get_llm(None if backend == "auto" else backend)
    parsed = split_sections(raw)
    code = extract_code(parsed.get("代码实现").content if parsed.get("代码实现") else raw)
    if not code:
        code = extract_code(raw)

    entry = problem.get("entry_point", "")
    public = run_suite(code, entry, problem.get("public_tests", []))
    adversarial = run_suite(code, entry, problem.get("adversarial_tests", []))
    findings, signals = analyze(problem, parsed, code, public, adversarial)

    from app.config import SECTION_TITLES

    critic = Critic(llm)
    verdicts = [
        critic.review_section(
            section_title=title,
            step_id=(parsed.get(title).step_id if parsed.get(title) else f"step_{i + 1}"),
            section_content=(parsed.get(title).content if parsed.get(title) else ""),
            problem=problem,
            code=code,
            signals=signals,
            rule_findings=findings,
        )
        for i, title in enumerate(SECTION_TITLES)
    ]

    result = EvaluationResult(
        problem_id=problem.get("id", "unknown"),
        difficulty=problem.get("difficulty", ""),
        title=problem.get("title", ""),
        raw_solution=raw,
        sections=parsed.as_dict(),
        code=code,
        public=public,
        adversarial=adversarial,
        rule_findings=findings,
        section_verdicts=verdicts,
    )
    from app.evaluator.pipeline import EvalPipeline

    EvalPipeline._aggregate(result)  # 复用汇总逻辑
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="对已有解题文本做过程评估")
    ap.add_argument("--id", required=True)
    ap.add_argument("--file", help="解题过程文本文件；缺省则使用题集预置解答")
    ap.add_argument("--backend", choices=["auto", "hy3", "mock"], default="auto")
    args = ap.parse_args()

    problem = load_by_id(args.id)
    if problem is None:
        print(f"未找到 id={args.id}；可用：{[p['id'] for p in load_all()]}", file=sys.stderr)
        return 1

    if args.file:
        raw = Path(args.file).read_text(encoding="utf-8")
    else:
        from app.llm.mock import MockLLM
        from app.solver.solver import Solver

        raw = Solver(MockLLM()).solve(problem)

    ev = evaluate_text(problem, raw, args.backend)

    print(f"[{ev.problem_id}] {ev.title}")
    print(f"公开 {ev.public.passed}/{ev.public.total}　对抗 {ev.adversarial.passed}/{ev.adversarial.total}")
    print(f"结果正确={ev.result_correct}　真实正确={ev.truly_correct}　过程成立={ev.process_valid}")
    if ev.false_positive_solution:
        print(">>> 伪正确样本")
    print(f"首个错误步骤：{ev.first_error_step or '—'}")
    print(f"错误类型：{'、'.join(label_of(t) for t in ev.error_types) or '—'}\n")
    for v in ev.section_verdicts:
        mark = {"flawed": "X", "suspicious": "?", "valid": "V"}.get(v.verdict, "?")
        print(f"  [{mark}] {v.section:<12} conf={v.confidence:<5} {v.reason[:100]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
