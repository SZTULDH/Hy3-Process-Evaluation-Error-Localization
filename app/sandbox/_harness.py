"""子进程内执行的测试夹具（harness）。

由 runner.py 通过 `python -I _harness.py <spec.json> <out.json>` 调用。

关键设计：**增量落盘**。每跑完一个用例就把结果写回 out.json，并提前把
即将执行的用例下标写入 `in_progress`。这样即使某个用例死循环触发父进程
超时，父进程仍能读到已完成用例的结果，并准确知道是第几个用例挂住的——
既避免了“每个用例一个进程”的启动开销，又不损失失败归因精度。
"""

from __future__ import annotations

import io
import json
import os
import sys
import threading
import traceback
from contextlib import redirect_stderr, redirect_stdout

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

OUT_PATH: str | None = None


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


def _make_import():
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


def write_state(state: dict) -> None:
    """把当前进度写入结果文件（UTF-8，规避跨进程编码问题）。"""
    if not OUT_PATH:
        return
    with _STATE_LOCK:
        with open(OUT_PATH, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, default=repr)


# ---- 逐用例超时看门狗 ----
# 父进程给的是整个套件的总预算，若只靠它，排在后面的用例会"继承"前面
# 用例省下来的时间，导致单个用例的 timeout 形同虚设。这里在子进程内用
# 定时器线程自行掐断：超时即记录进度并 os._exit。
# 子进程是一次性的，直接退出是安全的；Windows 上也没有 signal.alarm 可用。
_STATE_LOCK = threading.Lock()
_COMPLETED: list[dict] = []
_CURRENT_INDEX: list[int | None] = [None]


def _watchdog(deadline: float) -> threading.Timer | None:
    if not deadline or deadline <= 0:
        return None

    def fire() -> None:
        write_state(
            {
                "completed": list(_COMPLETED),
                "in_progress": _CURRENT_INDEX[0],
                "watchdog_timeout": True,
            }
        )
        os._exit(2)

    timer = threading.Timer(deadline, fire)
    timer.daemon = True
    timer.start()
    return timer


def load_module(code_path: str, max_output: int, recursion_limit: int):
    """载入候选代码，返回 (namespace, error_payload)。"""
    import builtins as _b

    ns: dict = {"__name__": "__candidate__"}
    safe_builtins = dict(vars(_b))
    safe_builtins["__import__"] = _make_import()
    for name in ("open", "compile", "eval", "exec"):
        safe_builtins.pop(name, None)
    ns["__builtins__"] = safe_builtins

    sys.setrecursionlimit(recursion_limit)
    out, err = _LimitedWriter(max_output), _LimitedWriter(max_output)
    try:
        with open(code_path, encoding="utf-8") as fh:
            source = fh.read()
        with redirect_stdout(out), redirect_stderr(err):
            exec(compile(source, code_path, "exec"), ns)
    except BaseException as exc:  # noqa: BLE001
        return None, {
            "status": "import_error",
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
            "traceback": traceback.format_exc()[-1500:],
            "stdout": out.getvalue()[:500],
            "stderr": err.getvalue()[:500],
        }
    return ns, None


def run_one(fn, args: list, kwargs: dict, max_output: int) -> dict:
    out, err = _LimitedWriter(max_output), _LimitedWriter(max_output)
    try:
        with redirect_stdout(out), redirect_stderr(err):
            result = fn(*args, **kwargs)
        return {
            "status": "ok",
            "result": result,
            "stdout": out.getvalue()[:300],
            "stderr": err.getvalue()[:300],
        }
    except BaseException as exc:  # noqa: BLE001
        return {
            "status": "runtime_error",
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
            "traceback": traceback.format_exc()[-1500:],
            "stdout": out.getvalue()[:300],
            "stderr": err.getvalue()[:300],
        }


def main() -> None:
    global OUT_PATH
    OUT_PATH = sys.argv[2] if len(sys.argv) > 2 else None

    spec = json.load(open(sys.argv[1], encoding="utf-8"))
    tests = spec.get("tests", [])
    entry = spec["entry_point"]
    max_output = spec.get("max_output_chars", 20000)

    ns, err = load_module(
        spec["code_path"], max_output, spec.get("recursion_limit", 3000)
    )
    if err is not None:
        # 载入失败：所有用例统一记为同一错误
        completed = [dict(err, index=i) for i in range(len(tests))]
        write_state({"completed": completed, "in_progress": None, "load_error": True})
        return

    fn = ns.get(entry)
    if fn is None:
        completed = [
            {
                "index": i,
                "status": "missing_entry",
                "error": f"未找到入口函数 `{entry}`",
            }
            for i in range(len(tests))
        ]
        write_state({"completed": completed, "in_progress": None, "load_error": True})
        return

    _COMPLETED.clear()
    for i, case in enumerate(tests):
        _CURRENT_INDEX[0] = i
        write_state({"completed": list(_COMPLETED), "in_progress": i})

        timer = _watchdog(float(case.get("timeout", 0) or 0))
        try:
            payload = run_one(fn, case.get("args", []), case.get("kwargs", {}), max_output)
        finally:
            if timer is not None:
                timer.cancel()
        payload["index"] = i
        _COMPLETED.append(payload)
        _CURRENT_INDEX[0] = None
        write_state({"completed": list(_COMPLETED), "in_progress": None})

    write_state({"completed": list(_COMPLETED), "in_progress": None, "done": True})


if __name__ == "__main__":
    main()
