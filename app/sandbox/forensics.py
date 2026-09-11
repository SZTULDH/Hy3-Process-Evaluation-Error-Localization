"""源码级取证适配层：对接 submodules/sandbox（sandbox-debugger）。

当对抗测试失败时，用新沙盒的 run_to_error 拿到：
  - 异常栈 / 返回值
  - 崩溃前最后若干步执行轨迹（行号 + 局部变量快照）

上层（rules / pipeline）只依赖本模块，不直接感知 submodule 路径。
注意：本仓库也有 app.sandbox 包，不能简单 sys.path 注入，必须用 importlib 按路径加载。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

_SUBMODULE = Path(__file__).resolve().parents[2] / "submodules" / "sandbox"
_DEBUG_API = None
_LOAD_ERROR: str | None = None


def _load_debug_api():
    """按文件路径加载 submodule 的 debug_api，避开与本仓库 app.sandbox 的包名冲突。"""
    global _DEBUG_API, _LOAD_ERROR
    if _DEBUG_API is not None:
        return _DEBUG_API
    if _LOAD_ERROR is not None:
        return None

    api_path = _SUBMODULE / "app" / "sandbox" / "debug_api.py"
    if not api_path.exists():
        _LOAD_ERROR = f"sandbox-debugger not found at {_SUBMODULE}"
        return None

    try:
        root = str(_SUBMODULE)
        if root not in sys.path:
            sys.path.insert(0, root)

        pkg_name = "_sbx_dbg"
        sandbox_dir = _SUBMODULE / "app" / "sandbox"

        for name, path in (
            (pkg_name, _SUBMODULE / "app"),
            (f"{pkg_name}.sandbox", sandbox_dir),
        ):
            if name not in sys.modules:
                init = path / "__init__.py"
                spec = importlib.util.spec_from_file_location(
                    name,
                    str(init) if init.exists() else str(path),
                    submodule_search_locations=[str(path)],
                )
                if spec is None:
                    continue
                mod = importlib.util.module_from_spec(spec)
                sys.modules[name] = mod
                if spec.loader and init.exists():
                    try:
                        spec.loader.exec_module(mod)
                    except Exception:
                        pass

        full_name = f"{pkg_name}.sandbox.debug_api"
        spec = importlib.util.spec_from_file_location(
            full_name,
            str(api_path),
            submodule_search_locations=[str(sandbox_dir)],
        )
        if spec is None or spec.loader is None:
            _LOAD_ERROR = "failed to create import spec for debug_api"
            return None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full_name] = mod
        mod.__package__ = f"{pkg_name}.sandbox"
        spec.loader.exec_module(mod)
        _DEBUG_API = mod
        return mod
    except Exception as exc:  # noqa: BLE001
        _LOAD_ERROR = f"{type(exc).__name__}: {exc}"
        return None


def run_forensics(
    code: str,
    entry_point: str,
    args: list | None = None,
    *,
    budget: float = 5.0,
    trace_limit: int = 40,
    class_name: str | None = None,
    init_args: list | None = None,
    init_kwargs: dict | None = None,
    method: str | None = None,
    kwargs: dict | None = None,
) -> dict[str, Any]:
    """对单个失败用例做源码级取证。

    类级目标可传 ``class_name`` / ``method``（或把 entry_point 写成
    ``"Class.method"``），``init_args`` / ``init_kwargs`` 用于构造实例。

    返回可序列化 dict；失败时仍返回 {"ok": False, "error": ...}，不会抛异常。
    """
    api = _load_debug_api()
    if api is None:
        return {"ok": False, "error": _LOAD_ERROR or "debug_api unavailable"}
    try:
        return api.run_to_error(
            code, entry_point, args, budget=budget, trace_limit=trace_limit,
            class_name=class_name, init_args=init_args,
            init_kwargs=init_kwargs, method=method, kwargs=kwargs,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


# ------------------------------------------------------------------ 类级回放

REPLAY_ENTRY = "__forensic_replay__"


def _call_src(obj: str, case: dict, default_method: str) -> str:
    """把一条用例渲染成 ``obj.method(*args, **kwargs)`` 源码。"""
    method = case.get("method") or default_method
    parts = [repr(a) for a in (case.get("args") or [])]
    parts += [f"{k}={v!r}" for k, v in (case.get("kwargs") or {}).items()]
    return f"{obj}.{method}({', '.join(parts)})"


def _ctor_src(problem: dict, default_method: str) -> str:
    class_name = problem.get("class_name") or default_method
    parts = [repr(a) for a in (problem.get("init_args") or [])]
    parts += [f"{k}={v!r}" for k, v in (problem.get("init_kwargs") or {}).items()]
    return f"{class_name}({', '.join(parts)})"


def build_replay(
    code: str, problem: dict, cases: list[dict], index: int
) -> tuple[str, str, list]:
    """生成「回放到第 index 个用例」的包装入口。

    类级用例默认共享同一实例，单独跑失败用例会丢掉前置状态——断点看到的
    ``self`` 并非真实执行时的样子。这里把同一套件里从最近一次 ``reset: true``
    起的用例按序回放，最后一步作为入口返回，使取证与真实执行一致。

    返回 ``(源码, 入口名, 该入口的参数列表)``；函数级或下标越界时原样返回。
    """
    kind = (problem.get("kind") or "function").lower()
    default_method = problem.get("entry_point") or ""
    if kind != "class" or not cases or not 0 <= index < len(cases):
        case = cases[index] if 0 <= index < len(cases) else {}
        return code, default_method, list(case.get("args") or [])

    ctor = _ctor_src(problem, default_method)
    start = 0
    for i in range(index, -1, -1):
        if cases[i].get("reset"):
            start = i
            break

    lines = [f"def {REPLAY_ENTRY}():", f"    obj = {ctor}"]
    for i in range(start, index + 1):
        case = cases[i]
        if i > start and case.get("reset"):
            lines.append(f"    obj = {ctor}")
        call = _call_src("obj", case, default_method)
        if i < index:
            # 前置用例即便抛异常也继续回放：取证关注的是最后一步的状态
            lines += ["    try:", f"        {call}",
                      "    except BaseException:", "        pass"]
        else:
            lines.append(f"    return {call}")
    return code + "\n\n" + "\n".join(lines) + "\n", REPLAY_ENTRY, []


def find_case_index(cases: list[dict], args: list | None,
                    method: str | None = None) -> int | None:
    """按 args / method 反查用例下标，供 LLM 工具按入参取证时使用。"""
    for i, case in enumerate(cases or []):
        if list(case.get("args") or []) == list(args or []):
            if method is None or (case.get("method") or None) == method:
                return i
    return None


def run_forensics_case(
    code: str,
    problem: dict,
    cases: list[dict],
    index: int,
    *,
    budget: float = 5.0,
    trace_limit: int = 40,
) -> dict[str, Any]:
    """按题目语义对一个失败用例取证：类级会先回放前置用例状态。"""
    source, entry, entry_args = build_replay(code, problem, cases, index)
    return run_forensics(source, entry, entry_args,
                         budget=budget, trace_limit=trace_limit)


def forensic_summary(forensic: dict[str, Any], max_steps: int = 8) -> str:
    """把取证结果压成短文本，适合塞进 Finding.evidence。"""
    if not forensic.get("ok"):
        return f"[forensics unavailable] {forensic.get('error', '')}"

    parts: list[str] = []
    status = forensic.get("status", "?")
    parts.append(f"status={status}")
    if forensic.get("error"):
        parts.append(f"error={forensic['error'][:120]}")
    if forensic.get("result") is not None and status == "returned":
        parts.append(f"result={forensic['result']!r}")

    tail = forensic.get("trace_tail") or {}
    steps = tail.get("steps") or []
    if steps:
        last = steps[-max_steps:]
        lines = []
        for s in last:
            loc = s.get("locals") or {}
            short_loc = {
                k: (v if len(str(v)) < 40 else str(v)[:37] + "...")
                for k, v in list(loc.items())[:6]
            }
            lines.append(
                f"  L{s.get('line')} {s.get('func')} locals={short_loc}"
            )
        parts.append("trace_tail:\n" + "\n".join(lines))

    return " | ".join(parts) if len(parts) <= 2 else "\n".join(parts)
