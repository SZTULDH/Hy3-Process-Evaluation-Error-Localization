"""子进程内测试夹具：函数级 + 类级。

由 runner.py 通过 `python -I _harness.py <spec.json> <out.json>` 调用。

类级默认同一实例顺序执行；单测 reset=true 时重建实例。
"""

from __future__ import annotations

import io
import json
import os
import sys
import threading
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout

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
_STATE_LOCK = threading.Lock()
_COMPLETED: list[dict] = []
_CURRENT_INDEX: list[int | None] = [None]


class _LimitedWriter(io.StringIO):
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
    if not OUT_PATH:
        return
    with _STATE_LOCK:
        with open(OUT_PATH, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, default=repr)


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
    start = time.perf_counter()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            result = fn(*args, **kwargs)
        return {
            "status": "ok",
            "result": result,
            "stdout": out.getvalue()[:300],
            "stderr": err.getvalue()[:300],
            "duration_ms": round((time.perf_counter() - start) * 1000, 3),
        }
    except BaseException as exc:  # noqa: BLE001
        return {
            "status": "runtime_error",
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
            "traceback": traceback.format_exc()[-1500:],
            "stdout": out.getvalue()[:300],
            "stderr": err.getvalue()[:300],
            "duration_ms": round((time.perf_counter() - start) * 1000, 3),
        }


def _missing(msg: str, n: int) -> list[dict]:
    return [{"index": i, "status": "missing_entry", "error": msg} for i in range(n)]


def main() -> None:
    global OUT_PATH
    OUT_PATH = sys.argv[2] if len(sys.argv) > 2 else None

    spec = json.load(open(sys.argv[1], encoding="utf-8"))
    tests = spec.get("tests", [])
    kind = (spec.get("kind") or "function").lower()
    entry = spec.get("entry_point") or ""
    max_output = spec.get("max_output_chars", 20000)

    ns, err = load_module(
        spec["code_path"], max_output, spec.get("recursion_limit", 3000)
    )
    if err is not None:
        completed = [dict(err, index=i) for i in range(len(tests))]
        write_state({"completed": completed, "in_progress": None, "load_error": True})
        return

    if kind == "class":
        class_name = spec.get("class_name") or entry
        cls = ns.get(class_name)
        if cls is None:
            write_state(
                {
                    "completed": _missing(f"未找到类 `{class_name}`", len(tests)),
                    "in_progress": None,
                    "load_error": True,
                }
            )
            return
        init_args = list(spec.get("init_args") or [])
        init_kwargs = dict(spec.get("init_kwargs") or {})
        default_method = entry
        instance = None

        def ensure_instance():
            nonlocal instance
            if instance is None:
                instance = cls(*init_args, **init_kwargs)
            return instance

        _COMPLETED.clear()
        for i, case in enumerate(tests):
            _CURRENT_INDEX[0] = i
            write_state({"completed": list(_COMPLETED), "in_progress": i})
            timer = _watchdog(float(case.get("timeout", 0) or 0))
            try:
                if case.get("reset"):
                    instance = None
                try:
                    obj = ensure_instance()
                except BaseException as exc:  # noqa: BLE001
                    payload = {
                        "status": "runtime_error",
                        "error_type": type(exc).__name__,
                        "error": f"构造失败: {exc}"[:500],
                        "traceback": traceback.format_exc()[-1500:],
                        "duration_ms": 0.0,
                    }
                else:
                    method_name = case.get("method") or default_method
                    if not method_name:
                        payload = {
                            "status": "missing_entry",
                            "error": "类级用例未指定 method，且无默认 entry_point",
                        }
                    else:
                        fn = getattr(obj, method_name, None)
                        if fn is None or not callable(fn):
                            payload = {
                                "status": "missing_entry",
                                "error": f"实例上未找到方法 `{method_name}`",
                            }
                        else:
                            payload = run_one(
                                fn,
                                case.get("args", []),
                                case.get("kwargs", {}),
                                max_output,
                            )
                            payload["method"] = method_name
            finally:
                if timer is not None:
                    timer.cancel()
            payload["index"] = i
            _COMPLETED.append(payload)
            _CURRENT_INDEX[0] = None
            write_state({"completed": list(_COMPLETED), "in_progress": None})

        write_state({"completed": list(_COMPLETED), "in_progress": None, "done": True})
        return

    fn = ns.get(entry)
    if fn is None:
        write_state(
            {
                "completed": _missing(f"未找到入口函数 `{entry}`", len(tests)),
                "in_progress": None,
                "load_error": True,
            }
        )
        return

    _COMPLETED.clear()
    for i, case in enumerate(tests):
        _CURRENT_INDEX[0] = i
        write_state({"completed": list(_COMPLETED), "in_progress": i})
        timer = _watchdog(float(case.get("timeout", 0) or 0))
        try:
            payload = run_one(
                fn, case.get("args", []), case.get("kwargs", {}), max_output
            )
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
