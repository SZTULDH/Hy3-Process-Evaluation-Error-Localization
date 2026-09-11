#!/usr/bin/env python
"""全量真机评测 + 过程结果完整落盘。

与 `python -m app.main --all` 的区别：
* 逐题落盘（每题一个 JSON + 汇总 JSONL），**支持续跑** —— 中断后重跑会跳过已完成的题
* 保存**完整过程**：未截断的解答原文与五段正文、提取到的代码、两组测试明细、
  规则发现、分步裁决、Checker 报告（含 LLM 总评与源码级取证）、耗时与配置

用法：
    python scripts/run_all_eval.py                     # 全部题目
    python scripts/run_all_eval.py --out results/run_x # 指定输出目录
    python scripts/run_all_eval.py --only engineering  # 只跑某个难度/类别
    python scripts/run_all_eval.py --limit 3           # 先跑前 3 题试水

输出目录结构：
    <out>/problems/<problem_id>.json   单题完整记录
    <out>/all.jsonl                    全量汇总（逐行一条记录）
    <out>/run_meta.json                运行配置与进度
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import (  # noqa: E402
    CRITIC_PARALLELISM,
    DATASET_ROOT,
    HY3_BASE_URL,
    HY3_MODEL,
    HY3_THINKING,
    LLM_TIMEOUT,
    RESULTS_DIR,
)
from app.datasets import load_problem  # noqa: E402
from app.evaluator.pipeline import EvalPipeline  # noqa: E402
from app.llm.factory import get_llm  # noqa: E402

_ORDER = {"easy": 0, "medium": 1, "hard": 2, "adversarial": 3,
          "engineering": 4, "realworld": 4}


def load_all_with_paths() -> list[tuple[dict, str]]:
    """与 app.datasets.load_all 同样的排序，但额外带上仓库内相对路径。"""
    items: list[tuple[dict, str]] = []
    for path in sorted(DATASET_ROOT.rglob("*.json")):
        items.append((load_problem(path), str(path.relative_to(ROOT))))
    items.sort(key=lambda it: (
        _ORDER.get(it[0].get("difficulty", ""), 99), it[0].get("id", "")))
    return items


def safe_id(pid: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in str(pid))


def record_of(problem: dict, ev, elapsed: float, rel_path: str | None = None) -> dict:
    """把一次评估压成可长期保存的完整记录（不截断正文）。"""
    d = ev.to_dict()
    return {
        "problem_id": ev.problem_id,
        "title": ev.title,
        "difficulty": ev.difficulty,
        "kind": (problem.get("kind") or "function"),
        "class_name": problem.get("class_name"),
        "entry_point": problem.get("entry_point"),
        "source_path": rel_path,
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
        "raw_solution": ev.raw_solution,
        "sections": ev.sections,
        "code": ev.code,
        "public": d.get("public", {}),
        "adversarial": d.get("adversarial", {}),
        "rule_findings": d.get("rule_findings", []),
        "section_verdicts": d.get("section_verdicts", []),
        "checker_report": d.get("checker_report", {}),
        "elapsed_sec": round(elapsed, 2),
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="全量真机评测（可续跑）")
    ap.add_argument("--out", help="输出目录，默认 results/run_<时间戳>")
    ap.add_argument("--only", help="只跑某难度/类别，如 engineering / medium / class")
    ap.add_argument("--limit", type=int, help="只跑前 N 题")
    args = ap.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(args.out) if args.out else (RESULTS_DIR / f"run_{stamp}")
    problems_dir = out / "problems"
    problems_dir.mkdir(parents=True, exist_ok=True)
    jsonl = out / "all.jsonl"

    problems = load_all_with_paths()
    if args.only:
        key = args.only.lower()
        problems = [
            (p, rel) for p, rel in problems
            if key in str(p.get("difficulty", "")).lower()
            or key in str(p.get("id", "")).lower()
            or key in rel.lower()
            or key == (p.get("kind") or "function").lower()
        ]
    if args.limit:
        problems = problems[: args.limit]

    done: set[str] = set()
    if jsonl.exists():
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    done.add(json.loads(line)["problem_id"])
                except Exception:  # noqa: BLE001
                    pass
    todo = [(p, rel) for p, rel in problems if str(p.get("id")) not in done]

    meta = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "model": HY3_MODEL,
        "base_url": HY3_BASE_URL,
        "thinking": HY3_THINKING,
        "llm_timeout": LLM_TIMEOUT,
        "critic_parallelism": CRITIC_PARALLELISM,
        "total_problems": len(problems),
        "already_done": len(done),
        "to_run": len(todo),
        "out_dir": str(out),
    }
    (out / "run_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"输出目录：{out}")
    print(f"题目总数：{len(problems)}　已完成：{len(done)}　本轮待跑：{len(todo)}", flush=True)
    print(f"模型：{HY3_MODEL} @ {HY3_BASE_URL}　思考强度：{HY3_THINKING}", flush=True)

    if not todo:
        print("没有待跑的题目。")
        return 0

    pipeline = EvalPipeline(get_llm("hy3"))
    ok = fail = 0
    t_all = time.perf_counter()

    with open(jsonl, "a", encoding="utf-8") as fh:
        for i, (problem, rel) in enumerate(todo, start=1):
            pid = str(problem.get("id"))
            tag = f"[{i}/{len(todo)}] {pid} {problem.get('title', '')}"
            print(f"{tag} ...", flush=True)
            t0 = time.perf_counter()
            try:
                ev = pipeline.run(problem)
                elapsed = time.perf_counter() - t0
                rec = record_of(problem, ev, elapsed, rel)
                (problems_dir / f"{safe_id(pid)}.json").write_text(
                    json.dumps(rec, ensure_ascii=False, default=repr, indent=2),
                    encoding="utf-8")
                fh.write(json.dumps(rec, ensure_ascii=False, default=repr) + "\n")
                fh.flush()
                ok += 1
                v = rec["verdict"]
                print(
                    f"    {elapsed:.0f}s　公开 {rec['public'].get('passed')}/"
                    f"{rec['public'].get('total')}　对抗 {rec['adversarial'].get('passed')}/"
                    f"{rec['adversarial'].get('total')}　结果={v['result_correct']} "
                    f"过程={v['process_valid']} 伪正确={v['false_positive_solution']}",
                    flush=True)
            except Exception as exc:  # noqa: BLE001 - 单题失败不能中断整批
                elapsed = time.perf_counter() - t0
                fail += 1
                err = f"{type(exc).__name__}: {exc}"
                print(f"    !! 失败（{elapsed:.0f}s）{err}", flush=True)
                fh.write(json.dumps({
                    "problem_id": pid, "title": problem.get("title"),
                    "difficulty": problem.get("difficulty"),
                    "kind": problem.get("kind") or "function",
                    "error": err, "traceback": traceback.format_exc()[-2000:],
                    "elapsed_sec": round(elapsed, 2),
                    "recorded_at": datetime.now().isoformat(timespec="seconds"),
                }, ensure_ascii=False) + "\n")
                fh.flush()

    total_sec = time.perf_counter() - t_all
    print("\n" + "=" * 60)
    print(f"本轮完成：成功 {ok} / 失败 {fail}　总耗时 {total_sec / 60:.1f} 分钟")
    print(f"汇总：{jsonl}")
    print(f"单题明细目录：{problems_dir}")
    return 0 if fail == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
