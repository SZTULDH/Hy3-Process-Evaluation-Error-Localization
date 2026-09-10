# 正式运行前就绪记录

- **日期**：2026-09-10
- **分支**：`multi-agent-hy3-dataset`（对应远程 `feature/multi-agent-hy3-dataset`）
- **工作目录**：`C:/Users/L/WorkBuddy/Worktrees/hy3-multi-agent-dataset`
- **状态**：已同步远端 `3692607`；真实模型单题冒烟 92s；**全量未跑，受 P0 阻塞**
- **最近同步**：2026-09-11，远端 `0ad1e33` → `3692607`（5 个提交）

本文档记录就绪检查的实测结果、本次改动、遗留缺口，以及拿到 Key 后的执行方式。目的是让"正式跑"之前的问题都摆在明面上，而不是跑完再从结果里倒推。

---

## 一、环境检查（全部实测，非推断）

| 检查项 | 结果 | 说明 |
|---|---|---|
| Python 依赖 | ✅ 无需安装 | 核心链路纯标准库；`openai` 为可选，未装时 `app/llm/hy3.py` 自动退回 urllib 直连 |
| 数据集题数 | ✅ **50 题** | easy 12 / medium 15 / hard 12 / adversarial 11，编号 1–12、1–15、1–12、1–11 **全部连续无缺口**，与 `docs/dataset.md` 一致 |
| 数据集整体加载 | ✅ 50 题 | 需先修 2 个 JSON 语法错误（见下），否则 `load_all()` 抛 `JSONDecodeError` |
| 全量（真实模型 + 关闭思考） | ✅ 约 1.6 小时 | 单题实测 117s，50 题串行估算 |
| 沙盒执行容错 | ✅ 扎实 | 空代码/语法错/死循环/多余输出均正确判失败；`SuiteResult.all_passed` 有 `total > 0` 保护，空结果不会被误判通过 |
| API Key 注入 | ✅ 通道就绪 | 支持环境变量或仓库根 `.env`（`.env` 已被 `.gitignore` 忽略） |
| 源码级取证（forensics） | ❌ **不可用** | `submodules/sandbox` 未初始化，`run_forensics` 返回 `ok=False`，失败用例的轨迹取证为空 |
| 模型输出解析鲁棒性 | ⚠️ **两处缺口** | 真实模型输出格式不稳时会造成假失败，详见第三节 |

---

## 二、真实模型冒烟结果（2026-09-10 实测）

**题目**：`adversarial-007` 字符串加法　**模型**：`hy4-preview`　**耗时**：2104s（35 分 04 秒）　**退出码**：0

```
公开测试 2/2　对抗测试 3/3
结果正确=True　真实正确=True　过程成立=False
>>> 判定：伪正确样本
首个错误步骤：step_1_approach
```

分步裁决（Critic，置信度均在 0.95 以上）：

| 段落 | 裁决 | conf | 理由摘要 |
|---|---|---|---|
| 解题思路 | **flawed** | 0.95 | 描述称每次只把单个字符转数字后与 carry 相加，"遗漏另一个字符串对应位" |
| 复杂度分析 | **flawed** | 0.95 | 描述称只用常数个变量，但实现里有 O(n) 的 `res` 列表 |
| 关键边界与处理策略 | **flawed** | 0.95 | 前导零处理的描述与代码实际行为不一致 |
| 代码实现 | valid | 0.98 | — |
| 自测说明 | valid | 0.97 | — |

**这份结果本身就是一个强信号，值得单独说明：**

模型**五个测试全过**（`truly_correct=True`），代码段也被 Critic 判 valid（0.98），却仍因前三段的**文字描述**被判 `process_valid=False`，最终打成"伪正确样本"。也就是说：

- Critic 的工作方式符合设计（区分"结果正确"与"过程正确"），但它会在**描述与实现的表述不一致**上以 0.95 的高置信度判 flawed；
- 只要模型的文字表述有任何与代码不完全贴合的地方，即使测试全过也会落进"伪正确"桶；
- 这直接印证 P0：真实模型做对了题，仍被计为"误报"。

补充一点：真实模型的实现质量其实不错 —— 它识别出"禁止整体转整型"的约束，用 `ord(ch)-ord('0')` 逐位相加并主动处理了前导零；而 gt 标的是"遗漏最终进位"（mock 里那行被注释掉才有的缺陷）。**模型做对了，gt 却预设这题有错。**

