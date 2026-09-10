# 过程评估工作台（Web GUI）

一个零第三方依赖的本地网页界面，覆盖两类评估场景：

- **算法竞赛** —— 给定题目与测试数据，评估解法思路、复杂度分析与边界处理
- **代码任务** —— 给定需求与测试用例，评估实现逻辑的正确性，而非仅判断测试是否通过

全程**流式输出**：模型生成的解答逐字显示，执行结果和每段审查结论算完即推。

---

## 一、启动

```bash
python webapp/server.py            # http://127.0.0.1:8787
python webapp/server.py 9000       # 换端口
```

启动时会打印当前生效的配置：

```
模型     : hy4-preview
接口     : https://tokenhub.tencentmaas.com/v1
API Key  : 已配置
思考默认 : disabled
```

Key 放在仓库根 `.env`（已被 `.gitignore` 忽略）：

```
HY3_API_KEY=sk-...
HY3_BASE_URL=https://tokenhub.tencentmaas.com/v1
HY3_MODEL=hy4-preview
HY3_THINKING=disabled
```

> `.env` 由 `app/config.py` 的 `_load_dotenv()` 读取，**环境变量优先级更高**，不会被 `.env` 覆盖。

---

## 二、界面速览

| 区域 | 说明 |
|---|---|
| 模式切换 | 「算法竞赛」/「代码任务」，只改提示侧重与字段文案，评估链路相同 |
| **模型** | 24 个官方模型下拉可选，也可直接手填清单外的自定义模型名 |
| **思考强度** | 默认 / 关闭 / 低 / 中 / 高，见第四节 |
| 题面区 | 标题、描述、入口函数名、期望复杂度、函数签名、约束 |
| 测试数据 | 公开测试（必填）、对抗测试（选填但强烈建议填） |
| 候选解答 | 留空则由模型现生成；粘贴自己的解答则评估它 |
| **Agent 工作流** | Checker 与 Critic 每一步的真实输入 / 输出，逐步实时展开 |
| 结果区 | 实时生成 → 执行结果 → 分步审查 → 总体结论 |

### 从题库载入（懒加载）

题面卡片顶部有个「从题库载入」折叠块，直接读 `datasets/code/` 下的 50 题，**不用手抄题面**：

1. 展开时才请求 `/api/problems` 拿**索引**（只有 id / 标题 / 难度 / 用例数，约 50 条轻量记录）
2. 按难度筛选或搜题名、函数名
3. 选中某题才请求 `/api/problem?id=...` 拿**完整题面**（正文 + 全部测试用例）
4. 「载入题面」填进左侧表单；「载入题面 + 内置解答」会额外把数据集里的 `mock_solution`
   拼成五段文档填进候选解答 —— **跳过模型生成，直接评估**，几秒就能看完整条链路

索引在服务端缓存，只扫一次盘；题面正文始终按需单条读取，不会 50 题一股脑塞进页面。
坏 JSON 会被逐文件跳过并提示（不会像 `datasets.load_all()` 那样整体崩）。

**测试用例格式**：JSON 数组，也容忍一行一个（JSON Lines）和单个对象。

```json
[{"args": ["11","123"], "expected": "134"},
 {"args": ["0","0"],    "expected": "0"}]
```

`args` 一定是数组（多参数按顺序放入）；写 `input` 也可以，会自动归一化。

**两个实用选项：**

- **只评估代码实现**：粘贴的内容是纯代码时勾上，跳过思路/复杂度/边界的过程审查，只跑 1 次评审（快 5 倍）。适合"我就想知道这段逻辑对不对"。
- **对抗测试**：这是识别伪正确的关键。公开测试过了但对抗没过 = 逻辑有问题。

---

## 三、输出怎么读

四个判定灯：

| 灯 | 含义 |
|---|---|
| 结果正确 | 公开测试是否全过（传统意义上的"对"） |
| 真实正确 | 公开 **+ 对抗**测试都过（逻辑层面的"对"） |
| 过程成立 | 五个步骤是否都成立（只审代码时 = 实现是否成立） |
| 伪正确 | 测试通过但逻辑不成立 —— **这个灯亮才有意思** |

