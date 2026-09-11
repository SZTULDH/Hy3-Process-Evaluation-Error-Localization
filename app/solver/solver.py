"""解题过程生成器（Solver Agent）。"""

from __future__ import annotations

from typing import Iterator

from ..llm.base import BaseLLM, ChatMessage, thinking_of
from ..llm.hy3 import Delta
from ..llm.mock import MockLLM
from .prompts import SOLVER_SYSTEM_PROMPT, build_solver_messages


class Solver:
    def __init__(self, llm: BaseLLM) -> None:
        self.llm = llm
        # 上一次生成时的思考草稿（thinking=enabled 时非空），供 GUI 展示
        self.last_reasoning: str | None = None

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
        self.last_reasoning = None
        resp = self.llm.chat(
            [
                ChatMessage(role="system", content=SOLVER_SYSTEM_PROMPT),
                ChatMessage(role="user", content=build_solver_messages(problem)),
            ],
            thinking=thinking_of(self.llm),
        )
        self.last_reasoning = getattr(resp, "reasoning_content", None)
        return resp.text or ""

    def _messages(self, problem: dict) -> list[ChatMessage]:
        return [
            ChatMessage(role="system", content=SOLVER_SYSTEM_PROMPT),
            ChatMessage(role="user", content=build_solver_messages(problem)),
        ]

    def iter_solve_deltas(self, problem: dict) -> Iterator[Delta]:
        """流式生成，逐个 yield `Delta`（含思考增量）。

        开启深度思考时思考过程可能持续数分钟，前端需要边想边显示，
        否则用户只能对着空白页干等。
        """
        self.last_reasoning = ""
        self._chunks = []
        messages = self._messages(problem)
        thinking = thinking_of(self.llm)

        if not hasattr(self.llm, "iter_deltas"):
            # 后端不支持流式：整段产出，再补一次性的思考内容
            resp = self.llm.chat(messages, thinking=thinking)
            self.last_reasoning = getattr(resp, "reasoning_content", None) or ""
            if self.last_reasoning:
                yield Delta("reasoning", self.last_reasoning)
            text = resp.text or ""
            self._chunks.append(text)
            yield Delta("content", text)
            return

        stream, result = self.llm.iter_deltas(messages, thinking=thinking)
        for delta in stream:
            if delta.kind == "content":
                self._chunks.append(delta.text)
            yield delta
        self.last_reasoning = result.reasoning

    def iter_solve(self, problem: dict) -> Iterator[str]:
        """流式生成：只 yield 正文增量（不关心思考过程时用这个）。"""
        self.last_reasoning = None
        self._chunks = []
        messages = self._messages(problem)
        thinking = thinking_of(self.llm)

        if hasattr(self.llm, "iter_stream"):
            stream, result = self.llm.iter_stream(messages, thinking=thinking)
            for delta in stream:
                self._chunks.append(delta)
                yield delta
            self.last_reasoning = result.reasoning
            return

        resp = self.llm.chat(messages, thinking=thinking)
        self.last_reasoning = getattr(resp, "reasoning_content", None)
        text = resp.text or ""
        self._chunks.append(text)
        yield text

    @property
    def last_text(self) -> str:
        return "".join(getattr(self, "_chunks", []) or [])
