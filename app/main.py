"""命令行入口：单题交互评测与题集批量评测。

用法：
    python -m app.main --problem datasets/code/medium/is_palindrome.json
    python -m app.main --all
    python -m app.main --all --backend hy3      # 需要设置 HY3_API_KEY
    python -m app.main --id medium-002 --quiet
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import RESULTS_DIR, llm_backend_name
from .datasets import load_all, load_by_id, load_problem
from .evaluator.pipeline import EvalPipeline
from .evaluator.validation import validate
from .evaluator.taxonomy import label_of
from .llm.factory import get_llm
from .reporting.report import write_reports


def _print_single(result, verbose: bool = True) -> None:
    ev = result
    print("=" * 72)
    print(f"[{ev.problem_id}] {ev.title}  ({ev.difficulty})")
    print("=" * 72)
    print(
        f"公开测试 {ev.public.passed}/{ev.public.total}　"
        f"对抗测试 {ev.adversarial.passed}/{ev.adversarial.total}"
    )
    print(
        f"结果正确={ev.result_correct}　真实正确={ev.truly_correct}　"
        f"过程成立={ev.process_valid}"
    )
    if ev.false_positive_solution:
        print(">>> 判定：伪正确样本（测试通过但过程/逻辑不成立）")
    print(f"首个错误步骤：{ev.first_error_step or '—'}")
    _type_labels = [label_of(t) for t in ev.error_types]
    print(f"错误类型：{'、'.join(_type_labels) or '—'}")

    if verbose:
        print("\n--- 分步裁决 ---")
        for v in ev.section_verdicts:
            mark = {"flawed": "X", "suspicious": "?", "valid": "V"}.get(v.verdict, "?")
            print(f"  [{mark}] {v.section:<12} conf={v.confidence:<5} {v.reason[:90]}")
        if ev.public.failed:
            print("\n--- 公开测试失败 ---")
            for r in ev.public.results:
                if not r.passed:
                    print(f"  {r.status}: args={r.args} expected={r.expected} actual={r.actual!r}")
        if ev.adversarial.failed:
            print("\n--- 对抗测试失败（暴露真实缺陷）---")
            for r in ev.adversarial.results:
                if not r.passed:
                    print(f"  {r.status}: args={r.args} expected={r.expected} actual={r.actual!r}")
    print()


def run_batch(pipeline: EvalPipeline, problems: list[dict], verbose: bool) -> list[dict]:
    records = []
    for i, problem in enumerate(problems, start=1):
        if verbose:
            print(f"[{i}/{len(problems)}] 评测 {problem.get('id')} ...", flush=True)
        ev = pipeline.run(problem)
        records.append({"problem": problem, "evaluation": ev.to_dict()})
        if verbose:
            _print_single(ev, verbose=False)
    return records


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Hy3 过程评估与错误定位")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--problem", help="单题 JSON 路径")
    g.add_argument("--id", help="按题目 id 评测")
    g.add_argument("--all", action="store_true", help="评测全部题集")
    ap.add_argument("--backend", choices=["auto", "hy3", "mock"], default="auto")
    ap.add_argument("--quiet", action="store_true", help="只输出汇总")
    ap.add_argument("--no-report", action="store_true", help="不写报告文件")
    args = ap.parse_args(argv)

    force = None if args.backend == "auto" else args.backend
    llm = get_llm(force)
    pipeline = EvalPipeline(llm)

    if not args.quiet:
        print(f"LLM 后端：{llm_backend_name()}\n")

    if args.all:
        problems = load_all()
        records = run_batch(pipeline, problems, verbose=not args.quiet)

        validation = validate(records)
        if not args.quiet:
            print("=" * 72)
            print("评估器有效性")
            print("=" * 72)
            print(f"定位准确率（top-1）      : {validation.localization_accuracy:.1%}")
            print(f"定位准确率（±1 容差）    : {validation.localization_accuracy_tolerant:.1%}")
            print(f"误报率                  : {validation.false_alarm_rate:.1%}")
            print(f"伪正确识别率            : {validation.pseudo_correct_recall:.1%}")
            print(f"过程判定准确率          : {validation.process_judgement_accuracy:.1%}")

        if not args.no_report:
            paths = write_reports(records, validation, tag="batch")
            print("\n报告已写入：")
            for k, v in paths.items():
                print(f"  {k}: {v}")

        return 0

    if args.problem:
        problem = load_problem(Path(args.problem))
    else:
        problem = load_by_id(args.id)
        if problem is None:
            print(f"未找到 id={args.id} 的题目", file=sys.stderr)
            return 1

    ev = pipeline.run(problem)
    _print_single(ev, verbose=not args.quiet)

    if not args.no_report:
        records = [{"problem": problem, "evaluation": ev.to_dict()}]
        paths = write_reports(records, validate(records), tag="single")
        if not args.quiet:
            print("报告已写入：")
            for k, v in paths.items():
                print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
