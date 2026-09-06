"""调试层冒烟测试：断点 / 单步 / 栈帧 / 变量 / 求值 / 轨迹 / 条件断点。"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sandbox.debugger import DebugSession, debug_once, as_stop  # noqa: E402

CODE = '''
def two_sum(nums, target):
    n = len(nums)
    for i in range(n):
        for j in range(i + 1, n):
            if nums[i] + nums[j] == target:
                return [i, j]
    return -1
'''


def main() -> int:
    ok = True

    def check(name, cond, extra=""):
        nonlocal ok
        print(f"{'PASS' if cond else 'FAIL'}  {name} {extra}")
        if not cond:
            ok = False

    # ---- 1. 断点 + 变量 + 求值
    with DebugSession(CODE, "two_sum", [[2, 7, 11, 15], 9], budget=5.0) as dbg:
        bp = dbg.set_breakpoint(line=6, condition="i == 0 and j == 1")
        check("set_breakpoint", bp == 1, f"id={bp}")

        ev = dbg.continue_()
        stop = as_stop(ev)
        check("continue 命中条件断点", stop is not None and stop.line == 6,
              f"line={stop.line if stop else None}")

        loc = dbg.locals()
        check("查看局部变量", loc.get("i") == "0" and loc.get("j") == "1", f"{loc}")

        r = dbg.eval("nums[i] + nums[j]")
        check("表达式求值", r.get("ok") and r.get("value") == "9", f"{r}")

        # 栈帧
        st = dbg.stack()
        check("调用栈", st["stack"][0]["func"] == "two_sum", f"depth={len(st['stack'])}")

        # 单步
        ev2 = dbg.step_over()
        s2 = as_stop(ev2)
        check("step_over 前进", s2 is not None and s2.line != stop.line,
              f"{stop.line} -> {s2.line if s2 else None}")

    # ---- 2. 一次性跑完 + 轨迹（target 取不到，6 组 (i,j) 全跑一遍）
    rep = debug_once(CODE, "two_sum", [[2, 7, 11, 15], 999],
                     breakpoints=[{"line": 6}], max_stops=50, collect_trace=20)
    check("debug_once 收集停靠点", rep["stop_count"] == 6, f"{rep['stop_count']}")
    check("debug_once 拿到退出事件", rep["exit"] is not None,
          f"result={rep['exit'].get('result') if rep['exit'] else None} (max_stops 未截断)")
    check("执行轨迹", len(rep["trace"]["steps"]) > 0,
          f"total={rep['trace']['total_steps']}")

    # ---- 3. 无断点事后回溯：找到返回值之前最后执行的行
    rep2 = debug_once(CODE, "two_sum", [[2, 7, 11, 15], 9], collect_trace=200)
    tail = rep2["trace"]["steps"]
    check("事后回溯定位最后执行行", bool(tail) and tail[-1]["line"] in (6, 7, 8),
          f"last_line={tail[-1]['line'] if tail else None}")

    # ---- 4a. 步数护栏
    LOOP = "def forever():\n    x = 0\n    while True:\n        x += 1\n    return x\n"
    with DebugSession(LOOP, "forever", [], budget=30.0, max_steps=50_000) as dbg:
        ev = dbg.continue_()
        check("死循环被步数护栏终止", ev.get("event") == "exited"
              and "max_steps" in (ev.get("error") or ""), f"{ev.get('status')}: {ev.get('error')}")

    # ---- 4b. 时间预算护栏（每步耗时大，先撞预算而不是步数）
    SLOW = "def slow():\n    while True:\n        s = sum(range(200000))\n    return s\n"
    with DebugSession(SLOW, "slow", [], budget=1.0, max_steps=10_000_000) as dbg:
        ev = dbg.continue_()
        check("长时间运行被预算终止", ev.get("event") == "exited"
              and "budget" in (ev.get("error") or ""), f"{ev.get('status')}: {ev.get('error')}")

    # ---- 5. 异常：拿到 traceback
    BAD = "def boom(a):\n    return 1 // a\n"
    with DebugSession(BAD, "boom", [0], budget=5.0) as dbg:
        ev = dbg.continue_()
        check("异常可捕获", ev.get("event") == "exited"
              and "ZeroDivisionError" in (ev.get("error") or ""), f"{ev.get('error')}")
        check("异常带 traceback", "candidate.py" in (ev.get("traceback") or ""))

    # ---- 6. print 不污染协议流
    NOISY = 'def f():\n    print("hello from candidate")\n    return 1\n'
    with DebugSession(NOISY, "f", [], budget=5.0) as dbg:
        ev = dbg.continue_()
        check("stdout 被捕获而非污染协议", ev.get("event") == "exited"
              and "hello from candidate" in (ev.get("stdout") or ""), f"{ev.get('stdout')!r}")

    print("\n=== ", "ALL PASS" if ok else "HAS FAILURE", " ===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
