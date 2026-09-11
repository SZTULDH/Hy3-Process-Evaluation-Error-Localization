"""Producer Agent：负责产出完整解题过程（思路→复杂度→边界→实现→自测）。"""

from __future__ import annotations

from ..llm.base import BaseLLM
from ..solver.solver import Solver


class ProducerAgent:
    """包装原有 Solver，作为多 Agent 链路中的「生产端」。"""

    role = "producer"

    def __init__(self, llm: BaseLLM) -> None:
        self.solver = Solver(llm)
        self.llm = llm

    def produce(self, problem: dict) -> str:
        """生成五段式完整解题过程文本。"""
        return self.solver.solve(problem)
