#!/usr/bin/env python
"""评估器有效性验证（标准答案路线）。

为什么要单开这一条路线：
题集里的 `ground_truth` 标注的是**参考解答**（`mock_solution`）的缺陷 —— 哪个
步骤先错、错在哪、属于哪类。所以"定位准确率 / 误报率"必须拿**参考解答**去喂
评估器，再与 ground_truth 对照才有意义。

若直接拿真实模型生成的解答去对这份 ground_truth，两边描述的缺陷不是同一个
对象，指标不成立。

本脚本每条记录只跑评估（不做生成），因此比全量跑批快得多。

用法：
    python scripts/validate_reference.py
    python scripts/validate_reference.py --out results/ref_validation
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import (  # noqa: E402
    DATASET_ROOT,
    HY3_BASE_URL,
    HY3_MODEL,
    HY3_THINKING,
    RESULTS_DIR,
    SECTION_TITLES,
)
from app.datasets import load_problem  # noqa: E402
from app.evaluator.pipeline import EvalPipeline  # noqa: E402
from app.evaluator.validation import validate  # noqa: E402
from app.llm.factory import get_llm  # noqa: E402


def reference_markdown(problem: dict) -> str:
    """把 mock_solution（分段 dict）拼成五段文档，与 Web 工作台口径一致。"""
    sol = problem.get("mock_solution") or {}
    if not isinstance(sol, dict) or not sol:
        return ""
    parts = []
    for n, title in enumerate(SECTION_TITLES, start=1):
        body = sol.get(title)
        if body:
            parts.append(f"## {n}. {title}\n\n{body}")
    if not parts:
        for n, (k, v) in enumerate(sol.items(), start=1):
            parts.append(f"## {n}. {k}\n\n{v}")
    return "\n\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description="评估器有效性验证（参考解答路线）")
    ap.add_argument("--out", help="输出目录")
    ap.add_argument("--limit", type=int, help="只跑前 N 题（试水）")
    args = ap.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(args.out) if args.out else (RESULTS_DIR / f"ref_validation_{stamp}")
    out.mkdir(parents=True, exist_ok=True)
    jsonl = out / "reference.jsonl"

    items = []
    for path in sorted(DATASET_ROOT.rglob("*.json")):
        data = load_problem(path)
        md = reference_markdown(data)
        if md.strip():
            items.append((data, md, str(path.relative_to(ROOT))))
    if args.limit:
        items = items[: args.limit]

    print(f"输出目录：{out}")
    print(f"含参考解答的题目：{len(items)}　模型：{HY3_MODEL}　思考强度：{HY3_THINKING}",
          flush=True)
    (out / "meta.json").write_text(json.dumps({
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "model": HY3_MODEL, "base_url": HY3_BASE_URL, "thinking": HY3_THINKING,
        "problems_with_reference": len(items), "out_dir": str(out),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    pipeline = EvalPipeline(get_llm("hy3"))
    records: list[dict] = []
    t_all = time.perf_counter()

    with open(jsonl, "a", encoding="utf-8") as fh:
        for i, (problem, raw, rel) in enumerate(items, start=1):
            pid = str(problem.get("id"))
            print(f"[{i}/{len(items)}] {pid} {problem.get('title', '')} ...", flush=True)
            t0 = time.perf_counter()
            try:
                ev = pipeline.evaluate_solution(problem, raw)
                d = ev.to_dict()
                rec = {
                    "problem_id": ev.problem_id, "title": ev.title,
                    "difficulty": ev.difficulty,
                    "kind": problem.get("kind") or "function",
                    "source_path": rel,
                    "ground_truth": problem.get("ground_truth") or {},
                    "verdict": {
                        "result_correct": ev.result_correct,
                        "truly_correct": ev.truly_correct,
                        "process_valid": ev.process_valid,
                        "false_positive_solution": ev.false_positive_solution,
                        "first_error_step": ev.first_error_step,
                        "first_error_section": ev.first_error_section,
                        "error_types": d.get("error_types", []),
                    },
                    "public": d.get("public", {}),
                    "adversarial": d.get("adversarial", {}),
                    "rule_findings": d.get("rule_findings", []),
                    "section_verdicts": d.get("section_verdicts", []),
                    "elapsed_sec": round(time.perf_counter() - t0, 2),
                }
                records.append({"problem": problem, "evaluation": ev.to_dict()})
                (out / "problems").mkdir(exist_ok=True)
                (out / "problems" / f"{pid}.json").write_text(
                    json.dumps(rec, ensure_ascii=False, default=repr, indent=2),
                    encoding="utf-8")
                fh.write(json.dumps(rec, ensure_ascii=False, default=repr) + "\n")
                fh.flush()
                v = rec["verdict"]
                print(f"    {rec['elapsed_sec']:.0f}s　公开 "
                      f"{rec['public'].get('passed')}/{rec['public'].get('total')}　"
                      f"过程={v['process_valid']} 定位={v['first_error_step']}",
                      flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"    !! 失败 {type(exc).__name__}: {exc}", flush=True)

    rep = validate(records)
    (out / "metrics.json").write_text(
        json.dumps(rep.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 60)
    print(f"样本 {rep.total}（标注有错 {rep.gt_flawed} / 标注无错 {rep.gt_clean}）")
    print(f"定位准确率 top-1   : {rep.localization_accuracy:.1%}")
    print(f"定位准确率 ±1      : {rep.localization_accuracy_tolerant:.1%}")
    print(f"误报率             : {rep.false_alarm_rate:.1%}")
    print(f"伪正确识别率       : {rep.pseudo_correct_recall:.1%}")
    print(f"过程判定准确率     : {rep.process_judgement_accuracy:.1%}")
    print(f"总耗时 {(time.perf_counter() - t_all) / 60:.1f} 分钟")
    print(f"指标：{out / 'metrics.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
