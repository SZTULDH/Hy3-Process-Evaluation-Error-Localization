"""规则信号通道（执行信号 + 静态分析）。

这一层不依赖任何 LLM，因此在离线 Mock 模式下也能独立产出有依据的判定。
在线模式下它的输出会作为证据喂给 Hy3 Critic，用于降低纯 LLM 审查的
主观性与误报（对应 PROPOSAL 第 8 节“多信号融合”）。
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

from ..sandbox.runner import SuiteResult
from .splitter import ParsedSolution
from .taxonomy import CODE_TO_LABEL

# ------------------------------------------------------------------ 复杂度


# 复杂度等级序：数值越大越“慢”
_COMPLEXITY_ORDER = {
    "1": 0,
    "logn": 1,
    "n": 2,
    "nlogn": 3,
    "n2": 4,
    "n3": 5,
    "2n": 6,
    "nk": 6,
}
_ORDER_LABEL = {v: k for k, v in _COMPLEXITY_ORDER.items()}


@dataclass
class Finding:
    section: str  # 命中的段落标题（用于步骤定位）
    error_type: str  # taxonomy code
    detail: str
    evidence: str
    confidence: float

    def to_dict(self) -> dict:
        return {
            "section": self.section,
            "error_type": self.error_type,
            "error_type_label": CODE_TO_LABEL.get(self.error_type, self.error_type),
            "detail": self.detail,
            "evidence": self.evidence[:300],
            "confidence": round(self.confidence, 2),
            "source": "rule",
        }


def _normalize_complexity(expr: str) -> str | None:
    """把 'O(n^2)' / 'O(n²)' / 'O(N log N)' 等写法归一到等级 key。"""
    s = expr.lower().replace(" ", "").replace("²", "^2").replace("³", "^3")
    s = s.replace("**", "^")
    if re.fullmatch(r"o?\(?1\)?", s):
        return "1"
    if "log" in s and re.search(r"n\s*\*?\s*log", s):
        return "nlogn"
    if "log" in s:
        return "logn"
    m = re.search(r"n\s*\^\s*(\d)", s)
    if m:
        k = int(m.group(1))
        return {1: "n", 2: "n2", 3: "n3"}.get(k, "nk")
    if re.search(r"2\s*\^\s*n", s):
        return "2n"
    if re.fullmatch(r"o?\(?n\)?", s) or re.search(r"\bn\b", s):
        return "n"
    return None


_CLAIM_RE = re.compile(
    r"(?:时间|time)[^\n]{0,20}?复杂度[^\n]{0,10}?[:：是为]?\s*(O\([^)\n]{1,24}\)|O\([^)\n]{1,24}\))",
    re.I,
)
_ANY_BIG_O_RE = re.compile(r"O\s*\(([^)\n]{1,24})\)", re.I)


def extract_claimed_complexity(section_text: str) -> str | None:
    m = _CLAIM_RE.search(section_text or "")
    if m:
        got = _normalize_complexity(m.group(1))
        if got:
            return got
    # 退而求其次：段落里出现的第一个 O(...)
    for m in _ANY_BIG_O_RE.finditer(section_text or ""):
        got = _normalize_complexity(f"O({m.group(1)})")
        if got:
            return got
    return None


class _LoopDepthVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.max_depth = 0
        self.recursive_funcs: set[str] = set()
        self._depth = 0
        self._stack: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def _visit_loop(self, node: ast.AST) -> None:
        self._depth += 1
        self.max_depth = max(self.max_depth, self._depth)
        self.generic_visit(node)
        self._depth -= 1

    def visit_For(self, node: ast.For) -> None:
        self._visit_loop(node)

    def visit_While(self, node: ast.While) -> None:
        self._visit_loop(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and self._stack:
            if node.func.id == self._stack[-1]:
                self.recursive_funcs.add(node.func.id)
        self.generic_visit(node)


def analyze_code_structure(code: str) -> dict:
    """静态分析：最大循环嵌套深度、是否存在递归。"""
    info = {"max_loop_depth": 0, "recursive": False, "parse_error": None}
    if not code.strip():
        info["parse_error"] = "代码为空"
        return info
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        info["parse_error"] = f"SyntaxError: {exc.msg}"
        return info
    visitor = _LoopDepthVisitor()
    visitor.visit(tree)
    info["max_loop_depth"] = visitor.max_depth
    info["recursive"] = bool(visitor.recursive_funcs)
    return info


def _depth_to_complexity(depth: int) -> str:
    return {0: "1", 1: "n", 2: "n2", 3: "n3"}.get(depth, "nk")


# ------------------------------------------------------------------ 边界关键词

_EDGE_KEYWORDS = {
    "空": ["空", "empty", "长度为 0", "len == 0", "len()==0"],
    "单元素": ["单元素", "只有一个", "长度为 1", "单个元素"],
    "重复元素": ["重复", "duplicate", "相同元素"],
    "负数": ["负数", "negative"],
    "边界": ["边界", "edge", "极值", "溢出"],
}


def mentions_edge(section_text: str) -> list[str]:
    text = (section_text or "").lower()
    return [k for k, pats in _EDGE_KEYWORDS.items() if any(p.lower() in text for p in pats)]


def _failure_kind(result) -> set[str]:
    """从失败用例的输入推断它属于哪类边界。"""
    kinds: set[str] = set()
    args = result.args or []
    for a in args:
        if isinstance(a, (list, str, dict, tuple)) and len(a) == 0:
            kinds.add("空")
        if isinstance(a, (list, str, tuple)) and len(a) == 1:
            kinds.add("单元素")
        if isinstance(a, list) and len(a) != len(set(map(repr, a))):
            kinds.add("重复元素")
        if isinstance(a, int) and a < 0:
            kinds.add("负数")
        if isinstance(a, list) and any(isinstance(x, int) and x < 0 for x in a):
            kinds.add("负数")
    return kinds


# ------------------------------------------------------------------ 主分析


def analyze(
    problem: dict,
    parsed: ParsedSolution,
    code: str,
    public: SuiteResult,
    adversarial: SuiteResult,
) -> tuple[list[Finding], dict]:
    """产出规则层发现 + 供 Critic 使用的信号摘要。"""
    findings: list[Finding] = []
    struct = analyze_code_structure(code)

    # ---- 结构完整性
    for title in parsed.missing_titles:
        findings.append(
            Finding(
                section=title,
                error_type="INCONSISTENT",
                detail=f"缺少规定段落「{title}」，过程不完整，无法对该步骤做有效评估。",
                evidence="段落解析结果缺失该标题",
                confidence=0.5,
            )
        )

    # ---- 代码实现：执行结果
    if public.total and not public.all_passed:
        failed = [r for r in public.results if not r.passed]
        statuses = {r.status for r in failed}
        if "timeout" in statuses:
            findings.append(
                Finding(
                    section="代码实现",
                    error_type="TERMINATION" if struct["recursive"] else "COMPLEXITY",
                    detail=(
                        f"公开测试中有 {sum(1 for r in failed if r.status == 'timeout')} 个用例超时，"
                        "存在终止条件或复杂度问题。"
                    ),
                    evidence=str([r.to_dict() for r in failed if r.status == "timeout"][:2]),
                    confidence=0.85,
                )
            )
        wrong = [r for r in failed if r.status == "wrong_answer"]
        if wrong:
            findings.append(
                Finding(
                    section="代码实现",
                    error_type="LOGIC",
                    detail=f"公开测试 {public.failed}/{public.total} 失败，实现结果与预期不符。",
                    evidence=str([r.to_dict() for r in wrong[:2]]),
                    confidence=0.9,
                )
            )
        errs = [r for r in failed if r.status in ("runtime_error", "import_error", "missing_entry")]
        if errs:
            findings.append(
                Finding(
                    section="代码实现",
                    error_type="LOGIC",
                    detail="代码执行抛出异常或无法载入。",
                    evidence=str([r.to_dict() for r in errs[:2]]),
                    confidence=0.95,
                )
            )

    # ---- 伪正确：公开过、对抗挂
    if (
        public.total
        and public.all_passed
        and adversarial.total
        and not adversarial.all_passed
    ):
        failed = [r for r in adversarial.results if not r.passed]
        findings.append(
            Finding(
                section="代码实现",
                error_type="FALSE_POSITIVE",
                detail=(
                    f"公开测试全部通过（{public.passed}/{public.total}），"
                    f"但对抗测试 {adversarial.failed}/{adversarial.total} 失败——"
                    "典型的“结果看似正确、实现逻辑有缺陷”的伪正确样本。"
                ),
                evidence=str([r.to_dict() for r in failed[:2]]),
                confidence=0.95,
            )
        )

        # 定位边界处理上的疏漏。优先使用题集声明的必答边界；
        # 没有声明时才退回“从失败用例输入反推边界类型”的启发式。
        required = problem.get("required_edge_cases") or []
        edge_section = parsed.get("关键边界与处理策略")
        edge_text = (edge_section.content if edge_section else "") or ""

        mentioned = mentions_edge(edge_text)

        if required:
            missing_req = [k for k in required if k.lower() not in edge_text.lower()]
            if missing_req:
                findings.append(
                    Finding(
                        section="关键边界与处理策略",
                        error_type="EDGE",
                        detail=(
                            f"本题要求的边界情形 {missing_req} 在「关键边界与处理策略」中未被讨论；"
                            "对抗测试已证明这些情形处理失败。"
                        ),
                        evidence=(
                            f"已提及：{mentioned if mentioned else '（无）'}；"
                            f"缺失：{missing_req}"
                        ),
                        confidence=0.85,
                    )
                )
        else:
            kinds: set[str] = set()
            for r in failed:
                kinds |= _failure_kind(r)
            uncovered = {k for k in kinds if k not in mentioned}
            if uncovered:
                findings.append(
                    Finding(
                        section="关键边界与处理策略",
                        error_type="EDGE",
                        detail=(
                            f"对抗测试暴露出未处理的边界情形：{'、'.join(sorted(uncovered))}；"
                            f"过程文档中被提及的边界仅包括：{'、'.join(mentioned) if mentioned else '（无）'}。"
                        ),
                        evidence=str([r.to_dict() for r in failed[:2]]),
                        confidence=0.8,
                    )
                )

    # ---- 复杂度：声称 vs 静态结构
    cx_section = parsed.get("复杂度分析")
    claimed = extract_claimed_complexity(cx_section.content if cx_section else "")
    expected_cx = (problem.get("expected_complexity") or {}).get("time")
    expected_key = _normalize_complexity(expected_cx) if expected_cx else None

    if claimed and not struct["parse_error"]:
        detected_key = _depth_to_complexity(struct["max_loop_depth"])
        d_order = _COMPLEXITY_ORDER.get(detected_key, 0)
        c_order = _COMPLEXITY_ORDER.get(claimed, 0)
        if d_order > c_order:
            findings.append(
                Finding(
                    section="复杂度分析",
                    error_type="COMPLEXITY",
                    detail=(
                        f"声称时间复杂度 {_ORDER_LABEL.get(c_order, claimed)}，"
                        f"但代码中检测到最大循环嵌套深度为 {struct['max_loop_depth']}，"
                        f"实际下界约为 {_ORDER_LABEL.get(d_order, detected_key)}。"
                    ),
                    evidence=f"AST 静态分析：max_loop_depth={struct['max_loop_depth']}, recursive={struct['recursive']}",
                    confidence=0.7,
                )
            )
        elif expected_key and c_order > _COMPLEXITY_ORDER.get(expected_key, 0):
            findings.append(
                Finding(
                    section="复杂度分析",
                    error_type="ALGO",
                    detail=(
                        f"声称复杂度 {_ORDER_LABEL.get(c_order, claimed)} 劣于题目期望的 "
                        f"{expected_cx}，算法选型未达要求。"
                    ),
                    evidence=f"期望 {expected_cx}，声称 {_ORDER_LABEL.get(c_order, claimed)}",
                    confidence=0.6,
                )
            )
    elif not claimed and cx_section is not None:
        findings.append(
            Finding(
                section="复杂度分析",
                error_type="COMPLEXITY",
                detail="复杂度分析段落未给出明确的 O(...) 形式结论，无法验证。",
                evidence=(cx_section.content or "")[:200],
                confidence=0.4,
            )
        )

    # ---- 自测说明：与执行结果是否一致
    st_section = parsed.get("自测说明")
    if st_section is not None:
        text = st_section.content or ""
        claims_all_pass = bool(
            re.search(r"(全部|所有|均|都)\s*(通过|正确|pass)", text)
            or re.search(r"passed?", text, re.I)
        )
        if claims_all_pass and adversarial.total and not adversarial.all_passed:
            findings.append(
                Finding(
                    section="自测说明",
                    error_type="HALLUCINATION",
                    detail="自测说明声称用例全部通过，但对抗测试实际存在失败用例，自测结论与事实不符。",
                    evidence=text[:200],
                    confidence=0.75,
                )
            )

    signals = {
        "problem_id": problem.get("id"),
        "entry_point": problem.get("entry_point"),
        "max_loop_depth": struct["max_loop_depth"],
        "recursive": struct["recursive"],
        "parse_error": struct["parse_error"],
        "claimed_complexity": claimed,
        "expected_complexity": expected_cx,
        "public": public.to_dict(),
        "adversarial": adversarial.to_dict(),
        "rule_findings": [f.to_dict() for f in findings],
    }
    return findings, signals
