"""题集加载。"""

from __future__ import annotations

import json
from pathlib import Path

from .config import DATASET_ROOT

_DIFFICULTY_ORDER = {"easy": 0, "medium": 1, "hard": 2, "adversarial": 3}


def load_problem(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_all(root: Path | None = None) -> list[dict]:
    """加载全部题目，按难度、再按 id 排序。"""
    root = root or DATASET_ROOT
    files = sorted(root.rglob("*.json"))
    problems = [load_problem(p) for p in files]
    problems.sort(
        key=lambda d: (
            _DIFFICULTY_ORDER.get(d.get("difficulty", ""), 99),
            d.get("id", ""),
        )
    )
    return problems


def load_by_id(problem_id: str, root: Path | None = None) -> dict | None:
    for p in load_all(root):
        if p.get("id") == problem_id:
            return p
    return None
