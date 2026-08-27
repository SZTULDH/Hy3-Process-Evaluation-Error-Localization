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

🚧 **当前阶段：方案设计与仓库初始化（方向已确认为代码任务）**

- [x] 仓库创建
- [x] 方向确认：代码任务
- [ ] 方案文档完善
- [ ] 应用核心实现
- [ ] 评测题集构建（含对抗测试与伪正确样本）
- [ ] 过程评估器实现
- [ ] 有效性验证
- [ ] 结果分析与报告
- [ ] Demo 视频/GIF

## 仓库结构（规划）

```
.
├── README.md
├── PROPOSAL.md                 # 方案文档
├── docs/
│   ├── design.md               # 详细设计
│   ├── error_taxonomy.md       # 错误分类体系
│   └── analysis_report.md      # 分析报告
├── app/
│   ├── solver/                 # Hy3 驱动的解题过程生成
│   ├── evaluator/              # 过程评估模块（LLM Critic + 沙盒 + 静态分析）
│   ├── sandbox/                # 代码执行沙盒
│   └── ui/                     # CLI / Web 界面
├── datasets/
│   └── code/                   # 分层代码任务题集
│       ├── easy/
│       ├── medium/
│       ├── hard/
│       └── adversarial/        # 伪正确样本（测试通过但逻辑有问题）
├── scripts/
│   ├── run_solver.py
│   ├── process_evaluator.py
│   ├── answer_checker.py       # 测试执行 + 额外对抗测试
│   └── validation.py           # 定位准确率 / 误报率验证
├── results/
├── demos/
└── requirements.txt
```

## 快速开始（规划）

```bash
pip install -r requirements.txt
export HY3_API_KEY=your_key

# 单题解题 + 过程评估
python -m app.main --problem datasets/code/medium/001.json
```

## 参考

- Hy3 仓库：https://github.com/Tencent-Hunyuan/Hy3
- 任务核心：过程评估与错误定位（代码实现逻辑正确性）

## License

Apache-2.0
