#!/usr/bin/env python
"""阶段一入口：只跑 Solver，输出结构化解题过程。

    python scripts/run_solver.py --id medium-002
    python scripts/run_solver.py --id medium-002 --raw    # 输出原始文本
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.datasets import load_all, load_by_id  # noqa: E402
from app.evaluator.splitter import extract_code, split_sections  # noqa: E402
from app.llm.factory import get_llm  # noqa: E402
from app.solver.solver import Solver  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="生成解题过程")
    ap.add_argument("--id", required=True)
    ap.add_argument("--backend", choices=["auto", "hy3", "mock"], default="auto")
    ap.add_argument("--raw", action="store_true", help="输出未切分的原始文本")
    args = ap.parse_args()

    problem = load_by_id(args.id)
    if problem is None:
        print(f"未找到 id={args.id}；可用：{[p['id'] for p in load_all()]}", file=sys.stderr)
        return 1

    solver = Solver(get_llm(None if args.backend == "auto" else args.backend))
    raw = solver.solve(problem)

    if args.raw:
        print(raw)
        return 0

    parsed = split_sections(raw)
    print(f"题目：{problem['id']} {problem['title']}")
    print(f"识别到 {len(parsed.sections)} 个段落")
    if parsed.missing_titles:
        print(f"缺失段落：{parsed.missing_titles}")
    print()
    for s in parsed.sections:
        print(f"--- {s.step_id} / {s.title} ---")
        print(s.content[:600])
        print()
    code = extract_code(parsed.get("代码实现").content if parsed.get("代码实现") else raw)
    print("--- 提取到的代码 ---")
    print(code or "（未提取到代码块）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