三者解耦的意义：识别出"结果正确但过程不成立"的伪正确样本。这是只看测试通过率发现不了的。

每段审查给出 `成立 / 可疑 / 不成立` + 置信度 + 依据 + 证据引用，并标注**信号一致性**：

- `agree` —— 规则信号（真实执行）与 LLM 审查结论一致
- `rule_only` —— 只有执行证据发现问题，LLM 没看出来
- `llm_only` —— 只有 LLM 发现，规则覆盖不到
- `conflict` —— 有分歧（此时以执行证据为准，但如实记录）

---

## 四、模型与思考强度

两个参数，两个开关，官方把它们分开：

| 参数 | 取值 | 作用 |
|---|---|---|
| `thinking.type` | `enabled` / `disabled` | 是否开启深度思考（会返回 `reasoning_content`） |
| `reasoning_effort` | `low` / `medium` / `high`（部分模型还有 `none` / `max`） | 推理深度，越高越充分，延迟与 token 也越高 |

界面上的「思考强度」把二者合成一档，由 `app/config.py::resolve_thinking()` 按模型能力展开：

| 选择 | 下发参数 |
|---|---|
| 默认（跟随模型） | 按模型默认：`thinking.type` + 不下发 `reasoning_effort` |
| 关闭（最快） | `thinking.type=disabled` |
| 低 / 中 / 高 | `thinking.type=enabled` + `reasoning_effort=low/medium/high` |

**模型能力差异会自动处理**，无需记表格：

- 不支持关闭思考的模型（GLM-5.3 / 5.3-Flash、Kimi-K2.7-Code 系、MiniMax-M2.7 / M2.5）→ 选「关闭」会降级为**最低推理深度**，而不是硬下发 `disabled` 让接口报错；界面上该选项会被置灰并标注。
- 档位不全的模型（如 hy4-preview 只支持 `none` / `high`）→ 选中的档位会被就近收敛，界面上不支持的档位置灰。
- 清单外的自定义模型 → 按最宽松处理，原样透传，不做收敛。

模型清单来自官方《深度思考》文档，写在 `app/config.py::MODEL_CATALOG`，由 `/api/config` 下发给前端。

### 耗时参考（hy 系列实测）

| | 关闭 | 开启（高） |
|---|---|---|
| 单题全流程 | **约 100 秒** | 约 35 分钟 |
| 首字到达 | 1.5 秒 | 慢得多（先吐思考） |

慢的根因不是网络，是**模型在推理 token 上花的生成时间**——实测一次五段解答，正文仅约 2900 字符，thinking 却要 274 秒。所以：

- 批量跑、看总体指标 → **关闭** 或 **低**
- 单题深挖、想看模型怎么想的 → **中 / 高**（界面会展示完整思考过程）

实现上两条路径都覆盖了：urllib 直连把 `thinking` / `reasoning_effort` 放进 body，
openai SDK 必须走 `extra_body`（否则被 SDK 静默丢弃）。

> 网关另支持 `/v1/responses` 端点（默认带 reasoning）。当前 GUI 走的是 `/chat/completions`，因为它能显式关闭思考。

---

## 五、Agent 工作流面板

两个评审 Agent 的每一步都会实时推到界面上，**点开就能看到这一步喂给模型什么、模型回了什么**。

**Agent 1 · Checker**（执行取证，不靠 LLM 猜）

| 步骤 | 展示内容 |
|---|---|
| ① 解析解答 / 抽取代码 | 输入：解答字符数、入口函数 → 输出：识别到哪些段落、抽出多少字符代码 |
| ② 执行公开测试 | 输入：用例数 → 输出：通过数、失败样本 |
| ③ 执行对抗测试 | 同上，是识别伪正确的主要依据 |
| ④ 规则层静态分析 | 输出：命中条数、错误类型、信号字段 |
| ⑤ 失败用例轨迹取证 | 输出：取证条数与摘要（需 sandbox 子模块，未初始化时为 0） |
| ⑥ LLM 二次诊断总评 | **真实 prompt 与原始返回**都可展开，输出 summary / root_cause / pseudo_correct |

