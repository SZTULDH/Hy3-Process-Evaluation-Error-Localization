"""错误类型体系（对应 PROPOSAL 第 8.3 节）。

每个类型标注了它通常“首发”于哪个过程步骤，用于把 Critic 判定的错误类型
与步骤级定位对齐。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorType:
    code: str
    label: str
    description: str
    typical_section: str  # 该错误通常在哪个步骤上首次暴露


ERROR_TYPES: dict[str, ErrorType] = {
    t.code: t
    for t in [
        ErrorType(
            "MISREAD", "题意误读",
            "对输入输出格式、约束条件或语义理解错误",
            "解题思路",
        ),
        ErrorType(
            "ALGO", "算法选择错误",
            "选用的算法或数据结构无法正确/高效地解决问题",
            "解题思路",
        ),
        ErrorType(
            "COMPLEXITY", "复杂度分析错误",
            "声称的时间/空间复杂度与实际实现不符",
            "复杂度分析",
        ),
        ErrorType(
            "EDGE", "边界处理缺失",
            "未处理空输入、单元素、极值、重复元素等特殊情况",
            "关键边界与处理策略",
        ),
        ErrorType(
            "LOGIC", "实现逻辑缺陷",
            "代码存在逻辑漏洞（即使公开测试通过）",
            "代码实现",
        ),
        ErrorType(
            "STATE", "状态管理错误",
            "变量更新、累积量维护、状态重置有误",
            "代码实现",
        ),
        ErrorType(
            "TERMINATION", "终止条件错误",
            "循环或递归的终止/边界条件有误，导致死循环或漏解",
            "代码实现",
        ),
        ErrorType(
            "HALLUCINATION", "幻觉",
            "编造不存在的 API、复杂度结论或测试结果",
            "自测说明",
        ),
        ErrorType(
            "INCONSISTENT", "描述与实现不一致",
            "文字思路正确但代码未体现，或反之",
            "代码实现",
        ),
        ErrorType(
            "FALSE_POSITIVE", "测试覆盖不足导致的伪正确",
            "公开测试通过但对抗测试失败，实现存在未被覆盖的缺陷",
            "代码实现",
        ),
    ]
}

LABEL_TO_CODE = {t.label: t.code for t in ERROR_TYPES.values()}
CODE_TO_LABEL = {t.code: t.label for t in ERROR_TYPES.values()}


def normalize_error_type(raw: str) -> str | None:
    """把模型输出的中文标签或英文代码统一成 code。"""
    if not raw:
        return None
    key = raw.strip()
    if key in ERROR_TYPES:
        return key
    if key in LABEL_TO_CODE:
        return LABEL_TO_CODE[key]
    for code, t in ERROR_TYPES.items():
        if t.label in key or key in t.label:
            return code
    return None


def label_of(code: str) -> str:
    return CODE_TO_LABEL.get(code, code)
