"""沙盒「类级」支持验收测试。

覆盖 runner.run_suite(kind="class") / run_problem 的类级语义，共 7 节：
  1. 数据集 3 道 kind=class 真题：正解 public + adversarial 全绿
  2. 错解识别：含"公开全过、对抗被挂"的伪正确样本
  3. 类级语义：同实例共享 / reset=true 重建 / method 缺省回落 / kwargs 透传
  4. 逐用例归因：构造失败 / 方法缺失 / 运行期异常 / 超时 / 语法错误
  5. 函数级回归（kind 缺省仍走函数路径）
  6. 主链路透传：Checker 真实执行类级题、伪正确识别、取证状态回放（仅父仓库）
  7. 源码级调试：类方法入口三种写法、方法帧断点 / 变量 / 求值、错误分支

用法（在仓库根目录）：
    python scripts/test_class_sandbox.py                           # 测主仓库 app/sandbox
    SANDBOX_IMPL=submodule python scripts/test_class_sandbox.py    # 测 submodules/sandbox

退出码：仅当沙盒层断言失败时为 1；集成层问题记 WARN，不影响退出码。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMPL = os.getenv("SANDBOX_IMPL", "parent").lower()
BASE = ROOT if IMPL != "submodule" else ROOT / "submodules" / "sandbox"

# 决定 import 到哪一份 app/sandbox
sys.path.insert(0, str(BASE))
for mod in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
    del sys.modules[mod]

from app.sandbox.runner import run_problem, run_suite  # noqa: E402

PASS, FAIL, WARN = [], [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def warn(name: str, detail: str = "") -> None:
    """集成层发现的问题：不计入沙盒模块本身的通过率。"""
    WARN.append(name)
    print(f"  [WARN] {name}" + (f"  -- {detail}" if detail else ""))


def statuses(suite) -> list[str]:
    return [r.status for r in suite.results]


# --------------------------------------------------------------- 被测代码

RL_OK = '''
class RateLimiter:
    def __init__(self, limit, window_sec):
        self.limit = limit
        self.window_sec = window_sec
        self.hits = {}

    def allow(self, key, now):
        h = [t for t in self.hits.get(key, []) if t > now - self.window_sec]
        if len(h) >= self.limit:
            self.hits[key] = h
            return False
        h.append(now)
        self.hits[key] = h
        return True
'''

# 窗口从不滑动的错误实现：公开用例全过，对抗用例被抓（典型"伪正确"）
RL_BUG = '''
class RateLimiter:
    def __init__(self, limit, window_sec):
        self.limit = limit
        self.window_sec = window_sec
        self.hits = {}

    def allow(self, key, now):
        h = self.hits.setdefault(key, [])
        if len(h) >= self.limit:
            return False
        h.append(now)
        return True
'''

ORDER_OK = '''
class Order:
    def __init__(self):
        self.state = "created"

    def pay(self):
        if self.state == "created":
            self.state = "paid"
            return True
        return False

    def ship(self):
        if self.state == "paid":
            self.state = "shipped"
            return True
        return False

    def complete(self):
        if self.state == "shipped":
            self.state = "done"
            return True
        return False

    def cancel(self):
        if self.state in ("created", "paid"):
            self.state = "cancelled"
            return True
        return False
'''

# 已发货仍允许取消：公开用例全过，对抗第 7 例被抓
ORDER_BUG = '''
class Order:
    def __init__(self):
        self.state = "created"

    def _to(self, src, dst):
        if self.state == src:
            self.state = dst
            return True
        return False

    def pay(self):
        return self._to("created", "paid")

    def ship(self):
        return self._to("paid", "shipped")

    def complete(self):
        return self._to("shipped", "done")

    def cancel(self):
        if self.state == "cancelled":
            return False
        self.state = "cancelled"
        return True
'''

LRU_OK = '''
class LRUCache:
    def __init__(self, capacity):
        self.capacity = capacity
        self.data = {}
        self.order = []

    def get(self, key):
        if key not in self.data:
            return None
        self.order.remove(key)
        self.order.append(key)
        return self.data[key]

    def put(self, key, value):
        if key in self.data:
            self.order.remove(key)
        self.order.append(key)
        self.data[key] = value
        if len(self.order) > self.capacity:
            self.data.pop(self.order.pop(0), None)
        return None
'''

# get 不刷新使用顺序 -> 退化成 FIFO
LRU_BUG = '''
class LRUCache:
    def __init__(self, capacity):
        self.capacity = capacity
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def put(self, key, value):
        if key not in self.data and len(self.data) >= self.capacity:
            self.data.pop(next(iter(self.data)))
        self.data[key] = value
        return None
'''

COUNTER = '''
class Counter:
    def __init__(self, start=0):
        self.n = start

    def bump(self):
        self.n += 1
        return self.n
'''

BOOM_CTOR = '''
class Boom:
    def __init__(self, *a, **k):
        raise ValueError("ctor denied")

    def go(self):
        return 1
'''

BAD_SYNTAX = '''
class Broken:
    def go(self)
        return 1
'''




def section(title: str) -> None:
    print(f"\n=== {title} ===")


def load_problem(pid: str) -> dict:
    path = ROOT / "datasets" / "code" / "engineering" / "class" / f"{pid}.json"
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------- 1. 真题对齐
section(f"1. 数据集真题 kind=class（实现：{IMPL} / 根目录 {BASE.name}）")
CASES = {"eng_cls_001": RL_OK, "eng_cls_002": ORDER_OK, "eng_cls_003": LRU_OK}

for pid, code in CASES.items():
    prob = load_problem(pid)
    ok = run_problem(code, prob, timeout=5)
    check(
        f"{pid} 正解 public 全过",
        ok.get("public", {}).get("all_passed") is True,
        f"public={ok.get('public', {}).get('passed')}/{ok.get('public', {}).get('total')}",
    )
    if "adversarial" in ok:
        check(
            f"{pid} 正解 adversarial 全过",
            ok["adversarial"]["all_passed"] is True,
            f"adv={ok['adversarial']['passed']}/{ok['adversarial']['total']}",
        )

# --------------------------------------------------------------- 2. 错解识别
section("2. 错解是否被抓（含'公开过、对抗挂'的伪正确样本）")
for pid, code in {"eng_cls_001": RL_BUG, "eng_cls_002": ORDER_BUG, "eng_cls_003": LRU_BUG}.items():
    prob = load_problem(pid)
    r = run_problem(code, prob, timeout=5)
    pub, adv = r.get("public", {}), r.get("adversarial", {})
    check(
        f"{pid} 错解 adversarial 未全过（期望被抓）",
        adv.get("all_passed") is False,
        f"public={pub.get('passed')}/{pub.get('total')} adv={adv.get('passed')}/{adv.get('total')} "
        f"首个失败={adv.get('failure_samples', [{}])[0].get('index', '?')}",
    )

# 伪正确样本专项：限流器必须"公开全过 + 对抗失败"
prob = load_problem("eng_cls_001")
r = run_problem(RL_BUG, prob, timeout=5)
check(
    "eng_cls_001 伪正确样本（public 全过 / adv 被抓）",
    r["public"]["all_passed"] is True and r["adversarial"]["all_passed"] is False,
    f"public={r['public']['passed']}/{r['public']['total']} adv={r['adversarial']['passed']}/{r['adversarial']['total']}",
)

# --------------------------------------------------------------- 3. 有状态语义
section("3. 类级语义：同实例共享 / reset / method 缺省 / kwargs")

tests = [{"method": "bump", "args": [], "expected": n} for n in (1, 2, 3)]
s = run_suite(COUNTER, "bump", tests, kind="class", class_name="Counter")
check("同实例顺序执行（3 次 bump 得到 1/2/3）", s.all_passed, f"statuses={statuses(s)}")

tests = [
    {"method": "bump", "args": [], "expected": 1},
    {"reset": True, "method": "bump", "args": [], "expected": 1},
    {"method": "bump", "args": [], "expected": 2},
]
s = run_suite(COUNTER, "bump", tests, kind="class", class_name="Counter")
check("reset=true 重建实例", s.all_passed, f"statuses={statuses(s)}")

# 反向对照：若 runner 每个用例都重建实例，第 2 次 bump 会返回 1；
# 实际返回 2，说明用例确实串在同一实例上，没有隐式重置。
s = run_suite(
    COUNTER, "bump", [{"method": "bump", "args": [], "expected": 1},
                      {"method": "bump", "args": [], "expected": 1}],
    kind="class", class_name="Counter",
)
check("无隐式重置：第 2 例实际拿到 2 而非 1（对照 [1,1] 应失败）",
      s.results[1].status == "wrong_answer" and s.results[1].actual == 2,
      f"statuses={statuses(s)} actual={[r.actual for r in s.results]}")

s = run_suite(COUNTER, "bump", [{"args": [], "expected": 1}], kind="class", class_name="Counter")
check("method 缺省回落到 entry_point", s.all_passed, f"statuses={statuses(s)}")

KW_CODE = '''
class Scaler:
    def __init__(self, base, offset=0):
        self.base = base
        self.offset = offset

    def scale(self, x, factor=2):
        return x * factor + self.base + self.offset
'''
s = run_suite(
    KW_CODE, "scale", [{"args": [3], "kwargs": {"factor": 4}, "expected": 14}],
    kind="class", class_name="Scaler", init_args=[1], init_kwargs={"offset": 1},
)
check("init_kwargs / 用例 kwargs 透传", s.all_passed,
      f"actual={s.results[0].actual if s.results else None}")

# --------------------------------------------------------------- 4. 逐用例归因
section("4. 逐用例归因：构造失败 / 方法缺失 / 异常 / 超时")

s = run_suite(BOOM_CTOR, "go", [{"args": []}, {"args": []}], kind="class", class_name="Boom")
check("构造失败 -> runtime_error 且带'构造失败'文案",
      all(r.status == "runtime_error" and "构造失败" in r.error for r in s.results),
      f"statuses={statuses(s)} err={s.results[0].error[:40]!r}")

s = run_suite(COUNTER, "bump", [{"method": "nope", "args": []}], kind="class", class_name="Counter")
check("方法不存在 -> missing_entry", s.results[0].status == "missing_entry",
      f"statuses={statuses(s)}")

s = run_suite(RL_OK, "allow", [{"args": ["k"]}], kind="class", class_name="NoSuchClass")
check("类不存在 -> missing_entry", s.results[0].status == "missing_entry",
      f"statuses={statuses(s)}")

RAISE_CODE = '''
class Picker:
    def __init__(self):
        pass

    def head(self, xs):
        return xs[0]

    def ok(self):
        return 1
'''
s = run_suite(
    RAISE_CODE, "head",
    [{"args": [[]]}, {"method": "ok", "args": [], "expected": 1}],
    kind="class", class_name="Picker",
)
check("用例异常归因到正确下标，后续用例继续跑",
      s.results[0].status == "runtime_error" and s.results[1].status == "ok",
      f"statuses={statuses(s)} traceback={'有' if s.results[0].traceback else '无'}")

SPIN = '''
class Spinner:
    def __init__(self):
        pass

    def run(self):
        while True:
            pass
'''
s = run_suite(
    SPIN, "run", [{"args": [], "timeout": 1.0}, {"args": []}],
    kind="class", class_name="Spinner", timeout=1.0,
)
check("死循环 -> 第 0 例 timeout、第 1 例 not_run",
      s.results[0].status == "timeout" and s.results[1].status == "not_run",
      f"statuses={statuses(s)}")

s = run_suite(BAD_SYNTAX, "go", [{"args": []}, {"args": []}], kind="class", class_name="Broken")
check("语法错误 -> 每个用例都报错而不是静默通过",
      all(not r.passed for r in s.results) and s.failed == 2,
      f"statuses={statuses(s)}")

# --------------------------------------------------------------- 5. 函数级回归
section("5. 函数级回归（kind 缺省）")
FN = '''
def add(a, b):
    return a + b
'''
s = run_suite(FN, "add", [{"args": [1, 2], "expected": 3}, {"args": [2, 2], "expected": 5}])
check("函数级正常/错误判定不受影响",
      s.results[0].status == "ok" and s.results[1].status == "wrong_answer",
      f"statuses={statuses(s)}")

s = run_suite(RL_OK, "allow", [{"args": ["u1", 1.0]}])
check("函数模式下把类当函数查 -> missing_entry（预期行为）",
      s.results[0].status == "missing_entry", f"statuses={statuses(s)}")

# --------------------------------------------------------------- 6. 主链路
section("6. 主链路（Checker / tools）类级参数透传")

if IMPL == "parent":
    from app.agents.checker import CheckerAgent  # noqa: E402
    from app.agents.tools import make_checker_handlers  # noqa: E402

    def wrap_code(code: str) -> str:
        return f"## 1. 解题思路\n\n略。\n\n## 4. 代码实现\n\n```python\n{code}\n```\n"

    prob = load_problem("eng_cls_001")
    rep = CheckerAgent(None).inspect(prob, wrap_code(RL_OK))
    check(
        "Checker.inspect 类级题真实执行（不再 missing_entry）",
        rep.public.total > 0 and rep.public.all_passed and rep.adversarial.all_passed,
        f"public={rep.public.passed}/{rep.public.total} "
        f"adv={rep.adversarial.passed}/{rep.adversarial.total}",
    )

    rep_bug = CheckerAgent(None).inspect(prob, wrap_code(RL_BUG))
    check(
        "Checker 能识别类级伪正确样本",
        rep_bug.pseudo_correct and rep_bug.public.all_passed
        and not rep_bug.adversarial.all_passed,
        f"pseudo={rep_bug.pseudo_correct} adv="
        f"{rep_bug.adversarial.passed}/{rep_bug.adversarial.total}",
    )

    fo = rep_bug.forensics[0]["forensic"] if rep_bug.forensics else {}
    trace = json.dumps(fo.get("trace_tail") or {}, ensure_ascii=False)
    check(
        "类级取证回放了前置用例状态",
        "[0.0, 0.0]" in trace,
        f"取证条数={len(rep_bug.forensics)} 方法={rep_bug.forensics[0].get('method') if rep_bug.forensics else None}",
    )

    handlers = make_checker_handlers(prob)
    tool_out = json.loads(handlers["run_adversarial_tests"]({"code": RL_BUG}))
    check(
        "LLM 工具 run_adversarial_tests 支持类级题",
        tool_out.get("total") == 4 and tool_out.get("passed") == 3,
        f"total={tool_out.get('total')} passed={tool_out.get('passed')}",
    )
else:
    print("  [SKIP] 主链路属于父仓库，子模块实现下跳过")

# --------------------------------------------------------------- 7. 调试侧
section("7. 源码级调试（断点 / 单步 / 求值）对类方法的支持")

from app.sandbox import debug_api as D  # noqa: E402

DBG_CODE = (
    "class RateLimiter:\n"
    "    def __init__(self, limit, window_sec):\n"
    "        self.limit = limit\n"
    "        self.window_sec = window_sec\n"
    "        self.hits = {}\n"
    "\n"
    "    def allow(self, key, now):\n"
    "        h = [t for t in self.hits.get(key, []) if t > now - self.window_sec]\n"
    "        if len(h) >= self.limit:\n"
    "            return False\n"
    "        h.append(now)\n"
    "        self.hits[key] = h\n"
    "        return True\n"
)
BP_LINE = next(i for i, ln in enumerate(DBG_CODE.splitlines(), 1) if "if len(h)" in ln)
WRAPPED = DBG_CODE + (
    "\n"
    "def __entry__():\n"
    "    rl = RateLimiter(2, 10.0)\n"
    '    rl.allow("u1", 1.0)\n'
    '    rl.allow("u1", 2.0)\n'
    '    return rl.allow("u1", 3.0)\n'
)

# 7.1 类方法入口的三种写法都要能启动
for tag, entry, kwargs in (
    ("点号形式 Class.method", "RateLimiter.allow", {"init_args": [2, 10.0]}),
    ("字段形式 class_name + 方法名", "allow",
     {"class_name": "RateLimiter", "init_args": [2, 10.0]}),
    ("两者同时给出", "RateLimiter.allow",
     {"class_name": "RateLimiter", "init_args": [2, 10.0]}),
):
    r = D.start_session(DBG_CODE, entry, ["u1", 1.0], stop_on_entry=True, **kwargs)
    if r.get("ok"):
        check(f"类方法入口可启动：{tag}", True)
        D.close_session(r["session_id"])
    else:
        check(f"类方法入口可启动：{tag}", False, f"error={r.get('error')}")

# 7.2 错误分支要报清楚
for tag, entry, kwargs, expect in (
    ("类不存在", "Nope.allow", {}, "未找到类"),
    ("方法不存在", "RateLimiter.nope", {"init_args": [2, 10.0]}, "未找到方法"),
):
    r = D.start_session(DBG_CODE, entry, [], **kwargs)
    check(f"错误分支可读：{tag}",
          (not r.get("ok")) and expect in (r.get("error") or ""),
          f"error={r.get('error')}")

# 7.2 模块级包装函数 + 方法内条件断点
r = D.start_session(WRAPPED, "__entry__", [], stop_on_entry=True)
if not r.get("ok"):
    check("包装函数方式启动调试会话", False, f"error={r.get('error')}")
else:
    sid = r["session_id"]
    D.set_breakpoint(sid, BP_LINE, condition="len(h) >= 2")
    ev = D.continue_(sid).get("event", {}) or {}
    cur = ev.get("current") or {}
    check("方法内条件断点命中（depth=2 的 allow 帧）",
          bool(ev.get("stopped")) and ev.get("reason") == "breakpoint#1" and cur.get("func") == "allow",
          f"reason={ev.get('reason')} depth={ev.get('depth')} func={cur.get('func')}")
    loc = D.get_locals(sid).get("locals", {}) or {}
    check("方法帧局部变量可读（self / key / now / h）", {"self", "key", "now", "h"} <= set(loc),
          f"keys={sorted(loc)}")
    val = (D.evaluate(sid, "self.hits").get("result") or {})
    check("方法帧内表达式求值", bool(val.get("ok")) and "u1" in str(val.get("value")),
          f"value={val.get('value')}")
    check("where 定位到方法内行号", "in allow" in (D.where(sid).get("where") or ""),
          D.where(sid).get("where") or "")
    D.close_session(sid)

# --------------------------------------------------------------- 汇总
print("\n" + "=" * 62)
print(f"实现：{IMPL}  |  通过 {len(PASS)} / 失败 {len(FAIL)} / 告警 {len(WARN)}")
if FAIL:
    for name in FAIL:
        print(f"  FAILED: {name}")
if WARN:
    for name in WARN:
        print(f"  WARN:   {name}")
print("=" * 62)
sys.exit(1 if FAIL else 0)
