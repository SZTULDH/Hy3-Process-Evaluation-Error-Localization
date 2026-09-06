"""常驻调试子进程：给 Agent 提供断点、单步、栈帧/变量查看、表达式求值。

用法（由 debugger.py 拉起，一般不需要手工调用）：

    python -I _dbgharness.py

协议：stdin / stdout 各一行一个 JSON（JSON Lines）。

* 请求   {"id": 1, "cmd": "continue", "args": {...}}
* 响应   {"id": 1, "ok": true, "result": {...}}  或 {"id":1,"ok":false,"error":"..."}
* 事件   {"event": "stopped"|"exited"|"loaded", ...}

设计要点：

1. 目标函数在**独立线程**中运行并安装 sys.settrace；命中断点时该线程阻塞在
   Event 上，主线程负责读写协议 —— 这样"停下来等命令"不会卡死调试器自己。
2. 协议输出走启动时保存的 sys.stdout 原始对象。候选代码的 print 被重定向到
   内存 buffer，绝不会污染协议流（否则 Agent 会解析到脏 JSON）。
3. 时间预算只统计**真正在跑**的时间，命中断点后 Agent 思考多久都不计入，
   交互调试不会被超时打断。
4. 只跟踪候选代码文件（target_file），标准库/第三方内部不展开，
   避免单步时一头扎进库函数。
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path

# Python 3.11+ 的 `-I` 隐含 `-P`，脚本所在目录不会被加入 sys.path，
# 因此这里显式补上，保证 `import tracer` 可用（不影响隔离模式的其他效果）。
sys.path.insert(0, str(Path(__file__).resolve().parent))

import tracer as T  # noqa: E402

# 协议流：在任何重定向之前抓住真实的 stdout
PROTOCOL_OUT = sys.stdout
# 中文 Windows 上 stdout 默认走 locale 编码（GBK），客户端按 UTF-8 解码会直接
# 抛 UnicodeDecodeError 并打死读线程。这里强制 UTF-8；注意 `python -I` 会忽略
# PYTHONIOENCODING，所以只能显式 reconfigure（与 runner.py 写文件同理的坑）。
try:
    PROTOCOL_OUT.reconfigure(encoding="utf-8", newline="\n")  # type: ignore[union-attr]
except BaseException:  # noqa: BLE001
    pass
_POUT_LOCK = threading.Lock()

# 单步/继续时，等待目标停下来的最长时间（秒）
STOP_WAIT_TIMEOUT = 5.0
# 整个调试进程的硬性存活上限（秒），兜底防挂死
PROCESS_WATCHDOG = 600.0


def _emit(obj: dict) -> None:
    with _POUT_LOCK:
        PROTOCOL_OUT.write(json.dumps(obj, ensure_ascii=False, default=T.safe_repr) + "\n")
        PROTOCOL_OUT.flush()


# 设 HY3_DBG_LOG=<path> 可把调试器自身的执行轨迹写到文件，用于排查协议问题
_LOG_PATH = os.getenv("HY3_DBG_LOG")


def _log(msg: str) -> None:
    if not _LOG_PATH:
        return
    try:
        with open(_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(f"[{time.monotonic():.3f}] {msg}\n")
    except BaseException:  # noqa: BLE001
        pass


class Abort(Exception):
    """内部信号：终止本次运行（超步数 / 超时间预算 / 被 kill）。"""


# ------------------------------------------------------------------ 断点


@dataclass
class Breakpoint:
    id: int
    line: int
    func: str | None = None
    condition: str | None = None  # 条件断点：在目标帧内求值的表达式
    hit_condition: str | None = None  # 命中次数条件，如 ">=3" "==5" "%2"
    hit_count: int = 0
    enabled: bool = True

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "line": self.line,
            "func": self.func,
            "condition": self.condition,
            "hit_condition": self.hit_condition,
            "hit_count": self.hit_count,
            "enabled": self.enabled,
        }
        return d


_HIT_RE = re.compile(r"^\s*(>=|==|%\s*)?\s*(\d+)\s*$")


def _hit_ok(hit_condition: str | None, hit_count: int) -> bool:
    if not hit_condition:
        return True
    m = _HIT_RE.match(hit_condition)
    if not m:
        return True
    op, num = (m.group(1) or ">=").replace(" ", ""), int(m.group(2))
    if op == ">=":
        return hit_count >= num
    if op == "==":
        return hit_count == num
    if op == "%":
        return num > 0 and hit_count % num == 0
    return True


# ------------------------------------------------------------------ 会话


class DebugSession:
    def __init__(self) -> None:
        self.q: "queue.Queue[dict]" = __import__("queue").Queue()

        self.workdir: str | None = None
        self.target_file: str | None = None
        self.ns: dict | None = None
        self.entry: str | None = None
        self.args: list = []
        self.kwargs: dict = {}
        self.out_writer: T._LimitedWriter | None = None
        self.err_writer: T._LimitedWriter | None = None

        self.breakpoints: list[Breakpoint] = []
        self._bp_seq = 0

        self.step_mode: str | None = None  # into | over | out
        self.step_depth = 0
        self.pause_flag = threading.Event()
        self.resume_event = threading.Event()
        self.stopped_event = threading.Event()
        self.paused_frame = None
        self.paused_depth = 0
        self.last_stop_reason = "step"

        self.worker: threading.Thread | None = None
        self.finished = True
        self.status = "idle"  # idle | returned | error | aborted
        self.result: object = None
        self.error: str = ""
        self.error_tb: str = ""

        self.depth = 0
        self.step_count = 0
        self.max_steps = 200_000
        self.budget = 10.0  # 纯运行时间预算（秒）
        self.used = 0.0
        self._run_start: float | None = None

        self.recorder = T.TraceRecorder()
        self.trace_enabled = True

    # -------------------------------------------------------------- 计时

    def _mark_run_start(self) -> None:
        self._run_start = time.monotonic()

    def _mark_run_stop(self) -> None:
        if self._run_start is not None:
            self.used += time.monotonic() - self._run_start
            self._run_start = None

    def _over_budget(self) -> bool:
        if self._run_start is None:
            return False
        return (self.used + time.monotonic() - self._run_start) > self.budget

    # -------------------------------------------------------------- 加载

    def load(self, args: dict) -> dict:
        code = args.get("code") or ""
        entry = args.get("entry_point")
        if not code or not entry:
            raise ValueError("load 需要 code 与 entry_point")

        self.workdir = tempfile.mkdtemp(prefix="hy3dbg_")
        self.target_file = os.path.join(self.workdir, "candidate.py")
        with open(self.target_file, "w", encoding="utf-8") as fh:
            fh.write(code)

        self.entry = entry
        self.args = args.get("args") or []
        self.kwargs = args.get("kwargs") or {}
        self.max_steps = int(args.get("max_steps", 200_000))
        self.budget = float(args.get("budget", 10.0))
        self.trace_enabled = bool(args.get("trace", True))

        self.breakpoints = []
        self._bp_seq = 0
        for bp in args.get("breakpoints") or []:
            self.add_breakpoint(bp)

        self.recorder = T.TraceRecorder(limit=int(args.get("trace_limit", 2000)))

        ns, out, err = T.build_sandbox_namespace(recursion_limit=int(args.get("recursion_limit", 3000)))
        self.ns, self.out_writer, self.err_writer = ns, out, err

        try:
            with redirect_stdout(out), redirect_stderr(err):
                exec(compile(code, self.target_file, "exec"), ns)
        except BaseException as exc:  # noqa: BLE001
            return {
                "loaded": False,
                "status": "import_error",
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
                "traceback": traceback.format_exc()[-1500:],
            }

        if entry not in ns:
            return {"loaded": False, "status": "missing_entry",
                    "error": f"未找到入口函数 `{entry}`"}

        self.finished = False
        self.status = "idle"
        self.result = None
        self.error = ""
        self.error_tb = ""
        self.step_count = 0
        self.depth = 0
        self.used = 0.0
        self.stopped_event.clear()
        self.resume_event.clear()
        self.pause_flag.clear()

        # stop_on_entry：拉起后先停在函数第一行，等价于 IDE 的"停在入口"
        self.step_mode = "into" if args.get("stop_on_entry", True) else None
        self.step_depth = 0

        self.worker = threading.Thread(target=self._run, daemon=True)
        self._mark_run_start()
        self.worker.start()

        return {
            "loaded": True,
            "entry": entry,
            "file": self.target_file,
            "breakpoints": [b.to_dict() for b in self.breakpoints],
        }

    def _run(self) -> None:
        sys.settrace(self._dispatch)
        try:
            with redirect_stdout(self.out_writer), redirect_stderr(self.err_writer):
                fn = self.ns[self.entry]
                self.result = fn(*self.args, **self.kwargs)
            self.status = "returned"
        except Abort as exc:
            self.status = "aborted"
            self.error = str(exc)
        except BaseException as exc:  # noqa: BLE001
            self.status = "error"
            self.error = f"{type(exc).__name__}: {exc}"
            self.error_tb = traceback.format_exc()[-2000:]
        finally:
            sys.settrace(None)
            self._mark_run_stop()
            self.finished = True
            self.paused_frame = None
            self.step_mode = None
            self.stopped_event.set()
            _emit({
                "event": "exited",
                "status": self.status,
                "result": T.safe_repr(self.result),
                "error": self.error,
                "traceback": self.error_tb[-1500:] if self.error_tb else "",
                "steps": self.step_count,
                "used_seconds": round(self.used, 3),
                "stdout": (self.out_writer.getvalue() if self.out_writer else "")[-2000:],
            })

    # -------------------------------------------------------------- 跟踪

    def _dispatch(self, frame, event, arg):
        if not self.target_file or frame.f_code.co_filename != self.target_file:
            return None
        try:
            if event == "call":
                self.depth += 1
                return self._dispatch
            if event == "line":
                return self._on_line(frame)
            if event == "return":
                self.depth -= 1
                return self._dispatch
            if event == "exception":
                return self._dispatch
        except Abort:
            raise
        except BaseException:  # noqa: BLE001
            return self._dispatch
        return self._dispatch

    def _on_line(self, frame):
        self.step_count += 1
        if self.step_count > self.max_steps:
            raise Abort(f"执行步数超过 max_steps={self.max_steps}")
        if self._over_budget():
            raise Abort(f"运行时间超过 budget={self.budget}s")
        if self.trace_enabled:
            self.recorder.record(self.step_count, frame, self.depth)
        if self._should_stop(frame):
            self._pause(frame)
        return self._dispatch

    def _should_stop(self, frame) -> bool:
        if self.step_mode == "into":
            self.last_stop_reason = "step"
            return True
        if self.step_mode == "over" and self.depth <= self.step_depth:
            self.last_stop_reason = "step"
            return True
        if self.step_mode == "out" and self.depth < self.step_depth:
            self.last_stop_reason = "step"
            return True
        if self.pause_flag.is_set():
            self.last_stop_reason = "pause"
            return True
        for bp in self.breakpoints:
            if not bp.enabled or bp.line != frame.f_lineno:
                continue
            if bp.func and bp.func != frame.f_code.co_name:
                continue
            bp.hit_count += 1
            if not _hit_ok(bp.hit_condition, bp.hit_count):
                continue
            if bp.condition and not self._eval_condition(bp.condition, frame):
                continue
            self.last_stop_reason = f"breakpoint#{bp.id}"
            return True
        return False

    def _eval_condition(self, expr: str, frame) -> bool:
        try:
            return bool(eval(expr, frame.f_globals, dict(frame.f_locals)))  # noqa: S307
        except BaseException:  # noqa: BLE001
            return False

    def _pause(self, frame) -> None:
        self._mark_run_stop()
        self.step_mode = None
        self.pause_flag.clear()
        self.paused_frame = frame
        self.paused_depth = self.depth
        self.stopped_event.set()
        # 事件直接由工作线程发出：若走队列，批量灌入命令时会排到命令之后，
        # 极端情况下进程先退出就丢事件了。
        _emit({"event": "stopped", **self.snapshot()})
        # 阻塞在这里等 Agent 的下一条命令；主线程负责读写协议
        self.resume_event.wait()
        self.resume_event.clear()
        self._mark_run_start()

    # -------------------------------------------------------------- 断点管理

    def add_breakpoint(self, spec: dict) -> Breakpoint:
        self._bp_seq += 1
        bp = Breakpoint(
            id=self._bp_seq,
            line=int(spec["line"]),
            func=spec.get("func"),
            condition=spec.get("condition"),
            hit_condition=spec.get("hit_condition"),
        )
        self.breakpoints.append(bp)
        return bp

    # -------------------------------------------------------------- 快照

    def _require_frame(self, index: int = 0):
        if self.finished or self.paused_frame is None:
            return None
        f = self.paused_frame
        for _ in range(int(index)):
            if f.f_back is None:
                break
            f = f.f_back
        return f

    def snapshot(self, index: int = 0, include_locals: bool = True) -> dict:
        f = self._require_frame(index)
        if f is None:
            return {"stopped": False, "finished": self.finished}
        stack = T.stack_snapshot(f, include_locals=include_locals)
        return {
            "stopped": True,
            "reason": self.last_stop_reason,
            "steps": self.step_count,
            "depth": self.paused_depth,
            "stack": stack,
            "current": {
                "line": f.f_lineno,
                "func": f.f_code.co_name,
                "source": T.source_line(f.f_code.co_filename, f.f_lineno),
                "locals": T.safe_vars(f.f_locals),
            },
        }

    # -------------------------------------------------------------- 命令

    def _wait_stopped(self, timeout: float = STOP_WAIT_TIMEOUT) -> bool:
        if self.finished:
            return False
        return self.stopped_event.wait(timeout)

    def resume(self, mode: str | None) -> dict:
        _log(f"resume enter mode={mode} finished={self.finished} "
             f"stopped={self.stopped_event.is_set()}")
        if self.finished:
            return {"resumed": False, "error": "目标已结束，需先 restart"}
        if not self.stopped_event.is_set():
            if not self._wait_stopped():
                return {"resumed": False, "error": "目标仍在运行，请先 pause"}
        self.step_mode = mode
        self.step_depth = self.paused_depth if mode in ("over", "out") else 0
        self.stopped_event.clear()
        self.resume_event.set()
        return {"resumed": True, "mode": mode or "continue"}

    def evaluate(self, expr: str, index: int = 0) -> dict:
        f = self._require_frame(index)
        if f is None:
            return {"ok": False, "error": "当前没有暂停中的栈帧"}
        globs, locs = f.f_globals, dict(f.f_locals)
        try:
            value = eval(expr, globs, locs)  # noqa: S307
            return {"ok": True, "expr": expr, "value": T.safe_repr(value, 1000)}
        except SyntaxError:
            # 支持 `x = 1` 这类语句做 what-if 探查（不写回真实栈帧）
            try:
                sandbox = dict(locs)
                exec(compile(expr, "<probe>", "exec"), globs, sandbox)  # noqa: S102
                changed = {k: T.safe_repr(v, 500) for k, v in sandbox.items()
                           if T.safe_repr(locs.get(k), 500) != T.safe_repr(v, 500)}
                return {"ok": True, "expr": expr, "exec": True, "changed": changed,
                        "note": "语句在副本上执行，未影响真实栈帧"}
            except BaseException as exc:  # noqa: BLE001
                return {"ok": False, "expr": expr,
                        "error": f"{type(exc).__name__}: {exc}"}
        except BaseException as exc:  # noqa: BLE001
            return {"ok": False, "expr": expr, "error": f"{type(exc).__name__}: {exc}"}

    def restart(self, args: dict) -> dict:
        if self.worker is not None and not self.finished:
            self.pause_flag.set()
            self.resume_event.set()
            self.worker.join(timeout=2.0)
        merged = {
            "code": open(self.target_file, encoding="utf-8").read() if self.target_file else "",
            "entry_point": self.entry,
            "args": self.args,
            "kwargs": self.kwargs,
            "budget": self.budget,
            "max_steps": self.max_steps,
            "trace": self.trace_enabled,
            "stop_on_entry": True,
            **args,
        }
        return self.load(merged)

    # -------------------------------------------------------------- 主循环

    def handle(self, msg: dict) -> None:
        mid = msg.get("id")
        cmd = msg.get("cmd")
        args = msg.get("args") or {}
        _log(f"handle enter id={mid} cmd={cmd}")
        try:
            result = self._dispatch_cmd(cmd, args)
            _emit({"id": mid, "ok": True, "result": result})
        except BaseException as exc:  # noqa: BLE001
            _emit({"id": mid, "ok": False,
                   "error": f"{type(exc).__name__}: {exc}",
                   "traceback": traceback.format_exc()[-800:]})
        _log(f"handle done  id={mid} cmd={cmd}")

    def _dispatch_cmd(self, cmd: str, args: dict):
        if cmd == "load":
            r = self.load(args)
            if not r.get("loaded"):
                self.finished = True
                self.status = r.get("status", "import_error")
                self.error = r.get("error", "")
                self.error_tb = r.get("traceback", "")
            return r

        if cmd == "restart":
            return self.restart(args)

        if cmd == "set_breakpoint":
            bp = self.add_breakpoint(args)
            return bp.to_dict()

        if cmd == "remove_breakpoint":
            before = len(self.breakpoints)
            self.breakpoints = [b for b in self.breakpoints if b.id != int(args.get("id", -1))]
            return {"removed": before - len(self.breakpoints)}

        if cmd == "list_breakpoints":
            return [b.to_dict() for b in self.breakpoints]

        if cmd == "continue":
            return self.resume(None)

        if cmd in ("step_over", "step_into", "step_out"):
            return self.resume({"step_over": "over", "step_into": "into",
                                "step_out": "out"}[cmd])

        if cmd == "pause":
            if self.finished:
                return {"paused": False, "error": "目标已结束"}
            self.pause_flag.set()
            return {"paused": True, "note": "将在下一条语句处停下"}

        if cmd == "get_stack":
            return self.snapshot(int(args.get("frame", 0)),
                                 include_locals=bool(args.get("locals", True)))

        if cmd == "get_locals":
            f = self._require_frame(int(args.get("frame", 0)))
            if f is None:
                return {}
            return T.safe_vars(f.f_locals, limit=int(args.get("limit", T.MAX_REPR)))

        if cmd == "get_trace":
            return {"total_steps": self.recorder.total,
                    "truncated": self.recorder.truncated,
                    "steps": self.recorder.tail(int(args.get("limit", 50)))}

        if cmd == "eval":
            return self.evaluate(args.get("expr") or "", int(args.get("frame", 0)))

        if cmd == "get_output":
            return {"stdout": (self.out_writer.getvalue() if self.out_writer else "")[-2000:],
                    "stderr": (self.err_writer.getvalue() if self.err_writer else "")[-2000:]}

        if cmd == "state":
            return {"status": self.status, "finished": self.finished,
                    "steps": self.step_count, "depth": self.depth,
                    "used_seconds": round(self.used, 3),
                    "budget": self.budget,
                    "result": T.safe_repr(self.result),
                    "error": self.error}

        raise ValueError(f"未知命令: {cmd}")

    def serve(self) -> None:
        def reader() -> None:
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    self.q.put(json.loads(line))
                except BaseException:  # noqa: BLE001
                    self.q.put({"id": None, "cmd": "__bad__"})

        threading.Thread(target=reader, daemon=True).start()
        threading.Timer(PROCESS_WATCHDOG, lambda: os._exit(2)).start()

        while True:
            msg = self.q.get()
            if msg.get("cmd") == "quit":
                _emit({"id": msg.get("id"), "ok": True, "result": {"bye": True}})
                os._exit(0)
            self.handle(msg)


if __name__ == "__main__":
    DebugSession().serve()
