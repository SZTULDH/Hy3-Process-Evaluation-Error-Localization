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

## 项目状态

✅ **当前阶段：全链路已打通，提供 Web 工作台，已接入真实 Hy3 API**

- [x] 仓库创建
- [x] 方向确认：代码任务
- [x] 方案文档完善（PROPOSAL.md + docs/design.md + error_taxonomy.md）
- [x] 应用核心实现（Solver → Sandbox → Rules → Critic → Aggregate）
- [x] 评测题集构建：**50 题**（easy 12 / medium 15 / hard 12 / adversarial 11，含对抗测试与伪正确样本）
- [x] 过程评估器实现（分步 Critic + 规则硬证据融合）
- [x] 有效性验证（伪正确识别率 100%，步骤定位 top-1 83.3%）
- [x] 结果分析与报告（results/latest_report.md）
- [x] 真实 Hy3 API 接通（含 429 退避重试、流式、思考强度可配）
- [x] **Web 工作台**（`webapp/`：SSE 流式、模型/思考强度可选、Agent 工作流可视化、题库懒加载）
- [x] **Agent 工作流可观测**（Checker 取证与 Critic 裁决的真实输入/输出逐步可查）
- [ ] Demo 视频/GIF
- [ ] 真实 Hy3 API 大规模评测与调优（50 题全量跑批）

## 最新评测摘要（Mock 模式，8 题闭环样本）

| 指标 | 数值 |
|------|------|
| 结果正确率（公开测试） | 8/8 |
| 真实正确率（公开+对抗） | 3/8 |
| 伪正确样本 | 6/8 |
| 伪正确识别率 | 100% |
| 步骤定位准确率（top-1） | 83.3% |
| 误报率 | 0% |

> 上表来自 `results/latest_report.md`，是 Mock 离线模式下 8 题的闭环验证。
> 50 题全量基线（Mock）另测得：步骤定位 55%、±1 容差 75%、误报 16.7%、伪正确识别 100%、过程判定 82%。
> 真实模型上的系统性评测仍在进行中。

详细报告见 [`results/latest_report.md`](results/latest_report.md)

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
│   └── code/                   # 分层代码任务题集（共 50 题）
│       ├── easy/               # 12
│       ├── medium/             # 15
│       ├── hard/               # 12
│       └── adversarial/        # 11（伪正确样本）
├── scripts/
├── results/                    # 评测产物（latest_report.md 已纳入版本控制）
└── requirements.txt
```

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
| 题库懒加载 | 先取索引再按需取题面，不一次性载入 50 题 |
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
- 任务核心：过程评估与错误定位（代码实现逻辑正确性）

## License

Apache-2.0
