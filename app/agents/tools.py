"""Agent 可用的 Hy3 function-calling 工具定义与处理器。

Checker / Critic 在需要动态调试时可通过 `llm.chat_with_tools` 调用：
- 交错式：同一 user 轮内回填 reasoning_content + tool 结果
- 保留式：跨 user 轮时 messages 中保留全部历史 reasoning_content（调用方负责）
"""

from __future__ import annotations

import json
from typing import Any

from ..sandbox.forensics import (
    find_case_index,
    forensic_summary,
    run_forensics,
    run_forensics_case,
)
from ..sandbox.runner import run_suite, suite_kwargs

# ---------------------------------------------------------------------------
# OpenAI tools schema
# ---------------------------------------------------------------------------

CHECKER_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "run_public_tests",
            "description": (
                "在沙盒中对提交代码跑公开测试用例，返回通过率与失败详情。"
                "类级题目（kind=class）会自动构造实例并在同一实例上顺序调用各方法。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "待测 Python 源码"},
                    "entry_point": {"type": "string", "description": "函数入口名"},
                },
                "required": ["code", "entry_point"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_adversarial_tests",
            "description": (
                "在沙盒中对提交代码跑对抗测试用例，用于发现伪正确。"
                "类级题目（kind=class）会自动构造实例并在同一实例上顺序调用各方法。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "待测 Python 源码"},
                    "entry_point": {"type": "string", "description": "函数入口名"},
                },
                "required": ["code", "entry_point"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_forensics",
            "description": (
                "对失败入参做源码级调试取证（轨迹、局部变量、出错行）。"
                "类级题目会先回放该用例之前的同套件用例，保证 self 状态与真实执行一致。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "entry_point": {"type": "string"},
                    "args": {
                        "type": "array",
                        "description": "失败用例的 args 列表",
                        "items": {},
                    },
                    "method": {
                        "type": "string",
                        "description": "类级题目：该用例调用的方法名",
                    },
                },
                "required": ["code", "entry_point", "args"],
            },
        },
    },
]


def make_checker_handlers(problem: dict) -> dict[str, Any]:
    """绑定题目测试数据的工具执行器。"""

    public = problem.get("public_tests") or []
    adversarial = problem.get("adversarial_tests") or []
    kind = (problem.get("kind") or "function").lower()
    suite_kw = suite_kwargs(problem)

    def run_public_tests(args: dict) -> str:
        code = args.get("code") or ""
        entry = args.get("entry_point") or problem.get("entry_point", "")
        suite = run_suite(code, entry, public, **suite_kw)
        return json.dumps(suite.to_dict(), ensure_ascii=False)

    def run_adversarial_tests(args: dict) -> str:
        code = args.get("code") or ""
        entry = args.get("entry_point") or problem.get("entry_point", "")
        suite = run_suite(code, entry, adversarial, **suite_kw)
        return json.dumps(suite.to_dict(), ensure_ascii=False)

    def run_forensics_tool(args: dict) -> str:
        code = args.get("code") or ""
        entry = args.get("entry_point") or problem.get("entry_point", "")
        case_args = args.get("args") or []
        method = (args.get("method") or "").strip() or None

        fo = None
        if kind == "class":
            # 能对上用例时走"回放前置状态"的取证，状态才与真实执行一致
            for cases in (adversarial, public):
                idx = find_case_index(cases, case_args, method)
                if idx is not None:
                    fo = run_forensics_case(code, problem, cases, idx)
                    break
        if fo is None:
            fo = run_forensics(
                code, entry, case_args,
                class_name=suite_kw["class_name"],
                init_args=suite_kw["init_args"],
                init_kwargs=suite_kw["init_kwargs"],
                method=method if kind == "class" else None,
            )

        return json.dumps(
            {"forensic": fo, "summary": forensic_summary(fo)},
            ensure_ascii=False,
        )

    return {
        "run_public_tests": run_public_tests,
        "run_adversarial_tests": run_adversarial_tests,
        "run_forensics": run_forensics_tool,
    }
