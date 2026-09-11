# Hy3 Process-Level Evaluation & Error Localization

【犀牛鸟实战任务】可验证场景：过程评估与错误定位
**Hy3 application with process-level evaluation and error localization**

基于 [Hy3](https://github.com/Tencent-Hunyuan/Hy3) 构建面向**代码任务**的可验证 AI 应用，重点实现：

- 完整解题过程生成（思路 → 复杂度分析 → 边界处理 → 代码实现 → 自测说明）
- 过程正确性判定（而非仅判断测试是否通过）
- 错误步骤定位
- 错误类型归类
- **结果正确但过程不成立**的样本识别（测试通过但逻辑有缺陷、边界缺失、算法碰巧正确等）

## 核心亮点

测试用例通过 ≠ 逻辑正确。本项目专门针对这一痛点，设计过程级评估体系，识别"伪正确"代码。

## 演示

三份工作台快照，覆盖两种题目形态（算法竞赛 / 工程任务·类级）。
每份都同时提供**静态截图**与**可交互 HTML 页面** —— HTML 里保留了当时的完整 DOM，
可以逐段展开查看 Checker / Critic 每一步的真实输入与输出。

> **预览方式**：点击下方截图或「在线预览」链接，由 [htmlpreview.github.io](https://htmlpreview.github.io/)
> 在线渲染，无需下载；也可以直接把 `assets/*.html` 下载到本地用浏览器打开。

### ① 困难题「单词拆分」· 全绿通过

左侧是题库与题面（含公开 / 对抗两组测试），右侧是本次评估的完整产出：
Agent 工作流（Checker 执行取证 / Critic 分步裁决）、四个判定灯、Checker 总评、
五段逐条结论、提取到的代码，以及开启深度思考后的推理过程。
本例公开 3/3、对抗 2/2，四个判定全部成立 —— 典型的 DP 解法，过程无瑕疵。

[![困难题「单词拆分」· 全绿通过](https://github.com/SZTULDH/Hy3-Process-Evaluation-Error-Localization/blob/main/assets/1.jpeg)](https://htmlpreview.github.io/?https://github.com/SZTULDH/Hy3-Process-Evaluation-Error-Localization/blob/main/assets/1.html)

[**▶ 在线预览交互页面（HTML）**](https://htmlpreview.github.io/?https://github.com/SZTULDH/Hy3-Process-Evaluation-Error-Localization/blob/main/assets/1.html) · [下载 HTML](assets/1.html) · [下载截图](assets/1.jpeg)

### ② 困难题「第 K 个最大元素」· 伪正确识别

公开 2/2、对抗 2/2 **全部通过**，但过程成立为**否**：复杂度分析与实际实现不符、
描述与实现不一致，首个不成立步骤定位到**「复杂度分析」**。
这正是本项目要抓的核心 —— 结果对了，过程不成立；只看测试通过率完全发现不了。

[![伪正确识别 · 第 K 个最大元素](https://github.com/SZTULDH/Hy3-Process-Evaluation-Error-Localization/blob/main/assets/2.jpeg)](https://htmlpreview.github.io/?https://github.com/SZTULDH/Hy3-Process-Evaluation-Error-Localization/blob/main/assets/2.html)

[**▶ 在线预览交互页面（HTML）**](https://htmlpreview.github.io/?https://github.com/SZTULDH/Hy3-Process-Evaluation-Error-Localization/blob/main/assets/2.html) · [下载 HTML](assets/2.html) · [下载截图](assets/2.jpeg)

### ③ 工程任务（类级）·「订单状态机」

同一套链路跑**有状态题目**：交付的是类而非函数，用例在**同一实例**上按序调用。
本例公开 3/3、对抗 7/7，状态机在多次调用间的迁移与复位全部正确，五段过程成立。

[![工程任务（类级）· 订单状态机](https://github.com/SZTULDH/Hy3-Process-Evaluation-Error-Localization/blob/main/assets/3.jpeg)](https://htmlpreview.github.io/?https://github.com/SZTULDH/Hy3-Process-Evaluation-Error-Localization/blob/main/assets/3.html)

[**▶ 在线预览交互页面（HTML）**](https://htmlpreview.github.io/?https://github.com/SZTULDH/Hy3-Process-Evaluation-Error-Localization/blob/main/assets/3.html) · [下载 HTML](assets/3.html) · [下载截图](assets/3.jpeg)

## 子项目：Sandbox Debugger

本仓库通过 git submodule 引入独立能力库 [sandbox-debugger](https://github.com/SZTULDH/sandbox-debugger)（路径：`submodules/sandbox`）。

它是面向 Agent 的 **Python 源码级沙盒调试与评测执行**能力，定位为可复用的通用库，不绑定本任务本体：

- **源码级调试**：断点、单步、栈/变量、求值、轨迹回溯
- **评测执行**：函数级 / 类级测试套件（`runner` + `_harness`）

### 接入方式

1. **MCP Server（推荐）**：`python -m app.sandbox.mcp_server`
2. **调试 API**：`from app.sandbox import debug_api as D`
3. **评测 API**：`from app.sandbox.runner import run_suite, run_problem`
4. **CLI**：`python scripts/sandbox_debug.py --problem ... --run --trace 60`

更多细节见子模块 README：[`submodules/sandbox/README.md`](submodules/sandbox/README.md) 或上游仓库。

## 项目状态

✅ **当前阶段：全链路已打通，提供 Web 工作台，已接入真实 Hy3 API**

- [x] 仓库创建
- [x] 方向确认：代码任务
- [x] 方案文档完善（PROPOSAL.md + docs/design.md + error_taxonomy.md）
- [x] 应用核心实现（Solver → Sandbox → Rules → Critic → Aggregate）
- [x] 评测题集构建：**73 题**（easy 12 / medium 15 / hard 12 / adversarial 11 / engineering 23，含对抗测试、
      类级有状态题目与伪正确样本）
- [x] 过程评估器实现（分步 Critic + 规则硬证据融合）
- [x] 有效性验证（伪正确识别率 100%，步骤定位 top-1 83.3%）
- [x] 结果分析与报告（results/latest_report.md）
- [x] 真实 Hy3 API 接通（含 429 退避重试、流式、思考强度可配）
- [x] **Web 工作台**（`webapp/`：SSE 流式、模型/思考强度可选、Agent 工作流可视化、题库懒加载）
- [x] **Agent 工作流可观测**（Checker 取证与 Critic 裁决的真实输入/输出逐步可查）
- [x] **Demo 视频**（见上方「演示」）
- [x] **真实 Hy3 API 大规模评测**（`hy4-preview` 全量 73 题，报告见 `results/final_report.md`）

## 最新评测摘要（真机 `hy4-preview`，全量 73 题）

| 指标 | 数值 | 说明 |
|------|------|------|
| 答案准确率（公开测试全过） | **97.3%**（71/73） | 传统意义上的"做对了" |
| 过程正确率 | **68.5%**（50/73） | 五段推理链均成立 |
| 伪正确样本 | **28.8%**（21/73） | 公开测试过了但逻辑不成立 |
| 过程成立率：easy / medium / hard | **100% / 60% / 33.3%** | 难度分层的断崖 |
| 单题平均耗时 | 55s | 思考强度关闭 |

**核心结论**：公开测试通过率 97.3%，过程正确率却只有 68.5% —— 差出来的 28.8% 正是
"测试通过但逻辑不成立"的伪正确区间。只看测试通过率会系统性高估模型能力。

过程成立率的断崖出现在 **medium**（100% → 60%），最低点在 **hard**（33.3%）：
模型在简单题上毫无破绽，一到中等就开始"结果对、过程不对"。

错误类型分布以**复杂度分析错误**（26.0%）为首，且其中 20 次由规则层静态分析独立命中，
不依赖 LLM —— 这是全套信号里最可靠的一类。

完整报告见 [`results/final_report.md`](results/final_report.md)，
逐题原始记录在 [`results/run_20260911_222554/`](results/run_20260911_222554/)。
离线 Mock 模式的 8 题闭环样本见 [`results/latest_report.md`](results/latest_report.md)。

## 仓库结构

```
.
├── README.md
├── PROPOSAL.md                 # 方案文档
├── docs/
│   ├── design.md               # 详细设计
│   ├── error_taxonomy.md       # 错误分类体系
│   ├── dataset.md              # 题集说明
│   ├── webui.md                # Web 工作台使用与接口文档
│   ├── run_readiness.md        # 环境就绪 / 接入真实 API 记录
│   └── hy3_thinking.md         # 深度思考参数说明
├── app/
│   ├── solver/                 # Hy3 驱动的解题过程生成
│   ├── evaluator/              # 过程评估模块（LLM Critic + 沙盒 + 静态分析）
│   ├── agents/                 # Producer / Checker（执行取证）
│   ├── sandbox/                # 代码执行沙盒
│   ├── llm/                    # 可插拔后端（Hy3 / Mock）
│   ├── reporting/              # 报告生成
│   ├── config.py               # 配置与模型目录、思考强度解析
│   └── main.py                 # CLI 入口
├── webapp/                     # Web 工作台（零第三方依赖，http.server + SSE）
│   ├── server.py
│   └── index.html
├── datasets/
│   └── code/                   # 分层代码任务题集（共 73 题）
│       ├── easy/               # 12
│       ├── medium/             # 15
│       ├── hard/               # 12
│       ├── adversarial/        # 11（伪正确样本）
│       └── engineering/        # 23（含类级有状态题目）
├── submodules/
│   └── sandbox/                # git submodule → sandbox-debugger（源码级调试 + 评测执行）
├── scripts/
├── results/                    # 评测产物（latest_report.md / final_report.md 已纳入版本控制）
└── requirements.txt
```

## 环境要求

- **Python ≥ 3.9**（所有模块启用 `from __future__ import annotations`，实测环境 3.13）
- **零必需依赖**：核心链路、多 Agent 编排与 Web 工作台只用标准库，克隆即可跑
- `openai` 为**可选**：不装时走标准库 `urllib` 直连；装了优先用官方 SDK

## 快速开始

### 1. Web 工作台（推荐，可视化跑单题）

```bash
cp .env.example .env        # 填入 HY3_API_KEY
python webapp/server.py 8787
# 打开 http://127.0.0.1:8787
```

零第三方依赖。界面支持：

| 能力 | 说明 |
|---|---|
| 两种模式 | 算法竞赛（思路/复杂度/边界）、代码任务（实现逻辑而非仅测试通过） |
| 模型与思考强度 | 24 个官方模型可选 + 手填自定义；思考强度 默认/关闭/低/中/高 |
| 全链路流式 | 解答逐字、执行结果算完即推、每段审查算完即推 |
| Agent 工作流 | Checker 取证与 Critic 裁决每一步的真实输入/输出可展开 |
| 题库懒加载 | 先取索引再按需取题面，不一次性载入全部题目 |
| 候选解答 | 留空由模型现生成；粘贴自己的解答则评估它（可跳过生成直接审） |

详见 [`docs/webui.md`](docs/webui.md)。

### 2. 离线 Mock 模式（无需任何 Key，推荐先跑这个）

```bash
# 无需安装依赖（仅标准库）。注意默认后端是 hy3，跑 Mock 必须显式指定
python -m app.main --all --backend mock

# 单题评测
python -m app.main --problem datasets/code/medium/is_palindrome.json --backend mock
# 或按 id
python -m app.main --id medium-002 --backend mock
```

> 未配置 Key 且未指定 `--backend mock` 时会直接报错退出，不会静默降级成 Mock —— 避免"假跑通"。

### 3. 真实 Hy3 模式（CLI）

```bash
pip install -r requirements.txt   # 可选，不装也能用 urllib 直连
cp .env.example .env              # 填入 HY3_API_KEY；也可直接 export

python -m app.main --all --backend hy3
```

### 配置

复制 `.env.example` 为 `.env`，或直接设环境变量。主要项：

| 变量 | 默认 | 说明 |
|---|---|---|
| `HY3_API_KEY` | — | 必填；`OPENAI_API_KEY` 亦可作别名 |
| `HY3_BASE_URL` | `https://api.hunyuan.cloud.tencent.com/v1` | OpenAI 兼容端点 |
| `HY3_MODEL` | `hy3` | 也支持 `hy4-preview`、`glm-*`、`kimi-*`、`deepseek-*` 等 |
| `HY3_THINKING` | `off` | `auto` / `off` / `low` / `medium` / `high`（`disabled`/`enabled` 仍兼容） |
| `LLM_TIMEOUT` | `600` | 单轮超时秒数，开深度思考时建议 ≥ 600 |
| `LLM_MAX_RETRIES` | `5` | 429 / 5xx 指数退避重试 |
| `CRITIC_PARALLELISM` | `5` | Critic 五段并发数 |

**思考强度**按官方《深度思考》文档实现：`thinking` 与 `reasoning_effort` 是两个独立参数，
界面上的一档会由 `app/config.py::resolve_thinking()` 按模型能力展开：

| 选择 | 实际下发 |
|---|---|
| 默认 | 不下发 `thinking`，跟随模型默认 |
| 关闭（最快） | `thinking.type=disabled` |
| 低 / 中 / 高 | `thinking.type=enabled` + `reasoning_effort=low/medium/high` |

模型能力差异自动处理：**关不掉思考的模型**（GLM-5.3 系、Kimi-K2.7-Code 系、MiniMax-M2 系等）
选"关闭"会降级到最低推理深度而不是硬发 `disabled` 报错；档位不全的（如 `hy4-preview` 只有
none/high）会就近收敛。实测单题耗时：思考关闭约 100 秒，开启约 35 分钟——批量跑建议关闭。

## 评估链路

```
题面 ──► Solver/Producer ──► 五段解题过程
                                │
        ┌───────────────────────┴───────────────────────┐
        ▼                                               ▼
   Checker（硬证据）                              Critic（LLM 裁决）
   解析抽取代码 → 执行公开测试                    每段独立调用，并发
   → 执行对抗测试 → 规则静态分析                  规则信号 + LLM 融合
   → 失败用例轨迹取证                             输出 裁决/置信度/错误类型/依据
        └───────────────────────┬───────────────────────┘
                                ▼
                     Aggregate → 首个错误步骤 + 错误类型 + 报告
```

两个 Agent 的每一步都可通过 `trace=` / `on_step=` 回调观测，Web 界面上直接展开查看，
不改变任何业务逻辑。

## 参考

- Hy3 仓库：https://github.com/Tencent-Hunyuan/Hy3
- 子项目 sandbox-debugger：https://github.com/SZTULDH/sandbox-debugger
- 任务核心：过程评估与错误定位（代码实现逻辑正确性）

## License

Apache-2.0