### 真实环境暴露的两个必现故障（mock 模式下完全不可见）

| 故障 | 现象 | 处理 |
|---|---|---|
| 429 限流 + 重试无退避 | 原实现**立即重发**，连撞 3 次直接放弃；全量上百次调用必然踩中 | 已加退避：429 等 `5×n` 秒、5xx 等 `2×n`、其余指数退避 |
| Producer 超时 | 单次生成实测 **274s**，而默认 `LLM_TIMEOUT=120s` —— 不是偶发，是必然超时 | `.env` 中设为 `LLM_TIMEOUT=600`、`LLM_MAX_RETRIES=5` |

### 根因：hy4-preview 是推理模型，96% 的 token 是看不见的思考

不是网络慢，不是 openai 库的问题，是**模型在后台思考**。每次调用的 `usage` 实测：

| 请求 | 耗时 | completion tokens | 其中 reasoning | 可见输出 |
|---|---|---|---|---|
| 只回复一个字 | 6.3s | 137 | **134（97.8%）** | 1 字符 |
| Critic + `json_object` | 164.0s | 3767 | **3626（96.3%）** | 233 字符 |
| Critic 无 `json_object` | 90.7s | 1798 | **1700（94.5%）** | 182 字符 |

输出速率稳定在 **20–23 tok/s**，所以 `耗时 ≈ completion_tokens ÷ 22`。而 completion 里 96% 是 `completion_tokens_details.reasoning_tokens` —— **不返回给调用方、纯计费和耗时**。

**为什么和 openai 库无关**：SDK 和 urllib 打的是同一个 HTTP 端点，只是一个封装。实测极小请求 6.3s，其中 134 个推理 token 按 22 tok/s 算就是 6.1s —— **网络+prefill 只剩约 0.2s**。两条路径的 completion 数也几乎一样（76 vs 79）。时间全部消耗在服务端生成思考 token 上，换客户端库动不了它。

### 解法：关闭深度思考（已实施，远端后来也做了同样的事）

官方参数 `thinking.type`。**远端 `3692607` 已自带更完整的实现**（`app/llm/base.py` +219、`app/llm/hy3.py` +193、新增 `app/agents/tools.py`、`docs/hy3_thinking.md`），支持 tools 调用与 `reasoning_content` 回填，且 `HY3_THINKING` 同样默认 `disabled`。本地已同步该版本，我的独立实现已废弃、改用远端的。

### 效果：单题 2104s → 117s（18 倍）

| 项 | 关闭前 | 关闭后 |
|---|---|---|
| 单题耗时 | 2104s（35min） | **117s（2min）** |
| 50 题全量 | ≈ 29 小时 | **≈ 1.6 小时** |

顺带一个意外收获：关闭思考后，同一题的五段裁决**全部 valid（conf 0.95）**，`process_valid=True`，不再判成"伪正确"。此前那三条 flawed（"遗漏另一个字符串对应位"、"复杂度描述不一致"）在思考模式下更像**过度解读** —— 模型的五个测试全是过的。这一点只有 1 题样本，需在更大样本上确认 Critic 不会因此变得过松。

---

## 三、本次改动（6 项，均未提交）

1. **`datasets/code/medium/single_number.json`** —— 修 JSON 语法错误
   `"args": [[2, 2, 1],]` 数组尾部多余逗号。因 `app/datasets.py:21` 为 `problems = [load_problem(p) for p in files]` 且无任何容错，**这一个文件会让全量评测整体崩溃**。仅改语法，未改语义。

2. **`app/config.py`** —— 新增 `_load_dotenv()`
   原实现只 `os.getenv`，`.env` 文件不会生效。补了一个标准库实现的加载：仅在环境变量缺失时从仓库根 `.env` 补全，**不覆盖已显式设置的值**，无 `.env` 时静默跳过。已验证：`.env` 可读、环境变量优先、git 正确忽略。

3. **`app/llm/hy3.py`** —— 重试加退避
   429 等 `5×n` 秒、5xx 等 `2×n`、其余指数退避；`LLM_MAX_RETRIES` 上限内重试，超限统一抛出。

4. **`app/config.py` + `app/evaluator/pipeline.py`** —— Critic 可并发
   新增 `CRITIC_PARALLELISM`（默认 `1`，即完全保持原串行行为）。`_review_sections` 在 >1 时用 `ThreadPoolExecutor` 并发五段审查，**结果顺序与串行一致**。已用 mock 后端逐字段比对：串行 == 并发。

