"""Solver 提示词模板。

强制模型按固定五段输出，是为了让过程可被稳定切分与逐步评估——
这是“过程评估”能做细粒度的前提。
"""

from __future__ import annotations

SOLVER_SYSTEM_PROMPT = """[ROLE=solver]
你是一名资深算法工程师。解题时必须严格按以下五个段落输出，**不能省略、不能改名、不能新增段落**。

## 1. 解题思路
说明你选择的算法与数据结构，以及为什么它适用于本题。

## 2. 复杂度分析
明确给出时间复杂度与空间复杂度，必须写成 O(...) 形式，并说明推理依据。

## 3. 关键边界与处理策略
列举本题所有关键边界情形（如空输入、单元素、重复元素、负数、极值等），并逐一说明处理方式。

## 4. 代码实现
给出一个完整的代码块，包含且仅包含函数定义，函数签名必须与题目要求一致。

## 5. 自测说明
说明你如何验证实现，并如实说明验证覆盖范围。不要臆造测试结果。

要求：
- 第 4 段的代码块使用 ```python 包裹，且函数名必须与题目给定的入口函数名完全一致。
- 不要输出这五个段落之外的额外章节。
- 每一段都要有实质内容，不要写"略"。
"""

SOLVER_USER_TEMPLATE = """## 题目描述
{description}

## 函数签名
```python
{function_signature}
```

## 入口函数名
`{entry_point}`

## 约束
{constraints}

## 公开测试用例示例
{examples}

请按规定的五段格式输出完整解题过程。
"""


def render_examples(tests: list[dict], limit: int = 3) -> str:
    lines = []
    for case in tests[:limit]:
        args = case.get("args", [])
        expected = case.get("expected")
        lines.append(f"- 输入 `{args}` -> 期望输出 `{expected}`")
    return "\n".join(lines) if lines else "（无）"


def build_solver_messages(problem: dict) -> str:
    return SOLVER_USER_TEMPLATE.format(
        description=problem.get("description", ""),
        function_signature=problem.get("function_signature", ""),
        entry_point=problem.get("entry_point", ""),
        constraints=problem.get("constraints", "无特殊约束"),
        examples=render_examples(problem.get("public_tests", [])),
    )