**Agent 2 · Critic**（逐段裁决，LLM 软判断）

每段先推「running」（段落名、字符数、预览、规则命中），审完推「done」：
裁决结果、置信度、错误类型、依据、信号一致性，以及**可展开的完整 prompt 和模型原始 JSON**。

这一层的价值在于把黑盒评审拆开：若某段判了「不成立」，可以直接看是哪条规则命中、
还是 LLM 自己的判断（`agreement` 字段区分 `agree` / `rule_only` / `llm_only`）。

实现上靠两个「观察袋」，不侵入业务逻辑：

- `Critic.review_section(..., trace=dict)` —— 写入 prompt / llm_raw / 裁决中间量
- `CheckerAgent.inspect(..., on_step=cb)` 与 `summarize(..., trace=dict)` —— 逐步回调

并发注意：`trace` 必须是**每个任务自己的 dict**（默认 `CRITIC_PARALLELISM=1` 串行，调大时每个 job 各持一份）。

---

## 六、流式是怎么做的

后端 `/api/evaluate/stream` 返回 SSE，事件序列：

```
start → stage(生成解答) → [reasoning_delta ×N] → delta ×N → solution
      → stage(执行+规则) → agent(checker ×5) → tests
      → stage(分步审查) → agent(critic running ×N) → [section + agent(critic done)] ×N
      → stage(汇总) → agent(checker summary running/done) → done
```

- **reasoning_delta**：思考过程增量，仅开启深度思考时有。实测 1.4s 就开始到达
- **delta**：解答正文增量，实测首字 1.5s 到达，平均间隔 86ms
- **tests**：沙盒真实执行完就推，不等后面的审查
- **agent**：两个 Agent 的步骤流水（见第五节），`status` 为 `running` / `done` / `error`
- **section**：Critic 每段裁决；`CRITIC_PARALLELISM > 1` 时并发执行，谁先算完谁先显示
- **done**：完整结果

思考也必须流式：实测开启深度思考时思考过程要跑 5 分钟以上，攒到最后再推会让前端白屏干等（改前首达 324s，改后 1.4s）。

流式重试有约束：**只在吐出首个增量之前重试**。一旦开始输出就不能重放，否则前端会收到重复内容。

---

## 七、HTTP 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 页面 |
| GET | `/api/config` | 模型清单、当前模型、base_url、是否有 Key、思考默认值 |
| GET | `/api/problems` | 题库**索引**。可选 `?difficulty=medium` / `?q=关键词` |
| GET | `/api/problem?id=medium-014` | 单题**详情**（正文、测试用例、拼好的 mock 解答） |
| POST | `/api/evaluate` | 一次性返回完整结果（JSON） |
| POST | `/api/evaluate/stream` | SSE 流式 |

请求体字段：

```jsonc
{
  "mode": "algorithm | code",
  "model": "hy4-preview",           // 可选，默认取 HY3_MODEL；可填清单外的自定义名
  "effort": "auto | off | low | medium | high",   // 思考强度，默认 auto（跟随模型）
  "thinking": "disabled | enabled", // 可选，旧字段；未给 effort 时才生效
  "title": "可选",
  "description": "题面 / 需求（必填）",
  "entry_point": "函数名",
  "function_signature": "def f(...):",
  "constraints": "约束文本",
  "expected_complexity": {"time": "O(n)"},
  "public_tests": [...],        // JSON 数组或 JSON Lines 字符串
  "adversarial_tests": [...],
  "candidate": "可选，待评估的解答",
  "code_only": false,           // 配合 candidate，只审代码实现
  "backend": "hy3 | mock"       // 可选，默认 hy3；mock 用于离线验证链路
}
```

