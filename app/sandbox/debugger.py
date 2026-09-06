"""交互式调试会话客户端。

把 _dbgharness.py 子进程包装成一个对象，供 Agent / 评估器直接调用：

    with DebugSession(code, "two_sum", [[2,7,11,15], 9]) as dbg:
        dbg.set_breakpoint(line=4, condition="i > 0")
        dbg.continue_()                 # 停在断点
        print(dbg.locals())             # 查看当前变量
        print(dbg.eval("nums[i] + nums[j]"))
        dbg.step_over()

也可一次性跑完并收集所有停靠点（更适合自动化流水线）：

    report = debug_once(code, "two_sum", [[2,7,11,15], 9],
                        breakpoints=[{"line": 4}])
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

DBG_HARNESS_PATH = Path(__file__).with_name("_dbgharness.py")

# 设 HY3_DEBUG_PROTOCOL=1 可把每条协议报文打到 stderr，用于排查调试器本身
_TRACE = bool(os.getenv("HY3_DEBUG_PROTOCOL"))

# 等待子进程响应的默认超时（秒）。注意这是墙钟时间，
# 目标运行时间由 budget 单独控制，两者互不影响。
DEFAULT_IO_TIMEOUT = 60.0


class DebugError(RuntimeError):
    """调试会话异常：子进程崩溃、协议错误或命令被拒。"""


@dataclass
class StopEvent:
    """一次停靠的完整快照。"""

    reason: str
    steps: int
    depth: int
    stack: list[dict] = field(default_factory=list)
    current: dict = field(default_factory=dict)

    @property
    def line(self) -> int:
        return int(self.current.get("line", -1))

    @property
    def source(self) -> str:
        return str(self.current.get("source", ""))

    def __str__(self) -> str:
        return f"停在 line {self.line} ({self.reason})  {self.source.strip()}"


class DebugSession:
    """一个长驻的调试子进程会话。用完必须 close()（或用 with）。"""

    def __init__(
        self,
        code: str | None = None,
        entry_point: str | None = None,
        args: list | None = None,
        kwargs: dict | None = None,
        *,
        budget: float = 10.0,
        max_steps: int = 200_000,
        trace: bool = True,
        trace_limit: int = 2000,
        stop_on_entry: bool = True,
        breakpoints: list[dict] | None = None,
        io_timeout: float = DEFAULT_IO_TIMEOUT,
    ) -> None:
        self.io_timeout = io_timeout
        self._seq = 0
        self.budget = budget
        self.entry_stop: dict | None = None
        self._pending: dict | None = None
        self._events: "queue.Queue[dict]" = queue.Queue()
        self._responses: "queue.Queue[dict]" = queue.Queue()
        self.dead = False

        self.proc = subprocess.Popen(
            [sys.executable, "-I", str(DBG_HARNESS_PATH)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            # errors="replace" 是防御底线：单个杂字节绝不能把读线程打死，
            # 否则后续所有响应都会丢失（表现为莫名其妙的"响应超时"）
            errors="replace",
            bufsize=1,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._stderr_lines: list[str] = []
        threading.Thread(target=self._read_stderr, daemon=True).start()

        if code is not None:
            self.load(code, entry_point, args, kwargs, budget=budget,
                      max_steps=max_steps, trace=trace, trace_limit=trace_limit,
                      stop_on_entry=stop_on_entry, breakpoints=breakpoints)

    # -------------------------------------------------------------- IO

    def _read_loop(self) -> None:
        """持续读取子进程输出。任何单条报文的解析异常都不能中断本线程。"""
        while True:
            try:
                for line in self.proc.stdout:  # type: ignore[union-attr]
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if _TRACE:
                        if "event" in msg:
                            print(f"[dbg <-] event {msg.get('event')}", file=sys.stderr)
                        else:
                            print(f"[dbg <-] resp id={msg.get('id')} ok={msg.get('ok')}",
                                  file=sys.stderr)
                    if "event" in msg:
                        self._events.put(msg)
                    else:
                        self._responses.put(msg)
            except BaseException:  # noqa: BLE001
                pass
            if self.proc.poll() is not None:
                break
        self.dead = True

    def _send(self, cmd: str, args: dict | None = None) -> int:
        if self.dead or self.proc.poll() is not None:
            raise DebugError("调试子进程已退出")
        self._seq += 1
        payload = json.dumps({"id": self._seq, "cmd": cmd, "args": args or {}},
                             ensure_ascii=False)
        if _TRACE:
            print(f"[dbg ->] id={self._seq} {cmd}", file=sys.stderr)
        assert self.proc.stdin is not None
        self.proc.stdin.write(payload + "\n")
        self.proc.stdin.flush()
        return self._seq

    def _read_stderr(self) -> None:
        try:
            for line in self.proc.stderr:  # type: ignore[union-attr]
                self._stderr_lines.append(line.rstrip("\n"))
        except BaseException:  # noqa: BLE001
            pass

    def stderr_tail(self, k: int = 10) -> str:
        return "\n".join(self._stderr_lines[-k:])

    def _response(self, mid: int, timeout: float | None = None) -> dict:
        deadline = None
        if timeout is not None:
            deadline = timeout
        try:
            msg = self._responses.get(timeout=deadline or self.io_timeout)
        except queue.Empty as exc:
            tail = self.stderr_tail()
            hint = f"\n子进程 stderr:\n{tail}" if tail else ""
            raise DebugError(f"等待命令 {mid} 的响应超时{hint}") from exc
        # 理论上 id 严格递增，这里做个兜底对齐
        while msg.get("id") != mid:
            try:
                msg = self._responses.get(timeout=self.io_timeout)
            except queue.Empty as exc:
                raise DebugError(f"未收到命令 {mid} 的响应") from exc
        if not msg.get("ok"):
            raise DebugError(msg.get("error", "命令执行失败"))
        return msg.get("result") or {}

    def _next_event(self, timeout: float | None = None) -> dict:
        try:
            return self._events.get(timeout=timeout or self.io_timeout)
        except queue.Empty as exc:
            raise DebugError("等待 stopped/exited 事件超时") from exc

    def _cmd(self, cmd: str, args: dict | None = None, timeout: float | None = None) -> dict:
        mid = self._send(cmd, args)
        return self._response(mid, timeout)

    def _drain_events(self) -> list[dict]:
        out = []
        while True:
            try:
                out.append(self._events.get_nowait())
            except queue.Empty:
                return out

    # -------------------------------------------------------------- 生命周期

    def load(
        self,
        code: str,
        entry_point: str,
        args: list | None = None,
        kwargs: dict | None = None,
        *,
        budget: float = 10.0,
        max_steps: int = 200_000,
        trace: bool = True,
        trace_limit: int = 2000,
        stop_on_entry: bool = True,
        breakpoints: list[dict] | None = None,
    ) -> dict:
        self.budget = budget
        res = self._cmd("load", {
            "code": code,
            "entry_point": entry_point,
            "args": args or [],
            "kwargs": kwargs or {},
            "budget": budget,
            "max_steps": max_steps,
            "trace": trace,
            "trace_limit": trace_limit,
            "stop_on_entry": stop_on_entry,
            "breakpoints": breakpoints or [],
        })
        if not res.get("loaded"):
            raise DebugError(f"载入失败: {res.get('error')}")
        # 载入后立刻等第一个事件（入口停靠 / 首个断点 / 直接退出）并暂存。
        # 不这样做的话，跑得快的程序会在我们发 continue 之前就退出，
        # 事件被白白丢掉，客户端只能干等一个永远不会来的事件。
        self._pending = self._wait_first_event(budget)
        self.entry_stop = self._pending  # 兼容旧字段名，语义即"载入后的首个停靠"
        return res

    @property
    def pending(self) -> dict | None:
        """尚未消费的首个事件（只读，不会像 first_event() 那样取走）。"""
        return self._pending

    def _wait_first_event(self, budget: float) -> dict | None:
        try:
            return self._next_event(timeout=budget + 15.0)
        except DebugError:
            return None

    def first_event(self) -> dict | None:
        """取出 load 之后暂存的首个事件（入口停靠或首个断点）。"""
        ev, self._pending = self._pending, None
        return ev

    def restart(self, **overrides) -> dict:
        self._drain_events()
        return self._cmd("restart", overrides)

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                self._send("quit")
                self.proc.wait(timeout=2)
            except BaseException:  # noqa: BLE001
                pass
        if self.proc.poll() is None:
            self.proc.kill()
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except BaseException:  # noqa: BLE001
            pass

    def __enter__(self) -> "DebugSession":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -------------------------------------------------------------- 断点

    def set_breakpoint(
        self,
        line: int,
        func: str | None = None,
        condition: str | None = None,
        hit_condition: str | None = None,
    ) -> int:
        """下断点。condition 为在目标帧内求值的表达式；hit_condition 形如 ">=3"。"""
        res = self._cmd("set_breakpoint", {
            "line": line, "func": func,
            "condition": condition, "hit_condition": hit_condition,
        })
        return int(res["id"])

    def remove_breakpoint(self, bp_id: int) -> int:
        return int(self._cmd("remove_breakpoint", {"id": bp_id}).get("removed", 0))

    def breakpoints(self) -> list[dict]:
        return list(self._cmd("list_breakpoints"))

    # -------------------------------------------------------------- 执行控制

    def _resume(self, cmd: str, timeout: float | None = None) -> dict:
        """发出继续/单步命令，并返回随之而来的 stopped / exited 事件。

        若目标已经退出（load 时暂存到了 exited 事件），直接把它返回，
        不再发命令 —— 否则会去等一个永远不会来的事件。
        """
        pending, self._pending = self._pending, None
        if pending is not None and pending.get("event") == "exited":
            return pending
        mid = self._send(cmd)
        self._response(mid, timeout)
        grace = getattr(self, "budget", 10.0) + 15.0
        return self._next_event(timeout or grace)

    def continue_(self, timeout: float | None = None) -> dict:
        return self._resume("continue", timeout)

    def step_over(self, timeout: float | None = None) -> dict:
        return self._resume("step_over", timeout)

    def step_into(self, timeout: float | None = None) -> dict:
        return self._resume("step_into", timeout)

    def step_out(self, timeout: float | None = None) -> dict:
        return self._resume("step_out", timeout)

    def pause(self) -> dict:
        return self._cmd("pause")

    # -------------------------------------------------------------- 观察

    def stack(self, frame: int = 0, locals_: bool = True) -> dict:
        return self._cmd("get_stack", {"frame": frame, "locals": locals_})

    def locals(self, frame: int = 0) -> dict:  # noqa: A003
        return self._cmd("get_locals", {"frame": frame})

    def eval(self, expr: str, frame: int = 0) -> dict:  # noqa: A003
        return self._cmd("eval", {"expr": expr, "frame": frame})

    def trace(self, limit: int = 50) -> dict:
        """最近的执行轨迹（无需断点的事后回溯）。"""
        return self._cmd("get_trace", {"limit": limit})

    def output(self) -> dict:
        return self._cmd("get_output")

    def state(self) -> dict:
        return self._cmd("state")

    # -------------------------------------------------------------- 便捷

    def frames(self) -> list[dict]:
        return list(self.stack().get("stack") or [])

    def where(self) -> str:
        """一行式定位信息，适合直接放进日志或 LLM 提示词。"""
        snap = self.stack()
        if not snap.get("stopped"):
            return "<未处于暂停状态>"
        cur = snap["current"]
        return f"line {cur['line']} in {cur['func']} | {cur['source'].strip()}"


def as_stop(ev: dict) -> StopEvent | None:
    if ev.get("event") != "stopped":
        return None
    return StopEvent(
        reason=ev.get("reason", "?"),
        steps=int(ev.get("steps", 0)),
        depth=int(ev.get("depth", 0)),
        stack=list(ev.get("stack") or []),
        current=dict(ev.get("current") or {}),
    )


def debug_once(
    code: str,
    entry_point: str,
    args: list | None = None,
    *,
    breakpoints: list[dict] | None = None,
    budget: float = 10.0,
    max_steps: int = 200_000,
    max_stops: int = 50,
    collect_trace: int = 0,
) -> dict:
    """一次性跑完：下断点 → 一路 continue → 收集所有停靠点与最终结果。

    适合自动化流水线（不给 Agent 交互机会，直接产出结构化报告）。
    """
    stops: list[dict] = []
    with DebugSession(code, entry_point, args, budget=budget, max_steps=max_steps,
                      stop_on_entry=False, breakpoints=breakpoints) as dbg:
        # load 已经等到了第一个事件：有断点时是首个停靠点，无断点时是退出事件
        ev = dbg.first_event() or {}
        while ev.get("event") == "stopped" and len(stops) < max_stops:
            stops.append(ev)
            ev = dbg.continue_()
        report = {
            "stops": stops,
            "stop_count": len(stops),
            "exit": ev if ev.get("event") == "exited" else None,
        }
        if collect_trace:
            report["trace"] = dbg.trace(collect_trace)
    return report
