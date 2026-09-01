"""端到端评估流水线：生成 → 执行 → 规则信号 → 分步 Critic → 汇总裁决。

核心输出是「结果正确性」与「过程正确性」两条**解耦**的通道：
* result_correct —— 公开测试是否通过（传统意义上的“对”）
* truly_correct  —— 公开 + 对抗测试均通过（逻辑层面的“对”）
* process_valid  —— 五个步骤是否都成立
三者组合才能识别“结果正确但过程不成立”的伪正确样本。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..config import SECTION_TITLES
from ..llm.base import BaseLLM
from ..llm.factory import get_llm
from ..llm.mock import MockLLM
from ..sandbox.runner import SuiteResult, run_suite
from ..solver.solver import Solver
from .critic import Critic, SectionVerdict
from .rules import Finding, analyze
from .splitter import ParsedSolution, extract_code, split_sections
from .taxonomy import label_of


@dataclass
class EvaluationResult:
    problem_id: str
    difficulty: str
    title: str

    raw_solution: str
    sections: dict[str, str]
    code: str

    public: SuiteResult
    adversarial: SuiteResult

    rule_findings: list[Finding] = field(default_factory=list)
    section_verdicts: list[SectionVerdict] = field(default_factory=list)

    # 两条通道
    result_correct: bool = False
    truly_correct: bool = False
    process_valid: bool = False

    first_error_step: str | None = None
    first_error_section: str | None = None
    error_types: list[str] = field(default_factory=list)

    false_positive_solution: bool = False
    elapsed_sec: float = 0.0
    backend: str = "mock"

    def to_dict(self) -> dict:
        return {
            "problem_id": self.problem_id,
            "difficulty": self.difficulty,
            "title": self.title,
            "backend": self.backend,
            "result_correct": self.result_correct,
            "truly_correct": self.truly_correct,
            "process_valid": self.process_valid,
            "false_positive_solution": self.false_positive_solution,
            "public": self.public.to_dict(),
            "adversarial": self.adversarial.to_dict(),
            "first_error_step": self.first_error_step,
            "first_error_section": self.first_error_section,
            "error_types": [label_of(t) for t in self.error_types],
            "sections": {
                k: (v[:500] + "...") if len(v) > 500 else v
                for k, v in self.sections.items()
            },
            "code": self.code,
            "rule_findings": [f.to_dict() for f in self.rule_findings],
            "section_verdicts": [v.to_dict() for v in self.section_verdicts],
            "elapsed_sec": round(self.elapsed_sec, 2),
        }


class EvalPipeline:
    def __init__(self, llm: BaseLLM | None = None) -> None:
        self.llm = llm or get_llm()
        self.solver = Solver(self.llm)
        self.critic = Critic(self.llm)

    # ------------------------------------------------------------------

    def run(self, problem: dict) -> EvaluationResult:
        start = time.perf_counter()

        raw = self.solver.solve(problem)
        parsed: ParsedSolution = split_sections(raw)
        code = extract_code(parsed.get("代码实现").content if parsed.get("代码实现") else raw)
        if not code:
            code = extract_code(raw)

        entry = problem.get("entry_point", "")
        public = run_suite(code, entry, problem.get("public_tests", []))
        adversarial = run_suite(code, entry, problem.get("adversarial_tests", []))

        findings, signals = analyze(problem, parsed, code, public, adversarial)

        verdicts: list[SectionVerdict] = []
        for title in SECTION_TITLES:
            section = parsed.get(title)
            content = section.content if section else ""
            verdicts.append(
                self.critic.review_section(
                    section_title=title,
                    step_id=section.step_id if section else f"step_{SECTION_TITLES.index(title) + 1}",
                    section_content=content,
                    problem=problem,
                    code=code,
                    signals=signals,
                    rule_findings=findings,
                )
            )

        result = EvaluationResult(
            problem_id=problem.get("id", "unknown"),
            difficulty=problem.get("difficulty", "unknown"),
            title=problem.get("title", ""),
            raw_solution=raw,
            sections=parsed.as_dict(),
            code=code,
            public=public,
            adversarial=adversarial,
            rule_findings=findings,
            section_verdicts=verdicts,
            elapsed_sec=time.perf_counter() - start,
            backend="mock" if isinstance(self.llm, MockLLM) else "hy3",
        )

        self._aggregate(result)
        return result

    # ------------------------------------------------------------------

    def _aggregate(self, r: EvaluationResult) -> None:
        r.result_correct = r.public.all_passed
        r.truly_correct = r.public.all_passed and (
            r.adversarial.total == 0 or r.adversarial.all_passed
        )

        flawed = [v for v in r.section_verdicts if v.is_flawed]
        r.process_valid = not flawed

        if flawed:
            first = min(flawed, key=lambda v: SECTION_TITLES.index(v.section))
            r.first_error_step = first.step_id
            r.first_error_section = first.section

        types: set[str] = set()
        for v in flawed:
            types.update(v.error_types)
        r.error_types = sorted(types)

        # 伪正确：公开测试通过，但过程不成立或对抗测试失败
        r.false_positive_solution = bool(
            r.result_correct and (not r.process_valid or not r.truly_correct)
        )
