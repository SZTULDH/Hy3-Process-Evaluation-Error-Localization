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
from typing import Any, Callable

from ..config import SECTION_TITLES
from ..evaluator.rules import Finding, analyze
from ..evaluator.splitter import ParsedSolution, extract_code, split_sections
from ..llm.base import BaseLLM, ChatMessage, thinking_of
from ..llm.mock import MockLLM
from ..sandbox.forensics import forensic_summary, run_forensics_case
from ..sandbox.runner import SuiteResult, run_suite, suite_kwargs
from .tools import CHECKER_TOOLS, make_checker_handlers

CHECKER_SYSTEM = """[ROLE=checker]
你是代码产物检查与调试专家。你会收到：
- 题目信息
- Producer 生成的完整解题过程与代码
- 公开/对抗测试执行结果
- 源码级调试轨迹（若有）

若题目是类级（kind=class）：用例在**同一实例**上按序调用，观察 `self` 状态；
"公开过、对抗挂"往往源于状态未复位或跨调用的顺序依赖，而非单次调用算错。

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


def entry_label(problem: dict) -> str:
    """入口的展示名：类级显示 ``Class.method``，函数级就是函数名。"""
    entry = problem.get("entry_point") or ""
    if (problem.get("kind") or "function").lower() == "class":
        cls = problem.get("class_name") or entry
        return f"{cls}.{entry}" if entry else cls
    return entry


def case_label(result) -> str:
    """失败样本的一行标识，类级带上方法名。"""
    head = f"#{result.index}"
    if getattr(result, "method", None):
        head += f" {result.method}()"
    return f"{head} args={result.args!r}"


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
        """完整检查：执行/规则取证 + LLM 二次诊断。"""
        report = self.inspect(problem, raw_solution)
        report.llm_summary = self.summarize(problem, report)
        return report

    def inspect(self, problem: dict, raw_solution: str,
                on_step: Callable[[str, dict], None] | None = None) -> CheckerReport:
        """只做解析、沙盒执行、规则分析与取证，**不调用 LLM**。

        拆出来是为了流式场景：先把"硬证据"（测试结果）推给前端，
        再慢慢跑后面的 LLM 审查。

        `on_step` 是可选观察回调，按发生顺序收到 `(步骤名, {input, output})`，
        用于把 Agent 的每一步真实输入/输出开放给上层展示。
        """
        def emit(step: str, inp: dict, out: dict) -> None:
            if on_step is not None:
                on_step(step, {"input": inp, "output": out})

        report = CheckerReport()
        parsed = split_sections(raw_solution)
        report.parsed = parsed

        code_section = parsed.get("代码实现")
        code = extract_code(code_section.content if code_section else raw_solution)
        if not code:
            code = extract_code(raw_solution)
        report.code = code or ""

        emit(
            "parse",
            {
                "解答字符数": len(raw_solution or ""),
                "入口": entry_label(problem),
                "题目形态": (problem.get("kind") or "function"),
            },
            {
                "识别到的段落": [t for t in SECTION_TITLES if parsed.get(t) is not None],
                "代码字符数": len(report.code),
            },
        )

        entry = problem.get("entry_point", "")
        suite_kw = suite_kwargs(problem)
        public = run_suite(code, entry, problem.get("public_tests", []), **suite_kw)
        emit(
            "public",
            {"用例数": len(problem.get("public_tests", []) or []),
             "执行方式": entry_label(problem)},
            {
                "通过": f"{public.passed}/{public.total}",
                "失败样本": [
                    f"{case_label(r)} -> {r.status}"
                    for r in public.results if not r.passed
                ][:5],
            },
        )

        adversarial = run_suite(code, entry, problem.get("adversarial_tests", []),
                                **suite_kw)
        emit(
            "adversarial",
            {"用例数": len(problem.get("adversarial_tests", []) or []),
             "执行方式": entry_label(problem)},
            {
                "通过": f"{adversarial.passed}/{adversarial.total}",
                "失败样本": [
                    f"{case_label(r)} expected={r.expected!r} actual={r.actual!r}"
                    for r in adversarial.results if not r.passed
                ][:5],
            },
        )
        report.public = public
        report.adversarial = adversarial

        findings, signals = analyze(problem, parsed, code, public, adversarial)
        report.findings = findings
        report.signals = signals
        emit(
            "rules",
            {"输入": "段落 + 代码 + 两组测试结果"},
            {
                "命中条数": len(findings),
                "错误类型": sorted({f.error_type for f in findings if f.error_type}),
                "信号字段": sorted(signals.keys()),
            },
        )

        kind = (problem.get("kind") or "function").lower()
        adv_cases = problem.get("adversarial_tests") or []
        failed = [r for r in adversarial.results if not r.passed][:3]
        for r in failed:
            fo = run_forensics_case(code, problem, adv_cases, r.index)
            report.forensics.append(
                {
                    "case_index": r.index,
                    "kind": kind,
                    "method": r.method,
                    "args": r.args,
                    "expected": r.expected,
                    "actual": r.actual,
                    "status": r.status,
                    "forensic": fo,
                    "summary": forensic_summary(fo),
                }
            )

        emit(
            "forensics",
            {
                "待取证失败用例": len(failed),
                "取证方式": "类级：回放前置用例后取证" if kind == "class" else "函数级：直接取证",
            },
            {
                "取证条数": len(report.forensics),
                "摘要": [f.get("summary", "")[:200] for f in report.forensics],
            },
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
        return report

    def summarize(self, problem: dict, report: CheckerReport, trace: dict | None = None) -> dict:
        """在 inspect 结果上补 LLM 二次诊断（与 inspect 分离，便于流式编排）。

        `trace` 为可选观察袋，写入本次 LLM 调用的真实输入/输出，供上层展示。
        """
        return self._llm_diagnose(problem, report, trace)

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
                where = f"{fo['method']}() 的" if fo.get("method") else ""
                hints.append(
                    f"失败用例 {where}args={fo.get('args')!r} 的执行轨迹已取证："
                    f"{summary[:160]}"
                )
        for f in report.findings[:3]:
            if f.confidence >= 0.7:
                hints.append(f"[{f.error_type}] {f.detail[:120]}")
        if not hints:
            hints.append("公开与对抗测试均通过，规则层未见高置信缺陷。")
        return hints

    def _llm_diagnose(self, problem: dict, report: CheckerReport,
                      trace: dict | None = None) -> dict:
        if self.llm is None:
            return self._rule_summary(report)

        payload = {
            "problem_id": problem.get("id"),
            "title": problem.get("title"),
            "kind": problem.get("kind") or "function",
            "class_name": problem.get("class_name"),
            "entry_point": problem.get("entry_point"),
            "public": report.public.to_dict() if report.public else {},
            "adversarial": report.adversarial.to_dict() if report.adversarial else {},
            "findings": [f.to_dict() for f in report.findings[:5]],
            "forensics": [
                {"method": f.get("method"), "args": f.get("args"),
                 "summary": f.get("summary")}
                for f in report.forensics[:3]
            ],
            "pseudo_correct": report.pseudo_correct,
            "code_ok": report.code_ok,
        }

        if isinstance(self.llm, MockLLM):
            return self._rule_summary(report)

        if trace is not None:
            trace.update({"input": payload, "system_prompt": CHECKER_SYSTEM})

        try:
            resp = self.llm.chat(
                [
                    ChatMessage(role="system", content=CHECKER_SYSTEM),
                    ChatMessage(
                        role="user",
                        content=json.dumps(payload, ensure_ascii=False, indent=2),
                    ),
                ],
                response_format_json=True,
                thinking=thinking_of(self.llm),
            )
            text = (resp.text or "").strip()
            if trace is not None:
                trace["llm_raw"] = text[:6000]
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                return json.loads(text[start : end + 1])
            return {"summary": text[:300], "raw": True}
        except Exception as exc:  # noqa: BLE001
            if trace is not None:
                trace["error"] = f"{type(exc).__name__}: {exc}"
            out = self._rule_summary(report)
            out["llm_error"] = f"{type(exc).__name__}: {exc}"
            return out


    def diagnose_with_tools(self, problem: dict, report: CheckerReport) -> dict:
        """使用 Hy3 交错式思考 + 工具调用做二次诊断（可选）。

        模型可自行调用 run_public_tests / run_adversarial_tests / run_forensics，
        客户端按 SDK 要求原样回填 reasoning_content 与 tool 结果。
        """
        if self.llm is None or isinstance(self.llm, MockLLM):
            return self._rule_summary(report)

        handlers = make_checker_handlers(problem)
        user = {
            "problem_id": problem.get("id"),
            "title": problem.get("title"),
            "description": problem.get("description"),
            "kind": problem.get("kind") or "function",
            "class_name": problem.get("class_name"),
            "entry_point": problem.get("entry_point"),
            "code": report.code,
            "hint": (
                "可调用工具复测公开/对抗用例，或对失败 args 做 forensics。"
                "最终请输出与系统提示一致的 JSON 总评。"
            ),
            "current_signals": {
                "public_passed": report.public.all_passed if report.public else None,
                "adversarial_passed": (
                    report.adversarial.all_passed if report.adversarial else None
                ),
                "pseudo_correct": report.pseudo_correct,
            },
        }
        try:
            resp = self.llm.chat_with_tools(
                [
                    ChatMessage(role="system", content=CHECKER_SYSTEM),
                    ChatMessage(
                        role="user",
                        content=json.dumps(user, ensure_ascii=False, indent=2),
                    ),
                ],
                tools=CHECKER_TOOLS,
                handlers=handlers,
                max_rounds=6,
                thinking=thinking_of(self.llm),
            )
            text = (resp.text or "").strip()
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                out = json.loads(text[start : end + 1])
            else:
                out = {"summary": text[:400], "raw": True}
            out["_reasoning_preview"] = (resp.reasoning_content or "")[:200]
            return out
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
