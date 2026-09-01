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

测试用例通过 ≠ 逻辑正确。本项目专门针对这一痛点，设计过程级评估体系，识别“伪正确”代码。

## 项目状态

✅ **当前阶段：最小可运行闭环已完成（Mock 离线模式验证通过）**

- [x] 仓库创建
- [x] 方向确认：代码任务
- [x] 方案文档完善（PROPOSAL.md + docs/design.md + error_taxonomy.md）
- [x] 应用核心实现（Solver → Sandbox → Rules → Critic → Aggregate）
- [x] 评测题集构建（含对抗测试与伪正确样本，easy / medium / hard / adversarial）
- [x] 过程评估器实现（分步 Critic + 规则硬证据融合）
- [x] 有效性验证（定位准确率 83.3%，伪正确识别率 100%，误报率 0%）
- [x] 结果分析与报告（results/latest_report.md）
- [ ] Demo 视频/GIF
- [ ] 真实 Hy3 API 大规模评测与调优
- [ ] Web UI（可选）

## 最新评测摘要（Mock 模式）

| 指标 | 数值 |
|------|------|
| 结果正确率（公开测试） | 8/8 |
| 真实正确率（公开+对抗） | 3/8 |
| 伪正确样本 | 6/8 |
| 伪正确识别率 | 100% |
| 步骤定位准确率（top-1） | 83.3% |
| 误报率 | 0% |

详细报告见 [`results/latest_report.md`](results/latest_report.md)

## 仓库结构

```
.
├── README.md
├── PROPOSAL.md                 # 方案文档
├── docs/
│   ├── design.md               # 详细设计
│   └── error_taxonomy.md       # 错误分类体系
├── app/
│   ├── solver/                 # Hy3 驱动的解题过程生成
│   ├── evaluator/              # 过程评估模块（LLM Critic + 沙盒 + 静态分析）
│   ├── sandbox/                # 代码执行沙盒
│   ├── llm/                    # 可插拔后端（Hy3 / Mock）
│   ├── reporting/              # 报告生成
│   └── main.py                 # CLI 入口
├── datasets/
│   └── code/                   # 分层代码任务题集
│       ├── easy/
│       ├── medium/
│       ├── hard/
│       └── adversarial/        # 伪正确样本
├── scripts/
├── results/                    # 评测产物（latest_report.md 已纳入版本控制）
└── requirements.txt
```

## 快速开始

### 离线 Mock 模式（无需任何 Key，推荐先跑这个）

```bash
# 无需安装依赖（仅标准库）
python -m app.main --all

# 单题评测
python -m app.main --problem datasets/code/medium/is_palindrome.json
# 或按 id
python -m app.main --id medium-002
```

### 真实 Hy3 模式

```bash
pip install -r requirements.txt   # 可选，不装也能用 urllib 直连
export HY3_API_KEY=your_key
# 可选：export HY3_BASE_URL=...  HY3_MODEL=...

python -m app.main --all --backend hy3
```

## 参考

- Hy3 仓库：https://github.com/Tencent-Hunyuan/Hy3
- 任务核心：过程评估与错误定位（代码实现逻辑正确性）

## License

Apache-2.0
