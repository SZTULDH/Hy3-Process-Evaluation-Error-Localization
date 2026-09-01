#!/usr/bin/env python
"""评估器有效性验证：跑全量题集并计算定位准确率 / 误报率 / 伪正确识别率。

    python scripts/validation.py
    python scripts/validation.py --json results/validation.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.datasets import load_all  # noqa: E402
from app.evaluator.pipeline import EvalPipeline  # noqa: E402
from app.evaluator.validation import validate  # noqa: E402
from app.llm.factory import get_llm  # noqa: E402
from app.reporting.report import write_reports  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="评估器有效性验证")
    ap.add_argument("--backend", choices=["auto", "hy3", "mock"], default="auto")
    ap.add_argument("--json", help="额外把指标写成 JSON 文件")
    args = ap.parse_args()

    problems = load_all()
    pipeline = EvalPipeline(get_llm(None if args.backend == "auto" else args.backend))

    records = []
    for i, problem in enumerate(problems, start=1):
        print(f"[{i}/{len(problems)}] {problem['id']} ...", flush=True)
        ev = pipeline.run(problem)
        records.append({"problem": problem, "evaluation": ev.to_dict()})

    rep = validate(records)

    print("\n" + "=" * 60)
    print("评估器有效性")
    print("=" * 60)
    print(f"样本总数                : {rep.total}（有错 {rep.gt_flawed} / 无错 {rep.gt_clean}）")
    print(f"定位准确率（top-1）      : {rep.localization_accuracy:.1%}")
    print(f"定位准确率（±1 容差）    : {rep.localization_accuracy_tolerant:.1%}")
    print(f"误报率                  : {rep.false_alarm_rate:.1%}")
    print(f"伪正确识别率            : {rep.pseudo_correct_recall:.1%}")
    print(f"过程判定准确率          : {rep.process_judgement_accuracy:.1%}")

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(
            json.dumps(rep.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n指标已写入 {args.json}")

    paths = write_reports(records, rep, tag="validation")
    print(f"明细报告：{paths['markdown']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
