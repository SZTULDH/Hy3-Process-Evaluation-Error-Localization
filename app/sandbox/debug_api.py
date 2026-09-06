"""面向 Agent 的标准调试接口。

这一层是**稳定契约**：上层（Agent、评估器、MCP Server、CLI）只依赖这里的
函数名与返回结构，底层协议/实现可以换。

三条使用路径：

1. 直接用 Python（本模块函数）
2. 用 CLI：python scripts/sandbox_debug.py
3. 用 MCP：python -m app.sandbox.mcp_server（tools/list + tools/call）

所有函数返回**可 JSON 序列化**的 dict，且永远不会抛异常——错误统一放在
`{"ok": false, "error": "..."}` 里，方便 LLM 直接消费。
"""

from __future__ import annotations

import threading
from typing import Any

from .debugger import DebugSession, DebugError

_LOCK = threading.Lock()
_SESSIONS: dict[str, DebugSession] = {}
_SEQ = 0


def _new_id() -> str:
    global _SEQ
    _SEQ += 1
    return f"s{_SEQ}"


def _wrap(fn):
    """把底层异常统一转成 {'ok': False, 'error': ...}。"""
    def inner(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except DebugError as exc:
            return {"ok": False, "error": str(exc)}
        except BaseException as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    inner.__name__ = fn.__name__
    inner.__doc__ = fn.__doc__
    return inner


def _get(session_id: str) -> DebugSession:
    with _LOCK:
        dbg = _SESSIONS.get(session_id)
    if dbg is None:
        raise DebugError(f"会话不存在或已关闭: {session_id}")
    return dbg


# ------------------------------------------------------------------ 生命周期


@_wrap
def start_session(
    code: str,
    entry_point: str,
    args: list | None = None,
    *,
    budget: float = 10.0,
    max_steps: int = 200_000,
    trace: bool = True,
    stop_on_entry: bool = True,
    breakpoints: list[dict] | None = None,
) -> dict:
    """启动调试会话。返回 {"ok": true, "session_id": ..., "entry_stop": {...}}。

    stop_on_entry=True 时会停在函数第一行，等效于 IDE 的"停在入口"。
    """
    dbg = DebugSession(code, entry_point, args, budget=budget, max_steps=max_steps,
                       trace=trace, stop_on_entry=stop_on_entry,
                       breakpoints=breakpoints)
    sid = _new_id()
    with _LOCK:
        _SESSIONS[sid] = dbg
    return {"ok": True, "session_id": sid, "entry_stop": dbg.pending,
            "note": "目标已停在入口" if stop_on_entry else "目标正在运行"}


@_wrap
def close_session(session_id: str) -> dict:
    """关闭会话并释放子进程。"""
    dbg = _get(session_id)
    dbg.close()
    with _LOCK:
        _SESSIONS.pop(session_id, None)
    return {"ok": True, "closed": session_id}


@_wrap
def list_sessions() -> dict:
    """列出当前存活的会话。"""
    with _LOCK:
        return {"ok": True, "sessions": list(_SESSIONS)}


# ------------------------------------------------------------------ 断点


@_wrap
def set_breakpoint(session_id: str, line: int, func: str | None = None,
                   condition: str | None = None,
                   hit_condition: str | None = None) -> dict:
    """下断点。

    condition    条件断点，在目标帧内求值的表达式，如 "i > 0 and j == 2"
    hit_condition 命中次数条件，形如 ">=3" / "==5" / "%2"
    """
    bp_id = _get(session_id).set_breakpoint(line, func, condition, hit_condition)
    return {"ok": True, "breakpoint_id": bp_id}


@_wrap
def remove_breakpoint(session_id: str, breakpoint_id: int) -> dict:
    """删除断点。"""
    return {"ok": True, "removed": _get(session_id).remove_breakpoint(breakpoint_id)}


@_wrap
def list_breakpoints(session_id: str) -> dict:
    """列出所有断点及其命中次数。"""
    return {"ok": True, "breakpoints": _get(session_id).breakpoints()}


# ------------------------------------------------------------------ 执行控制


@_wrap
def continue_(session_id: str) -> dict:
    """继续运行到下一个断点或结束。返回停靠快照 / 退出事件。"""
    return {"ok": True, "event": _get(session_id).continue_()}


@_wrap
def step_over(session_id: str) -> dict:
    """单步跳过（不进入函数内部）。"""
    return {"ok": True, "event": _get(session_id).step_over()}


@_wrap
def step_into(session_id: str) -> dict:
    """单步进入。"""
    return {"ok": True, "event": _get(session_id).step_into()}


@_wrap
def step_out(session_id: str) -> dict:
    """跳出当前函数。"""
    return {"ok": True, "event": _get(session_id).step_out()}


@_wrap
def pause(session_id: str) -> dict:
    """请求在下一条语句处暂停（用于打断正在运行的死循环）。"""
    return {"ok": True, "result": _get(session_id).pause()}


@_wrap
def restart(session_id: str, **overrides) -> dict:
    """重跑（保留断点之外的参数，可用 overrides 覆盖 code/args/breakpoints）。"""
    return {"ok": True, "result": _get(session_id).restart(**overrides)}


# ------------------------------------------------------------------ 观察


@_wrap
def get_stack(session_id: str, frame: int = 0, locals_: bool = True) -> dict:
    """获取调用栈。frame=0 为当前帧，1 为上一层调用者，依此类推。"""
    return {"ok": True, "result": _get(session_id).stack(frame, locals_)}


@_wrap
def get_locals(session_id: str, frame: int = 0) -> dict:
    """获取指定栈帧的局部变量（值为 repr 字符串）。"""
    return {"ok": True, "locals": _get(session_id).locals(frame)}


@_wrap
def evaluate(session_id: str, expr: str, frame: int = 0) -> dict:
    """在当前栈帧求值表达式；也支持 `x = 1` 形式的探查（在副本上执行）。"""
    return {"ok": True, "result": _get(session_id).eval(expr, frame)}


@_wrap
def get_trace(session_id: str, limit: int = 50) -> dict:
    """获取最近的执行轨迹（行号 + 当时的局部变量），无需断点即可事后回溯。"""
    return {"ok": True, "result": _get(session_id).trace(limit)}


@_wrap
def get_output(session_id: str) -> dict:
    """获取候选代码 print 出来的 stdout/stderr。"""
    return {"ok": True, "result": _get(session_id).output()}


@_wrap
def get_state(session_id: str) -> dict:
    """获取会话整体状态：是否结束、步数、耗时、返回值、错误。"""
    return {"ok": True, "result": _get(session_id).state()}


@_wrap
def where(session_id: str) -> dict:
    """一行式定位：当前停在第几行、该行源码是什么。"""
    return {"ok": True, "where": _get(session_id).where()}


# ------------------------------------------------------------------ 便捷组合


@_wrap
def run_to_error(code: str, entry_point: str, args: list | None = None,
                 budget: float = 10.0, trace_limit: int = 60) -> dict:
    """一步到位：跑一遍，失败则返回异常栈 + 最后若干步执行轨迹。

    这是评估流水线最常用的入口——不用下断点，直接拿到"崩在哪、崩之前发生了什么"。
    """
    with DebugSession(code, entry_point, args, budget=budget,
                      stop_on_entry=False) as dbg:
        ev = dbg.continue_()
        out = {"ok": True, "status": ev.get("status"), "result": ev.get("result"),
               "error": ev.get("error"), "traceback": ev.get("traceback"),
               "steps": ev.get("steps"), "used_seconds": ev.get("used_seconds")}
        if trace_limit:
            out["trace_tail"] = dbg.trace(trace_limit)
        if ev.get("status") != "returned":
            out["stdout"] = (dbg.output().get("stdout") or "")[-1000:]
        return out


# ------------------------------------------------------------------ 工具清单

# 供 function calling / MCP 直接使用的 JSON Schema。
# 约定：每个工具的 arguments 里如果涉及会话，统一用 session_id。
TOOLS: list[dict] = [
    {
        "name": "sandbox_start_session",
        "description": "启动一个 Python 调试会话，加载待调试代码并停在入口",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "待调试的 Python 源码"},
                "entry_point": {"type": "string", "description": "入口函数名"},
                "args": {"type": "array", "items": {}, "description": "入口函数的位置参数"},
                "budget": {"type": "number", "description": "纯运行时间预算（秒），默认 10"},
                "stop_on_entry": {"type": "boolean", "description": "是否停在函数第一行"},
                "breakpoints": {"type": "array", "items": {"type": "object"},
                                "description": "初始断点 [{line, condition, hit_condition}]"},
            },
            "required": ["code", "entry_point"],
        },
    },
    {"name": "sandbox_set_breakpoint", "description": "设置断点（支持条件与命中次数）",
     "inputSchema": {"type": "object", "properties": {
         "session_id": {"type": "string"},
         "line": {"type": "integer"},
         "func": {"type": "string"},
         "condition": {"type": "string"},
         "hit_condition": {"type": "string", "description": "如 >=3 / ==5 / %2"},
     }, "required": ["session_id", "line"]}},
    {"name": "sandbox_continue", "description": "继续运行到下一个断点或结束",
     "inputSchema": {"type": "object", "properties": {"session_id": {"type": "string"}},
                    "required": ["session_id"]}},
    {"name": "sandbox_step_over", "description": "单步跳过",
     "inputSchema": {"type": "object", "properties": {"session_id": {"type": "string"}},
                    "required": ["session_id"]}},
    {"name": "sandbox_step_into", "description": "单步进入",
     "inputSchema": {"type": "object", "properties": {"session_id": {"type": "string"}},
                    "required": ["session_id"]}},
    {"name": "sandbox_step_out", "description": "跳出当前函数",
     "inputSchema": {"type": "object", "properties": {"session_id": {"type": "string"}},
                    "required": ["session_id"]}},
    {"name": "sandbox_get_stack", "description": "获取调用栈（含各帧局部变量）",
     "inputSchema": {"type": "object", "properties": {
         "session_id": {"type": "string"}, "frame": {"type": "integer"},
         "locals_": {"type": "boolean"}}, "required": ["session_id"]}},
    {"name": "sandbox_get_locals", "description": "获取当前栈帧的局部变量",
     "inputSchema": {"type": "object", "properties": {
         "session_id": {"type": "string"}, "frame": {"type": "integer"}},
         "required": ["session_id"]}},
    {"name": "sandbox_evaluate", "description": "在当前栈帧求值表达式",
     "inputSchema": {"type": "object", "properties": {
         "session_id": {"type": "string"}, "expr": {"type": "string"},
         "frame": {"type": "integer"}}, "required": ["session_id", "expr"]}},
    {"name": "sandbox_get_trace", "description": "获取最近执行轨迹（无需断点的事后回溯）",
     "inputSchema": {"type": "object", "properties": {
         "session_id": {"type": "string"}, "limit": {"type": "integer"}},
         "required": ["session_id"]}},
    {"name": "sandbox_get_state", "description": "获取会话状态（是否结束/返回值/错误）",
     "inputSchema": {"type": "object", "properties": {"session_id": {"type": "string"}},
                    "required": ["session_id"]}},
    {"name": "sandbox_run_to_error",
     "description": "一步到位跑一遍代码，失败时返回异常栈与最后若干步执行轨迹",
     "inputSchema": {"type": "object", "properties": {
         "code": {"type": "string"}, "entry_point": {"type": "string"},
         "args": {"type": "array", "items": {}},
         "budget": {"type": "number"}, "trace_limit": {"type": "integer"}},
         "required": ["code", "entry_point"]}},
    {"name": "sandbox_close_session", "description": "关闭调试会话",
     "inputSchema": {"type": "object", "properties": {"session_id": {"type": "string"}},
                    "required": ["session_id"]}},
]

_HANDLERS = {
    "sandbox_start_session": start_session,
    "sandbox_set_breakpoint": set_breakpoint,
    "sandbox_continue": continue_,
    "sandbox_step_over": step_over,
    "sandbox_step_into": step_into,
    "sandbox_step_out": step_out,
    "sandbox_get_stack": get_stack,
    "sandbox_get_locals": get_locals,
    "sandbox_evaluate": evaluate,
    "sandbox_get_trace": get_trace,
    "sandbox_get_state": get_state,
    "sandbox_run_to_error": run_to_error,
    "sandbox_close_session": close_session,
}


def call_tool(name: str, arguments: dict | None = None) -> dict:
    """统一工具入口：按 TOOLS 里的名字调用，返回可 JSON 序列化的 dict。"""
    fn = _HANDLERS.get(name)
    if fn is None:
        return {"ok": False, "error": f"未知工具: {name}"}
    result = fn(**(arguments or {}))
    if not isinstance(result, dict):
        result = {"ok": True, "result": result}
    return result


def _shutdown_all() -> None:
    with _LOCK:
        items = list(_SESSIONS.items())
        _SESSIONS.clear()
    for _, dbg in items:
        try:
            dbg.close()
        except BaseException:  # noqa: BLE001
            pass