curl 冒烟：

```bash
curl -N -X POST http://127.0.0.1:8787/api/evaluate/stream \
  -H "Content-Type: application/json" \
  -d '{"mode":"code","description":"实现 add(a,b)","entry_point":"add",
       "public_tests":[{"args":[1,2],"expected":3}]}'
```

---

## 八、本次为此改动的代码

| 文件 | 改动 |
|---|---|
| `webapp/server.py` | 新增。Web 后端 + SSE 流式编排；`build_llm()` 按模型/强度构造 LLM；`_agent_event()` 推送 Agent 步骤 |
| `webapp/index.html` | 新增。前端界面 |
| `app/llm/hy3.py` | 新增 `iter_stream()` / `iter_deltas()` / `_iter_http()` / `_iter_sdk()` 流式生成与 `Delta` |
| `app/llm/base.py` | 新增 `thinking_of()` |
| `app/solver/solver.py` | 新增 `iter_solve()` / `iter_solve_deltas()`；记录 `last_reasoning` |
| `app/agents/checker.py` | 拆出 `inspect()`（纯执行，无 LLM）与 `summarize()`；新增 `on_step` 回调与 `trace` 观察袋 |
| `app/evaluator/critic.py` | `review_section(..., trace=)` 记录真实 prompt 与模型原始输出 |
| `app/config.py` | 新增 `MODEL_CATALOG` / `resolve_thinking()` / `snap_reasoning_effort()` |
| `app/llm/hy3.py` | 强度档位 → `thinking.type` + `reasoning_effort`；`reasoning_effort` 不再受 thinking 开关限制 |
| `app/evaluator/pipeline.py` | 新增 `evaluate_solution()`（评估外部解答、可只审指定段落） |
| `app/evaluator/splitter.py` | 修 `extract_code()`：围栏块 → 缩进块 → 裸代码三级回退 |

**一处必须修的坑**：`solver.py` / `critic.py` / `checker.py` 里原本**硬编码** `thinking="disabled"`，
会把 GUI 的思考开关和 `HY3_THINKING` 配置全部覆盖掉。现统一改为 `thinking_of(self.llm)`
跟随 LLM 实例配置。

**另一处必须修的坑**：`extract_code()` 原来只认 ```` ``` ```` 围栏，用户直接粘贴函数定义（无围栏）
时静默返回空串 —— 代码根本没进沙盒，全部用例报 `missing_entry`，看起来像"代码有错"，
实际是解析失败。现在按 **围栏块 → 缩进块 → 裸代码** 回退，裸代码需通过 `compile()` 校验
（避免把散文当代码）。

配套还有一步：纯代码粘贴时 `split_sections` 切不出段落会让 Critic 判"段落缺失"，
所以后端会把纯代码包成带「代码实现」段的最小文档再评估 —— 只在确实没有段落标题时触发。

改完 mock 全量 50 题回归，指标与改动前完全一致
（55.0 / 75.0 / 16.7 / 100.0 / 82.0），无回归。

---

## 八、已知限制

1. **ground_truth 不可用** —— 数据集里的 `ground_truth` 标注的是 `mock_solution` 的预设缺陷，GUI 里自定义题目没有这个字段，所以**不能**用定位准确率那套指标。界面只展示"结果正确 / 真实正确 / 过程成立"三条与测试执行强相关的判定，它们不依赖预置答案。
2. **源码级取证不可用** —— `submodules/sandbox` 子模块未初始化，`run_forensics` 返回 `ok=False`，失败用例拿不到执行轨迹。不崩，只是少一层证据。
3. **代码执行非安全边界** —— 沙盒是子进程 + 超时 + 危险模块限制，够用但不隔离。只跑自己写的代码。
4. **并发审查的显示顺序** —— `CRITIC_PARALLELISM > 1` 时五段按完成顺序推送，不是段落顺序。设为 1 则严格串行。
5. **开启思考时别中途关页面** —— 服务端会继续跑完（当前不感知客户端断开后的中断）。