5. **`datasets/code/medium/single_number.json`** —— 修 5 处数组尾逗号。远端**两个版本都没修**，每次同步后都要重修（`adversarial-009` 的 `my_pow.json` 同理）。

6. **`datasets/code/adversarial/my_pow.json`**（adversarial-009）—— `language` 被嵌进 `ground_truth` 内，根对象少一个 `}`。已闭合 `ground_truth` 并把 `language` 提到顶层。

> 上述均为未提交状态（`git status` 可见）。已在远端 `3692607` 之上重新打齐。**是否提交由你决定。**

## 三之二、2026-09-11 从远端同步（0ad1e33 → 3692607）

远端 5 个新提交，方向和我们一致——关掉深度思考：

| 提交 | 说明 |
|---|---|
| `6ddf44a` | Hy3 SDK llm package init + mock |
| `8742ebb` | `app/llm/base.py` Hy3 thinking SDK |
| `321e2b8` | solver reasoning_effort + checker tools |
| `48fe137` | Producer & Checker 的 preserved/interleaved thinking |
| `3692607` | **默认关闭思考（extra_body thinking.type=disabled）** |

改动文件 13 个（+672）：`app/llm/*` 大改、`app/agents/tools.py` 新增、`checker.py` +61、`docs/hy3_thinking.md` 新增。

**逐项核对远端是否已覆盖我的修复 —— 结论是一项都没覆盖：**

| 我的修复 | 远端是否有 |
|---|---|
| 2 个 JSON 语法错误 | ❌ 仍然坏（两个文件都还是坏的） |
| `.env` 自动加载 | ❌ 没有 |
| 429/5xx 重试退避 | ❌ 没有（仍是立即重发） |
| Critic 并发 | ❌ 没有 |

所以同步后重新打上了这 4 项。同步方式：`git reset --mixed <远端>` + `git checkout -- .`，再逐项 re-apply（`pipeline.py` 远端没改，直接拷回）。

**回归验证：** mock 全量 50/50 通过，指标与同步前**完全一致**（55.0 / 75.0 / 16.7 / 100.0 / 82.0），无回归。真实单题冒烟 **92s**，五段 4 valid + 1 suspicious。

---

## 四、遗留缺口（正式跑之前建议拍板）

### P0 — `ground_truth` 绑定的是 `mock_solution`，不是题目

`app/evaluator/validation.py` 的 docstring 写明：衡量的是**评估器**准不准，不是模型解题准不准。全部指标（定位准确率、误报率、伪正确识别率）都以 `ground_truth` 为基准。

而 gt 的 `truly_correct` / `first_error_step` / `error_types` 是针对**人为构造的 `mock_solution` 那个特定缺陷**标的（如 add_strings 标"遗漏最终进位"，是因为 mock 代码里那行被注释掉了）。

mock 取消后，真实模型的解答不再是那个样本：

- 模型**做对了** → gt 说 `process_valid=false` → 被计为**误报**
- 模型**犯了别的错** → gt 的 `first_error_step` 对不上 → 被计为**定位失败**

后果是三个指标同时失真，且无法从结果里分辨"评估器不准"还是"gt 过期"。

**两个方向二选一：**

- 评**评估器** → 保留 mock，`mock_solution` 即标准缺陷样本，gt 有效
- 评**模型** → gt 需重标，或改用不依赖预置答案的指标（公开-对抗通过率差、复杂度实测、多次采样一致性）

### P1 — 解析缺口，真实模式下会造成"假失败"

实测把真实模型常见写法喂进去：

| 函数 | 输入 | 结果 |
|---|---|---|
| `extract_code` | 裸代码（无 ``` 围栏） | **空字符串** |
| `extract_code` | 缩进代码块 | **空字符串** |
| `split_sections` | `**加粗标题**` | **切出 0/5 段** |
| `split_sections` | `1. 纯序号无 #` | **切出 0/5 段** |
| `split_sections` | `## 1、顿号` / 无序号 / `## Step N:` | ✅ 正常 5/5 |

`extract_code` 拿到空串 → 全部用例 `missing_entry` → 判"代码有错"，实为解析失败；`split_sections` 切出 0 段 → 五段内容全空 → Critic 在无内容上打分。而 `**加粗**` 恰是真实模型高频写法。

### P2 — 源码取证缺失

