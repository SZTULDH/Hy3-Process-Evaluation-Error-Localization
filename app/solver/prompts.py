"""Solver 提示词模板。

强制模型按固定五段输出，是为了让过程可被稳定切分与逐步评估——
这是“过程评估”能做细粒度的前提。

题目分两种形态，提示词必须跟着分叉，否则会出误导：
* `kind=function` —— 交付一个函数
* `kind=class`    —— 交付一个**类**（含 `__init__` 与若干方法）
早先的提示词统一写死“包含且仅包含函数定义，函数名必须与入口函数名一致”，
类级题会因此只产出同名方法片段，评估时直接 `missing_entry`——那是提示词的
问题，不是模型能力问题。
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
- 每一段都要有实质内容，不要写\"略\"。
"""

SOLVER_SYSTEM_PROMPT_CLASS = """[ROLE=solver]
你是一名资深 Python 工程师。本题要求交付一个**类**，而不是一个函数。
解题时必须严格按以下五个段落输出，**不能省略、不能改名、不能新增段落**。

## 1. 解题思路
说明你选择的算法与数据结构，以及状态如何在**多次方法调用之间**保存与迁移。

## 2. 复杂度分析
明确给出每个方法的均摊时间复杂度与带来的空间开销，必须写成 O(...) 形式，并说明推理依据。

## 3. 关键边界与处理策略
列举本题所有关键边界情形（如重复调用、乱序调用、达到容量/次数上限、非法状态迁移、
复位时机等），并逐一说明处理方式。

## 4. 代码实现
给出一个完整的代码块，包含**完整的类定义**：类名、`__init__`（含全部构造参数）
以及题目要求的每一个方法。签名必须与题目给出一致。

## 5. 自测说明
说明你如何验证实现，并如实说明验证覆盖范围。不要臆造测试结果。

要求：
- 第 4 段的代码块使用 ```python 包裹，类名必须与题目给定的类名完全一致。
- **禁止**把方法写成模块级函数，也**禁止**只给出某个方法而不给出类本身。
- 不要输出这五个段落之外的额外章节。
- 每一段都要有实质内容，不要写\"略\"。
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

SOLVER_USER_TEMPLATE_CLASS = """## 题目描述
{description}

## 交付形态
一个**类** `{class_name}`，其中需要实现方法 `{entry_point}`（其余方法以题目描述为准）。

## 类签名
```python
{function_signature}
```

## 构造参数
{init_args}

## 约束
{constraints}

## 测试用例示例（同一实例上按序调用）
{examples}

请按规定的五段格式输出完整解题过程，第 4 段必须是完整的类定义。
"""


def render_examples(tests: list[dict], limit: int = 3, is_class: bool = False) -> str:
    lines = []
    for case in tests[:limit]:
        args = case.get("args", [])
        expected = case.get("expected")
        kwargs = case.get("kwargs") or {}
        if is_class:
            method = case.get("method") or "（默认方法）"
            call = f"{method}({', '.join(repr(a) for a in args)})"
            if kwargs:
                call = call[:-1] + (", " if args else "") + \
                    ", ".join(f"{k}={v!r}" for k, v in kwargs.items()) + ")"
            prefix = "重建实例后 " if case.get("reset") else ""
            lines.append(f"- {prefix}`{call}` -> 期望返回 `{expected}`")
        else:
            lines.append(f"- 输入 `{args}` -> 期望输出 `{expected}`")
    return "\n".join(lines) if lines else "（无）"


def render_init_args(problem: dict) -> str:
    """类级题的构造参数说明；没有时如实写「无（无参构造）」。"""
    args = problem.get("init_args") or []
    kwargs = problem.get("init_kwargs") or {}
    if not args and not kwargs:
        return "无（无参构造）"
    parts = [repr(a) for a in args]
    parts += [f"{k}={v!r}" for k, v in kwargs.items()]
    return "`" + ", ".join(parts) + "`"


def is_class_problem(problem: dict) -> bool:
    return (problem.get("kind") or "function").lower() == "class"


def build_solver_messages(problem: dict) -> str:
    """按题目形态选择提示词；类级题用类模板，避免把类写成了函数。"""
    is_class = is_class_problem(problem)
    entry = problem.get("entry_point", "")
    signature = problem.get("function_signature", "")
    if is_class and not signature:
        # 题面没给签名时补一个类骨架，模型据此才知道要交付类
        cls = problem.get("class_name") or entry
        signature = f"class {cls}:\n    def {entry}(self, ...): ..."

    template = SOLVER_USER_TEMPLATE_CLASS if is_class else SOLVER_USER_TEMPLATE
    return template.format(
        description=problem.get("description", ""),
        function_signature=signature,
        entry_point=entry,
        class_name=problem.get("class_name") or entry,
        init_args=render_init_args(problem),
        constraints=problem.get("constraints", "无特殊约束"),
        examples=render_examples(problem.get("public_tests", []), is_class=is_class),
    )


def build_solver_system(problem: dict) -> str:
    return SOLVER_SYSTEM_PROMPT_CLASS if is_class_problem(problem) else SOLVER_SYSTEM_PROMPT
