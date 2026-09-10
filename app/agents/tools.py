"""Agent 可用的 Hy3 function-calling 工具定义与处理器。"""

from __future__ import annotations

import json
from typing import Any

from ..sandbox.forensics import forensic_summary, run_forensics
from ..sandbox.runner import run_suite

CHECKER_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "run_public_tests",
            "description": "在沙盒中对提交代码跑公开测试用例，返回通过率与失败详情。",
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
            "description": "在沙盒中对提交代码跑对抗测试用例，用于发现伪正确。",
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
            "description": "对失败入参做源码级调试取证（轨迹、局部变量、出错行）。",
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
                },
                "required": ["code", "entry_point", "args"],
            },
        },
    },
]


def make_checker_handlers(problem: dict) -> dict[str, Any]:
    public = problem.get("public_tests") or []
    adversarial = problem.get("adversarial_tests") or []

    def run_public_tests(args: dict) -> str:
        code = args.get("code") or ""
        entry = args.get("entry_point") or problem.get("entry_point", "")
        suite = run_suite(code, entry, public)
        return json.dumps(suite.to_dict(), ensure_ascii=False)

    def run_adversarial_tests(args: dict) -> str:
        code = args.get("code") or ""
        entry = args.get("entry_point") or problem.get("entry_point", "")
        suite = run_suite(code, entry, adversarial)
        return json.dumps(suite.to_dict(), ensure_ascii=False)

    def run_forensics_tool(args: dict) -> str:
        code = args.get("code") or ""
        entry = args.get("entry_point") or problem.get("entry_point", "")
        case_args = args.get("args") or []
        fo = run_forensics(code, entry, case_args)
        return json.dumps(
            {"forensic": fo, "summary": forensic_summary(fo)},
            ensure_ascii=False,
        )

    return {
        "run_public_tests": run_public_tests,
        "run_adversarial_tests": run_adversarial_tests,
        "run_forensics": run_forensics_tool,
    }
