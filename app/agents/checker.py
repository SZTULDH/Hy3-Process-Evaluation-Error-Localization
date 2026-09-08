"""Checker Agent：对 Producer 产物做检查、分析与源码级调试取证。

职责：
1. 解析产物，抽出代码
2. 沙盒执行公开/对抗测试
3. 对失败用例调用 sandbox-debugger 做轨迹取证
4. 规则层静态分析 + 过程缺陷汇总
5. （可选）LLM 对产物做二次诊断总结

输出结构化 CheckerReport，供评估流水线与报告使用。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..config import SECTION_TITLES
from ..evaluator.rules import Finding, analyze
from ..evaluator.splitter import ParsedSolution, extract_code, split_sections
from ..llm.base import BaseLLM, ChatMessage
from ..llm.mock import MockLLM
from ..sandbox.forensics import forensic_summary, run_forensics
from ..sandbox.runner import SuiteResult, run_suite

CHECKER_SYSTEM = """[ROLE=checker]
你是代码产物检查与调试专家。你会收到：
- 题目信息
- Producer 生成的完整解题过程与代码
- 公开/对抗测试执行结果
- 源码级调试轨迹（若有）

请输出 JSON（不要其它文字）：
{
  "summary": "一句话总评",
  "process_ok": true/false,
  "code_ok": true/false,
  "pseudo_correct": true/false,
  "root_cause": "根因简述",
  "debug_hints": ["可操作的调试建议1", "..."],
  "failed_case_analysis": ["对失败用例的逐条分析"]
}
"""


@dataclass
class CheckerReport:
    process_ok: bool = True
    code_ok: bool = True
    pseudo_correct: bool = False
    public: SuiteResult | None = None
    adversarial: SuiteResult | None = None
    findings: list[Finding] = field(default_factory=list)
    forensics: list[dict] = field(default_factory=list)
    signals: dict = field(default_factory=dict)
    parsed: ParsedSolution | None = None
    code: str = ""
    llm_summary: dict = field(default_factory=dict)
    debug_hints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "process_ok": self.process_ok,
            "code_ok": self.code_ok,
            "pseudo_correct": self.pseudo_correct,
            "public": self.public.to_dict() if self.public else {},
            "adversarial": self.adversarial.to_dict() if self.adversarial else {},
            "findings": [f.to_dict() for f in self.findings],
            "forensics": self.forensics,
            "signals": self.signals,
            "code": self.code,
            "llm_summary": self.llm_summary,
            "debug_hints": self.debug_hints,
            "sections": self.parsed.as_dict() if self.parsed else {},
        }


class CheckerAgent:
    role = "checker"

    def __init__(self, llm: BaseLLM | None = None) -> None:
        self.llm = llm

    def check(self, problem: dict, raw_solution: str) -> CheckerReport:
        """对 Producer 产物做完整检查与调试取证。"""
        report = CheckerReport()
        parsed = split_sections(raw_solution)
        report.parsed = parsed

        code_section = parsed.get("代码实现")
        code = extract_code(code_section.content if code_section else raw_solution)
        if not code:
            code = extract_code(raw_solution)
        report.code = code or ""

        entry = problem.get("entry_point", "")
        public = run_suite(code, entry, problem.get("public_tests", []))
        adversarial = run_suite(code, entry, problem.get("adversarial_tests", []))
        report.public = public
        report.adversarial = adversarial

        findings, signals = analyze(problem, parsed, code, public, adversarial)
        report.findings = findings
        report.signals = signals

        failed = [r for r in adversarial.results if not r.passed][:3]
        for r in failed:
            fo = run_forensics(code, entry, r.args)
            report.forensics.append(
                {
                    "case_index": r.index,
                    "args": r.args,
                    "expected": r.expected,
                    "actual": r.actual,
                    "status": r.status,
                    "forensic": fo,
                    "summary": forensic_summary(fo),
                }
            )

        report.code_ok = public.all_passed and (
            adversarial.total == 0 or adversarial.all_passed
        )
        report.process_ok = not any(
            f.error_type for f in findings if f.confidence >= 0.5
        )
        report.pseudo_correct = bool(
            public.total
            and public.all_passed
            and (
                (adversarial.total and not adversarial.all_passed)
                or not report.process_ok
            )
        )

        report.debug_hints = self._build_debug_hints(report)
        report.llm_summary = self._llm_diagnose(problem, report)
        return report

    def _build_debug_hints(self, report: CheckerReport) -> list[str]:
        hints: list[str] = []
        if report.pseudo_correct:
            hints.append(
                "公开测试通过但对抗失败：优先检查边界归一化、空/单元素、类型边界，"
                "并用 sandbox-debugger 在失败入参上下断点观察局部变量。"
            )
        for fo in report.forensics:
            summary = fo.get("summary") or ""
            if "trace_tail" in summary or fo.get("forensic", {}).get("ok"):
                hints.append(
                    f"失败用例 args={fo.get('args')!r} 的执行轨迹已取证："
                    f"{summary[:160]}"
                )
        for f in report.findings[:3]:
            if f.confidence >= 0.7:
                hints.append(f"[{f.error_type}] {f.detail[:120]}")
        if not hints:
            hints.append("公开与对抗测试均通过，规则层未见高置信缺陷。")
        return hints

    def _llm_diagnose(self, problem: dict, report: CheckerReport) -> dict:
        if self.llm is None:
            return self._rule_summary(report)

        payload = {
            "problem_id": problem.get("id"),
            "title": problem.get("title"),
            "public": report.public.to_dict() if report.public else {},
            "adversarial": report.adversarial.to_dict() if report.adversarial else {},
            "findings": [f.to_dict() for f in report.findings[:5]],
            "forensics": [
                {"args": f.get("args"), "summary": f.get("summary")}
                for f in report.forensics[:3]
            ],
            "pseudo_correct": report.pseudo_correct,
            "code_ok": report.code_ok,
        }

        if isinstance(self.llm, MockLLM):
            return self._rule_summary(report)

        try:
            resp = self.llm.chat(
                [
                    ChatMessage(role="system", content=CHECKER_SYSTEM),
                    ChatMessage(
                        role="user",
                        content=json.dumps(payload, ensure_ascii=False, indent=2),
                    ),
                ]
            )
            text = (resp.text or "").strip()
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                return json.loads(text[start : end + 1])
            return {"summary": text[:300], "raw": True}
        except Exception as exc:  # noqa: BLE001
            out = self._rule_summary(report)
            out["llm_error"] = f"{type(exc).__name__}: {exc}"
            return out

    @staticmethod
    def _rule_summary(report: CheckerReport) -> dict:
        if report.pseudo_correct:
            summary = "伪正确：公开测试通过，但对抗测试或过程审查暴露缺陷。"
        elif report.code_ok and report.process_ok:
            summary = "代码与过程均成立。"
        elif not report.code_ok:
            summary = "代码未通过全部测试。"
        else:
            summary = "测试通过，但过程存在可疑点。"
        return {
            "summary": summary,
            "process_ok": report.process_ok,
            "code_ok": report.code_ok,
            "pseudo_correct": report.pseudo_correct,
            "root_cause": (report.findings[0].detail if report.findings else ""),
            "debug_hints": report.debug_hints,
            "failed_case_analysis": [
                f"args={f.get('args')!r} status={f.get('status')} "
                f"expected={f.get('expected')!r} actual={f.get('actual')!r}"
                for f in report.forensics
            ],
        }
