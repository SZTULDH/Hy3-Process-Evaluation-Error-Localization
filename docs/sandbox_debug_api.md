# 沙盒调试层 · 标准接口文档

给 Agent（以及人）用的 **Python 源码级调试能力**：断点、单步、栈帧与变量查看、
表达式求值、执行轨迹回溯。它调试的对象是沙盒里那份**候选代码**——也就是
Solver 生成的 `candidate.py`。

与"跑一遍测试看红绿"的 `app/sandbox/runner.py` 是互补关系：

| | runner.py | 调试层 |
|---|---|---|
| 目的 | 判定对不对（测试通过率） | 回答"为什么错"（定位与取证） |
| 粒度 | 用例级 | 行级 / 变量级 |
| 交互 | 一次性 | 可长驻会话、反复探查 |

---

## 1. 三种接入方式

### 1.1 Python API（稳定契约，`app/sandbox/debug_api.py`）

所有函数返回可 JSON 序列化的 `dict`，**不抛异常**，错误统一为 `{"ok": false, "error": "..."}`。

```python
from app.sandbox import debug_api as D

r = D.start_session(code, "two_sum", [[2, 7, 11, 15], 9])
sid = r["session_id"]                       # 默认停在函数第一行

D.set_breakpoint(sid, line=6, condition="i == 0 and j == 1")
ev = D.continue_(sid)["event"]              # 停靠快照 / 退出事件

D.get_locals(sid)                           # {'nums': '[2, 7, 11, 15]', 'i': '0', ...}
D.evaluate(sid, "nums[i] + nums[j]")        # {'ok': True, 'value': '9'}
D.get_stack(sid)                            # 调用栈 + 各帧局部变量
D.get_trace(sid, limit=50)                  # 最近 N 步：行号 + 当时变量
D.close_session(sid)
```

一键取证（评估流水线最常用，不用下断点）：

```python
out = D.run_to_error(code, "two_sum", [[2, 7, 11, 15], 9])
# out: status / result / error / traceback / steps / trace_tail / stdout
```

### 1.2 CLI（`scripts/sandbox_debug.py`）

```bash
# 交互式 REPL
python scripts/sandbox_debug.py --problem datasets/code/hard/length_of_longest_substring.json --args '"abba"'

# 或直接指定源码
python scripts/sandbox_debug.py --code-file demo.py --entry two_sum --args '[2,7,11,15]' 9

# 一次性跑到底，输出 JSON（适合脚本）
python scripts/sandbox_debug.py --problem datasets/code/.../x.json --run --trace 60
```

REPL 命令：`b <行> [条件]` 下断点 · `c/n/s/r` 继续/跳过/进入/跳出 ·
`p <表达式>` 求值 · `l` 局部变量 · `bt` 调用栈 · `t [n]` 执行轨迹 · `o` 程序输出 · `q` 退出。

### 1.3 MCP Server（标准协议，任意支持 MCP 的 Agent 可直连）

```bash
python -m app.sandbox.mcp_server
```

客户端配置：

```json
{
  "mcpServers": {
    "hy3-sandbox": {
      "command": "<python 解释器绝对路径>",
      "args": ["-m", "app.sandbox.mcp_server"],
      "cwd": "<仓库根目录>"
    }
  }
}
```

实现了 MCP 最小可用子集：`initialize` / `tools/list` / `tools/call` / `ping` / `shutdown`，
零第三方依赖。工具清单见 `debug_api.TOOLS`，当前 13 个：

`sandbox_start_session` · `sandbox_set_breakpoint` · `sandbox_continue` ·
`sandbox_step_over` · `sandbox_step_into` · `sandbox_step_out` · `sandbox_get_stack` ·
`sandbox_get_locals` · `sandbox_evaluate` · `sandbox_get_trace` · `sandbox_get_state` ·
`sandbox_run_to_error` · `sandbox_close_session`

也可以用统一入口调用：

```python
D.call_tool("sandbox_run_to_error", {"code": code, "entry_point": "f", "args": [0]})
```

---

## 2. 能力清单

