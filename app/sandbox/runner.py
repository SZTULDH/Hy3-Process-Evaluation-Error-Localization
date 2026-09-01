"""代码执行沙盒：每个测试用例一个独立子进程，带超时与输出限制。

设计取舍：
* 一个用例一个进程 —— 牺牲启动开销，换取“哪个用例挂了 / 超时”的精确归因。
* 超时后 kill 整个进程树，避免残留死循环线程。
* 危险模块与 open/exec 等在 harness 内做尽力而为的限制；
  但这不是安全边界，处理不可信代码应在容器内进行。
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import (
    SANDBOX_MAX_OUTPUT_CHARS,
    SANDBOX_RECURSION_LIMIT,
    SANDBOX_TIMEOUT,
)

HARNESS_PATH = Path(__file__).with_name("_harness.py")

# 子进程启动与代码载入的固定开销（秒），计入超时预算。
# 实测启动约 1s，留 5s 作为低配机器上的安全余量。
STARTUP_OVERHEAD = 5.0


# ------------------------------------------------------------------ 数据结构


@dataclass
class TestResult:
    index: int
    args: list
    expected: Any
    status: str  # ok | wrong_answer | runtime_error | timeout | missing_entry | unserializable
    actual: Any = None
    error: str = ""
    traceback: str = ""
    duration_ms: float = 0.0

    @property
    def passed(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "args": self.args,
            "expected": self.expected,
            "status": self.status,
            "actual": self.actual,
            "error": self.error[:300],
            "duration_ms": round(self.duration_ms, 1),
        }


@dataclass
class SuiteResult:
    results: list[TestResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def all_passed(self) -> bool:
        return self.total > 0 and self.failed == 0

    def failure_samples(self, limit: int = 2) -> list[dict]:
        return [r.to_dict() for r in self.results if not r.passed][:limit]

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "all_passed": self.all_passed,
            "failure_samples": self.failure_samples(),
        }


# ------------------------------------------------------------------ 结果比较


def values_equal(actual: Any, expected: Any) -> bool:
    """宽松但不含糊的结果比较。

    * bool 与 int 不互换（`True` 不等于 `1`）——避免类型混淆被判为通过
    * 浮点使用相对+绝对容差
    * list / tuple 视为同构序列
    """
    if isinstance(expected, bool) or isinstance(actual, bool):
        if isinstance(expected, bool) and isinstance(actual, bool):
            return actual == expected
        return False

    if isinstance(expected, float) or isinstance(actual, float):
        try:
            return math.isclose(float(actual), float(expected), rel_tol=1e-9, abs_tol=1e-9)
        except (TypeError, ValueError):
            return False

    if isinstance(expected, dict) and isinstance(actual, dict):
        if set(expected.keys()) != set(actual.keys()):
            return False
        return all(values_equal(actual[k], expected[k]) for k in expected)

    if isinstance(expected, (list, tuple)) and isinstance(actual, (list, tuple)):
        if len(expected) != len(actual):
            return False
        return all(values_equal(a, e) for a, e in zip(actual, expected))

    return actual == expected


# ------------------------------------------------------------------ 执行


def _write_code_file(code: str, workdir: Path) -> Path:
    path = workdir / "candidate.py"
    path.write_text(code, encoding="utf-8")
    return path


def run_suite(
    code: str,
    entry_point: str,
    tests: list[dict],
    timeout: float = SANDBOX_TIMEOUT,
) -> SuiteResult:
    """在沙盒中执行一组测试用例。

    一个套件只起一个子进程（启动开销在 Windows 上约 1s/次，是主要成本），
    靠 harness 的增量落盘保留逐用例的失败归因。
    """
    if not tests:
        return SuiteResult()

    with tempfile.TemporaryDirectory(prefix="hy3sbx_") as tmp:
        workdir = Path(tmp)
        code_path = _write_code_file(code, workdir)

        # 把解析后的超时写回用例副本——子进程只看得到 spec，
        # 不写回去的话看门狗永远拿不到 deadline
        timeouts = [float(c.get("timeout", timeout)) for c in tests]
        spec_tests = [dict(c, timeout=t) for c, t in zip(tests, timeouts)]
        # 总预算 = 各用例超时之和 + 进程启动/载入的固定开销
        budget = sum(timeouts) + STARTUP_OVERHEAD

        spec = {
            "code_path": str(code_path),
            "entry_point": entry_point,
            "tests": spec_tests,
            "max_output_chars": SANDBOX_MAX_OUTPUT_CHARS,
            "recursion_limit": SANDBOX_RECURSION_LIMIT,
        }
        spec_path = workdir / "spec.json"
        spec_path.write_text(
            json.dumps(spec, ensure_ascii=False, default=repr), encoding="utf-8"
        )
        out_path = workdir / "out.json"

        timed_out = False
        start = time.perf_counter()
        returncode: int | None = None
        try:
            proc = subprocess.run(
                # 注意：这里刻意不使用 -S。实测在本机（Windows）上它对启动耗时
                # 没有可观测收益（瓶颈是进程创建本身），却可能影响到某些环境下
                # 由 site 模块建立的标准库搜索路径，属于无收益的纯风险。
                [sys.executable, "-I", str(HARNESS_PATH), str(spec_path), str(out_path)],
                capture_output=True,
                timeout=budget,
                cwd=str(workdir),
                stdin=subprocess.DEVNULL,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            returncode = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
        wall_ms = (time.perf_counter() - start) * 1000

        state = _read_state(out_path)
        # 子进程内看门狗命中（返回码 2）同样说明有用例超时
        if state.get("watchdog_timeout") or returncode == 2:
            timed_out = True
        completed = {int(r.get("index", -1)): r for r in state.get("completed", [])}
        hanging = state.get("in_progress")

        suite = SuiteResult()
        for i, case in enumerate(tests):
            expected = case.get("expected")
            args = case.get("args", [])
            payload = completed.get(i)

            if payload is None:
                # 未产出结果：要么就是挂住的那个，要么是被它连累没跑到的
                status = "timeout" if (timed_out and i == hanging) else "not_run"
                suite.results.append(
                    TestResult(
                        i,
                        args,
                        expected,
                        status,
                        error="超过 %ss 未返回" % timeouts[i]
                        if status == "timeout"
                        else "前序用例超时，未执行",
                        duration_ms=wall_ms if status == "timeout" else 0.0,
                    )
                )
                continue

            if payload.get("status") != "ok":
                suite.results.append(
                    TestResult(
                        i,
                        args,
                        expected,
                        payload.get("status", "runtime_error"),
                        error=payload.get("error", ""),
                        traceback=payload.get("traceback", ""),
                        duration_ms=0.0,
                    )
                )
                continue

            actual = payload.get("result")
            if values_equal(actual, expected):
                suite.results.append(
                    TestResult(i, args, expected, "ok", actual=actual)
                )
            else:
                suite.results.append(
                    TestResult(i, args, expected, "wrong_answer", actual=actual)
                )

    return suite


def _read_state(path: Path) -> dict:
    """读取 harness 的增量结果文件；损坏或缺失时返回空状态。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"completed": [], "in_progress": None}


def check_syntax(code: str) -> str | None:
    """静态语法检查，返回错误信息或 None。"""
    try:
        compile(code, "<candidate>", "exec")
        return None
    except SyntaxError as exc:
        return f"SyntaxError: {exc.msg} (行 {exc.lineno})"
