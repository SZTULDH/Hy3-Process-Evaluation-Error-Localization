"""调试层公用工具：帧序列化、安全 repr、行级执行轨迹记录。

被 _dbgharness.py（常驻调试子进程）与 debugger.py（客户端）共用。
本模块只依赖标准库，且必须能被同目录 import（子进程以脚本方式启动，
不经过 app 包）。
"""

from __future__ import annotations

import io
import reprlib
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field

# 单个变量 repr 的最大字符数，防止大列表/大字符串淹没输出
MAX_REPR = 200
# 默认保留的最近执行步数（用于无断点的事后回溯）
DEFAULT_TRACE_LIMIT = 2000


def safe_repr(value: object, limit: int = MAX_REPR) -> str:
    """repr 的防御版本：任意对象都不会抛异常、不会超长。"""
    try:
        text = repr(value)
    except BaseException as exc:  # noqa: BLE001
        return f"<repr failed: {type(exc).__name__}>"
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def safe_vars(mapping: object, limit: int = MAX_REPR, skip_dunder: bool = True) -> dict:
    """把 f_locals / f_globals 转成可 JSON 化的 {name: repr}。"""
    out: dict[str, str] = {}
    try:
        items = list(mapping.items())  # type: ignore[union-attr]
    except BaseException:  # noqa: BLE001
        return out
    for name, value in items:
        if skip_dunder and name.startswith("__"):
            continue
        try:
            out[name] = safe_repr(value, limit)
        except BaseException:  # noqa: BLE001
            out[name] = "<unserializable>"
    return out


def frame_snapshot(frame, include_locals: bool = True, limit: int = MAX_REPR) -> dict:
    """把一个栈帧序列化成可 JSON 化的字典。"""
    code = frame.f_code
    info = {
        "file": code.co_filename,
        "line": frame.f_lineno,
        "func": code.co_name,
    }
    if include_locals:
        info["locals"] = safe_vars(frame.f_locals, limit)
    return info


def stack_snapshot(frame, include_locals: bool = True, limit_frames: int = 30) -> list[dict]:
    """从当前帧向上回溯调用栈（index 0 = 当前帧）。

    只保留与最深层同文件的帧：用户看到的是候选代码自己的调用链，
    调试器/host 的内部帧（_run / threading 等）不混进来。
    """
    frames: list[dict] = []
    if frame is None:
        return frames
    root = frame.f_code.co_filename
    cur = frame
    while cur is not None and len(frames) < limit_frames:
        if cur.f_code.co_filename != root:
            break
        frames.append(frame_snapshot(cur, include_locals))
        cur = cur.f_back
    return frames


def source_line(filename: str, lineno: int) -> str:
    """取源码行文本，取不到返回空串。"""
    try:
        with open(filename, encoding="utf-8") as fh:
            lines = fh.readlines()
        if 1 <= lineno <= len(lines):
            return lines[lineno - 1].rstrip("\n")
    except BaseException:  # noqa: BLE001
        pass
    return ""


# ------------------------------------------------------------------ 轨迹记录


@dataclass
class Step:
    n: int
    line: int
    func: str
    depth: int
    locals: dict = field(default_factory=dict)
    ts_ms: float = 0.0
    stdout: str = ""

    def to_dict(self) -> dict:
        return {
            "n": self.n,
            "line": self.line,
            "func": self.func,
            "depth": self.depth,
            "locals": self.locals,
            "ts_ms": round(self.ts_ms, 3),
            "stdout": self.stdout,
        }


class TraceRecorder:
    """行级执行轨迹记录器（有界环形缓冲）。

    只保留最近 limit 步，避免长时间运行把内存吃光；`total` 记录真实步数，
    调用方可据此判断是否发生截断。
    """

    def __init__(self, limit: int = DEFAULT_TRACE_LIMIT) -> None:
        self.limit = limit
        self.steps: list[Step] = []
        self.total = 0
        self.truncated = False

    def record(self, n: int, frame, depth: int, stdout_delta: str = "") -> None:
        self.total += 1
        if len(self.steps) >= self.limit:
            self.steps.pop(0)
            self.truncated = True
        self.steps.append(
            Step(
                n=n,
                line=frame.f_lineno,
                func=frame.f_code.co_name,
                depth=depth,
                locals=safe_vars(frame.f_locals),
                ts_ms=time.perf_counter() * 1000,
                stdout=stdout_delta,
            )
        )

    def tail(self, k: int) -> list[dict]:
        return [s.to_dict() for s in self.steps[-k:]]


class _LimitedWriter(io.StringIO):
    """限制写入总量，防止死循环刷爆内存。"""

    def __init__(self, limit: int) -> None:
        super().__init__()
        self._limit = limit
        self._truncated = False

    def write(self, s: str) -> int:  # noqa: D102
        if self._truncated or self.tell() >= self._limit:
            self._truncated = True
            return 0
        return super().write(s[: self._limit - self.tell()])


# 尽力而为的危险模块拒绝列表（研究用途，非安全边界）
BLOCKED_MODULES = {
    "subprocess",
    "socket",
    "shutil",
    "ctypes",
    "multiprocessing",
    "requests",
    "urllib",
    "http",
    "ftplib",
    "smtplib",
    "pickle",
    "webbrowser",
}


def make_guarded_import():
    builtins_obj = __builtins__
    real_import = (
        builtins_obj["__import__"]
        if isinstance(builtins_obj, dict)
        else getattr(builtins_obj, "__import__")
    )

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        root = name.split(".")[0]
        if root in BLOCKED_MODULES:
            raise ImportError(f"沙盒禁止导入模块: {name}")
        return real_import(name, globals, locals, fromlist, level)

    return guarded_import


def build_sandbox_namespace(max_output: int = 20000, recursion_limit: int = 3000):
    """构造受限的执行命名空间（禁用 open/eval/exec/compile 与危险模块）。"""
    import builtins as _b

    ns: dict = {"__name__": "__candidate__"}
    safe_builtins = dict(vars(_b))
    safe_builtins["__import__"] = make_guarded_import()
    for name in ("open", "compile", "eval", "exec"):
        safe_builtins.pop(name, None)
    ns["__builtins__"] = safe_builtins
    sys.setrecursionlimit(recursion_limit)
    out = _LimitedWriter(max_output)
    err = _LimitedWriter(max_output)
    return ns, out, err