| 能力 | 接口 | 说明 |
|---|---|---|
| 行断点 | `set_breakpoint(line)` | 命中即停 |
| 条件断点 | `set_breakpoint(line, condition="i > 0")` | 在目标帧内求值 |
| 命中次数断点 | `set_breakpoint(line, hit_condition=">=3")` | 支持 `>=n` / `==n` / `%n` |
| 单步 | `step_over` / `step_into` / `step_out` | 按行步进 |
| 调用栈 | `get_stack(frame=i)` | 只显示候选代码自身的帧 |
| 变量 | `get_locals(frame=i)` | 值为 `repr` 字符串，截断 200 字符 |
| 表达式求值 | `evaluate(expr, frame=i)` | 在当前帧求值；也支持 `x = 1` 探查（在副本上跑，不影响真实栈帧） |
| 执行轨迹 | `get_trace(limit)` | 保留最近 N 步的行号与当时变量，**无需断点**即可事后回溯 |
| 输出捕获 | `get_output()` | 候选代码的 print 不会污染协议流 |
| 异常取证 | `run_to_error` | 异常栈 + 崩溃前最后若干步 |
| 重跑 | `restart(...)` | 可换 code / args / 断点 |

### 护栏（重要）

调试的是 LLM 生成的代码，必须假设它会死循环：

* **时间预算** `budget`（默认 10s）：只统计**真正在跑**的时间，断点处 Agent 思考多久都不计入
* **步数护栏** `max_steps`（默认 20 万）：先撞哪个算哪个
* **进程看门狗** 600s 硬上限
* 命中护栏返回 `status="aborted"` 并说明原因，不会挂死

---

## 3. 与过程评估流水线的结合

调试层是 PROPOSAL 里"基于执行轨迹的细粒度错误定位"的落地手段：

1. `runner.py` 发现对抗测试失败 → 拿到**失败输入**
2. `run_to_error(code, entry, 失败输入)` → 得到异常栈或返回值
3. 若无异常（逻辑错而非崩溃）→ `get_trace()` 回溯最后 N 步，观察变量如何偏离预期
4. 需要更精细时 → 下条件断点，只在对应用例的输入下停住，逐步观察

典型场景：`length_of_longest_substring("abba")` 返回 3（应为 2）。
跑 `--run --trace` 能看到第 8 行 `if ch in seen:` 命中后 `left` 只 +1 而不是收缩到
重复字符之后——**错误定位到具体行与变量**，比"对抗测试 2/3 失败"精确得多。

---

## 4. 底层协议（供扩展参考）

`debugger.py` 拉起 `_dbgharness.py` 子进程，stdin/stdout 各一行一个 JSON：

```
请求  {"id": 1, "cmd": "continue", "args": {}}
响应  {"id": 1, "ok": true, "result": {...}}
事件  {"event": "stopped"|"exited", ...}
```

命令：`load` `restart` `set_breakpoint` `remove_breakpoint` `list_breakpoints`
`continue` `step_over` `step_into` `step_out` `pause` `get_stack` `get_locals`
`get_trace` `eval` `get_output` `state` `quit`

实现要点：

* 目标函数在**独立线程**运行并安装 `sys.settrace`；命中时该线程阻塞在 Event 上，
  主线程读写协议——"停下来等命令"不会卡死调试器自己
* 事件由**工作线程直接发出**（走队列会在批量灌命令时漏事件）
* `load` 之后客户端必须等到第一个事件并暂存为 pending，否则跑得快的程序
  会在 `continue` 之前就退出，客户端只能干等一个永不到达的事件

---

## 5. 已知限制

* **仅支持 Python**，且是行级源码调试，不是字节码/汇编级
* 只跟踪候选代码文件，单步不会进入标准库/第三方库内部
* 变量值是 `repr` 快照，不做对象图展开；大对象会被截断
* `evaluate` 的语句模式在副本上执行，**不会**真的修改被调程序的变量
* 沙盒限制是"尽力而为"（禁用 open/eval/exec 与危险模块），**不是安全边界**；
  处理不可信代码仍应在容器内运行

---

## 6. 踩坑记录（本机 Windows 实测）

1. **`python -I` 隐含 `-P`**（3.11+）：脚本所在目录不进 `sys.path`，
   子进程 `import tracer` 会失败。已在 `_dbgharness.py` 里显式插入脚本目录。
2. **stdout 编码**：中文 Windows 上子进程 stdout 走 locale 编码（GBK），
   客户端按 UTF-8 解码会抛 `UnicodeDecodeError` 并**打死读线程**，
   表现为莫名其妙的"响应超时"。两边都已加固：子进程 `reconfigure(encoding="utf-8")`，
   客户端 `errors="replace"` + 读线程绝不因单条报文退出。
   （与 `runner.py` 改用文件传递结果是同一类坑，见 `docs/design.md`）
