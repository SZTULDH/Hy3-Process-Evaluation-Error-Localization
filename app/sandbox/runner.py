"""代码执行沙盒：函数级 / 类级测试套件，带超时与输出限制。"""

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
STARTUP_OVERHEAD = 5.0
DEBUG = bool(os.getenv("SANDBOX_DEBUG"))


@dataclass
class TestResult:
    index: int
    args: list
    expected: Any
    status: str
    actual: Any = None
    error: str = ""
    traceback: str = ""
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0
    method: str | None = None

    @property
    def passed(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> dict:
        d = {
            "index": self.index,
            "args": self.args,
            "expected": self.expected,
            "status": self.status,
            "actual": self.actual,
            "error": self.error[:300],
            "duration_ms": round(self.duration_ms, 3),
        }
        if self.method:
            d["method"] = self.method
        if self.status == "ok":
            return d
        lim = 100000 if DEBUG else 600
        if self.traceback:
            d["traceback"] = self.traceback[-lim:]
        if self.stdout:
            d["stdout"] = self.stdout[-lim:]
        if self.stderr:
            d["stderr"] = self.stderr[-lim:]
        return d

    def explain(self) -> str:
        lines = [
            f"[{self.status}] 用例 #{self.index}",
            f"  方法  : {self.method}" if self.method else None,
            f"  入参  : {self.args!r}",
            f"  期望  : {self.expected!r}",
            f"  实际  : {self.actual!r}",
            f"  耗时  : {self.duration_ms:.3f} ms",
        ]
        lines = [x for x in lines if x]
        if self.error:
            lines.append(f"  错误  : {self.error}")
        if self.traceback:
            lines.append("  回溯  :")
            lines.extend("    " + ln for ln in self.traceback.strip().splitlines()[-12:])
        return "\n".join(lines)


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


def values_equal(actual: Any, expected: Any) -> bool:
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


def _write_code_file(code: str, workdir: Path) -> Path:
    path = workdir / "candidate.py"
    path.write_text(code, encoding="utf-8")
    return path


def run_suite(
    code: str,
    entry_point: str,
    tests: list[dict],
    timeout: float = SANDBOX_TIMEOUT,
    *,
    kind: str = "function",
    class_name: str | None = None,
    init_args: list | None = None,
    init_kwargs: dict | None = None,
) -> SuiteResult:
    """执行测试套件。kind=class 时构造类并顺序调用 method（同实例）。"""
    if not tests:
        return SuiteResult()

    with tempfile.TemporaryDirectory(prefix="hy3sbx_") as tmp:
        workdir = Path(tmp)
        code_path = _write_code_file(code, workdir)

        timeouts = [float(c.get("timeout", timeout)) for c in tests]
        spec_tests = [dict(c, timeout=t) for c, t in zip(tests, timeouts)]
        budget = sum(timeouts) + STARTUP_OVERHEAD

        spec = {
            "code_path": str(code_path),
            "kind": kind or "function",
            "entry_point": entry_point,
            "class_name": class_name or entry_point,
            "init_args": init_args or [],
            "init_kwargs": init_kwargs or {},
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
        if state.get("watchdog_timeout") or returncode == 2:
            timed_out = True
        completed = {int(r.get("index", -1)): r for r in state.get("completed", [])}
        hanging = state.get("in_progress")

        suite = SuiteResult()
        for i, case in enumerate(tests):
            expected = case.get("expected")
            args = case.get("args", [])
            method = case.get("method")
            payload = completed.get(i)

            if payload is None:
                status = "timeout" if (timed_out and i == hanging) else "not_run"
                suite.results.append(
                    TestResult(
                        i,
                        args,
                        expected,
                        status,
                        error=(
                            "超过 %ss 未返回" % timeouts[i]
                            if status == "timeout"
                            else "前序用例超时，未执行"
                        ),
                        duration_ms=wall_ms if status == "timeout" else 0.0,
                        method=method,
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
                        stdout=payload.get("stdout", ""),
                        stderr=payload.get("stderr", ""),
                        duration_ms=float(payload.get("duration_ms", 0.0)),
                        method=payload.get("method") or method,
                    )
                )
                continue

            actual = payload.get("result")
            ok = values_equal(actual, expected)
            suite.results.append(
                TestResult(
                    i,
                    args,
                    expected,
                    "ok" if ok else "wrong_answer",
                    actual=actual,
                    stdout=payload.get("stdout", ""),
                    stderr=payload.get("stderr", ""),
                    duration_ms=float(payload.get("duration_ms", 0.0)),
                    method=payload.get("method") or method,
                )
            )

    return suite


def suite_kwargs(problem: dict) -> dict:
    """从题目 JSON 抽出 ``run_suite`` 需要的类级参数。

    函数级题目退化成 ``kind="function"`` + 空类名，与旧行为一致；
    调用方统一用它，避免各处漏传导致类级题全部 missing_entry。
    """
    return {
        "kind": problem.get("kind") or "function",
        "class_name": problem.get("class_name"),
        "init_args": problem.get("init_args"),
        "init_kwargs": problem.get("init_kwargs"),
    }


def run_problem(
    code: str,
    problem: dict,
    timeout: float = SANDBOX_TIMEOUT,
) -> dict:
    """按题目 JSON 跑 public / adversarial。"""
    entry = problem.get("entry_point") or ""
    common = dict(suite_kwargs(problem), timeout=timeout)
    pub = run_suite(code, entry, problem.get("public_tests") or [], **common)
    out = {"public": pub.to_dict()}
    adv = problem.get("adversarial_tests") or []
    if adv:
        out["adversarial"] = run_suite(code, entry, adv, **common).to_dict()
    return out


def _read_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"completed": [], "in_progress": None}


def check_syntax(code: str) -> str | None:
    try:
        compile(code, "<candidate>", "exec")
        return None
    except SyntaxError as exc:
        return f"SyntaxError: {exc.msg} (行 {exc.lineno})"


def debug_run(
    code: str,
    entry_point: str,
    args: list | None = None,
    kwargs: dict | None = None,
    timeout: float = SANDBOX_TIMEOUT,
) -> TestResult:
    res = run_suite(
        code,
        entry_point,
        [{"args": args or [], "expected": None, "kwargs": kwargs or {}, "timeout": timeout}],
    )
    if not res.results:
        return TestResult(0, args or [], None, "not_run", error="沙盒未产出结果")
    r = res.results[0]
    if r.status == "wrong_answer" and r.expected is None:
        r.status = "ok"
    return r