`submodules/sandbox`（指向 `sandbox-debugger`，gitlink `11f75b6`）从未初始化，且该子模块仓库本身零提交。影响：不崩溃（有降级），但对失败用例的轨迹取证为空，报告里 `debug_hints` 的取证部分缺失。要启用需先把 sandbox 代码推到 `sandbox-debugger` 仓库。

### P3 — 已解除：样本量 22 → 50

远端 `feature/multi-agent-hy3-dataset` 在 2026-09-10 13:22–13:29 连推 10 个提交补齐全量数据集（`0ad1e33`），本地工作区已同步。核对结论：

- 文件数 **50**，编号**全部连续无缺口**（此前 22 题是同步推到一半的中间状态）
- 12 个必需字段（`function_signature` 等）**零缺失**；50 题**均有对抗测试**

**但远端带下 2 个 JSON 语法错误，`load_all()` 零容错会整体崩溃，已修：**

| 文件 | 错误 | 修法 |
|---|---|---|
| `medium/single_number.json`（medium-008） | `"args": [[2, 2, 1],]` 等 5 处数组尾逗号 | 去掉逗号，仅改语法 |
| `adversarial/my_pow.json`（adversarial-009） | `language` 字段被误放进 `ground_truth` 内部，导致根节点缺一个 `}` | 闭合 `ground_truth` 后把 `language` 提到顶层 |

修后 50 题全部加载成功、0 个 JSON 错误、mock 全量 50/50 通过。

---

## 五、全量执行方式

Key 已写入仓库根 `.env`（已被 git 忽略）。当前生效配置：

```
HY3_API_KEY=<已配置>
HY3_BASE_URL=https://tokenhub.tencentmaas.com/v1
HY3_MODEL=hy4-preview
LLM_TIMEOUT=600
LLM_MAX_RETRIES=5
# 关闭深度思考：hy4-preview 默认开启，completion 中约 96% 是不可见的 reasoning token，
# 是单题耗时 35min 的主因。改为 enabled 可恢复深度思考。
HY3_THINKING=disabled
# CRITIC_PARALLELISM=5   # 关闭思考后已足够快，一般无需再开
```

```bash
# 单题冒烟（已跑通，2104s）
python -m app.main --problem datasets/code/adversarial/add_strings.json

# 全量（未跑，受 P0 阻塞）
python -m app.main --all
```

不加 `--backend` 即走真实 Hy3。若 Key 未生效，首行会显示 `hy3(missing-key)`。

⚠️ **全量前需先定 P0**。跑本身已经很快了（50 题约 1.6 小时），但得到的指标因 gt 过期而不可信。

---

## 七、基线数据（mock 模式，50 题实测）

```
定位准确率（top-1）    : 55.0%
定位准确率（±1 容差）  : 75.0%
误报率                 : 16.7%
伪正确识别率           : 100.0%
过程判定准确率         : 82.0%
```

对比 22 题时的旧基线（top-1 60.0% / ±1 80.0% / 误报率 14.3% / 过程判定 86.4%）—— **样本从 22 扩到 50 后三项指标均下降 3–5 个百分点**。这说明此前 22 题的样本偏差确实存在，50 题这组更可信。

⚠️ 这组数是**评估器在 mock 缺陷样本上的表现**，不是模型能力。换成真实模型后，因 P0 问题，这组数的绝对值不可直接比较。

---

## 八、两处修正记录

此前判断"add_strings 的 `first_error_step` 与 gt 一致（step_4_implementation）"是**错的**。

`EvalPipeline._aggregate` 取的是 `min(flawed, key=lambda v: SECTION_TITLES.index(v.section))` —— **五段中最靠前的缺陷段**，而非 findings 的入列顺序。实测该题为 `step_3_edge_cases`，与 gt 的 `step_4_implementation` 不一致（top-1 不命中，±1 容差命中）。

同理，规则层 findings 的追加顺序（先"代码实现"后"关键边界"）不决定最终结果，任何"某段先入列所以 first_error_step 是它"的推断都不成立。

**修正二：解析缺口 P1 在本次真实冒烟中未触发。** 真实模型输出使用了 `## 1、` 形式的标题，五段被正确切分（`代码实现`/`自测说明` 都有内容即证明）。此前担心的 `**加粗**` 标题问题这次没出现，但**样本只有 1 题，不能据此认为不会触发** —— 缺口仍在，只是这一题躲过了。
