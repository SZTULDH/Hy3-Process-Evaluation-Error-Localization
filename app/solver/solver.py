"""解题过程生成器（Solver Agent）。"""

from __future__ import annotations

from ..llm.base import BaseLLM, ChatMessage
from ..llm.mock import MockLLM
from .prompts import SOLVER_SYSTEM_PROMPT, build_solver_messages


class Solver:
    def __init__(self, llm: BaseLLM) -> None:
        self.llm = llm

    def _bind_mock_solution(self, problem: dict) -> None:
        """Mock 后端：把题集预置的解答绑上去，使离线链路可复现。"""
        if not isinstance(self.llm, MockLLM):
            return
        preset = problem.get("mock_solution")
        if isinstance(preset, dict):
            parts = []
            titles = [
                "解题思路",
                "复杂度分析",
                "关键边界与处理策略",
                "代码实现",
                "自测说明",
            ]
            for i, title in enumerate(titles, start=1):
                body = preset.get(title, "")
                parts.append(f"## {i}. {title}\n{body}")
            self.llm.bind_solution("\n\n".join(parts))
        elif isinstance(preset, str):
            self.llm.bind_solution(preset)

    def solve(self, problem: dict) -> str:
        self._bind_mock_solution(problem)
        resp = self.llm.chat(
            [
                ChatMessage(role="system", content=SOLVER_SYSTEM_PROMPT),
                ChatMessage(role="user", content=build_solver_messages(problem)),
            ]
        )
        return resp.text or ""
