# Hy3 Process-Level Evaluation & Error Localization

【犀牛鸟实战任务】可验证场景：过程评估与错误定位  
**Hy3 application with process-level evaluation and error localization**

基于 [Hy3](https://github.com/Tencent-Hunyuan/Hy3) 构建面向可验证场景的 AI 应用，重点实现：
- 完整解题过程生成（而非仅最终答案）
- 过程正确性判定
- 错误步骤定位
- 错误类型归类
- 结果正确但过程不成立的样本识别

## 项目状态

🚧 **当前阶段：方案设计与仓库初始化**

- [x] 仓库创建
- [ ] 方案文档提交
- [ ] 应用核心实现
- [ ] 评测题集构建
- [ ] 过程评估器实现
- [ ] 有效性验证
- [ ] 结果分析与报告
- [ ] Demo 视频/GIF

## 仓库结构（规划）

```
.
├── README.md
├── PROPOSAL.md                 # 方案文档（设计思路、架构、重点技术、时间规划）
├── docs/
│   ├── design.md               # 详细设计文档
│   ├── error_taxonomy.md       # 错误分类体系
│   └── analysis_report.md      # 分析报告
├── app/                        # 应用侧代码
│   ├── solver/                 # 基于 Hy3 的解题过程生成
│   ├── evaluator/              # 过程评估模块
│   └── ui/                     # 交互界面（Web/CLI）
├── datasets/                   # 评测题集
│   ├── math/                   # 数学题（分层）
│   ├── physics/                # 物理题（可选）
│   └── code/                   # 代码任务（可选）
├── scripts/
│   ├── answer_checker.py       # 答案自动校验脚本
│   ├── process_evaluator.py    # 过程评估脚本
│   └── validation.py           # 定位准确率 / 误报率验证
├── results/                    # 评测结果与人工抽检记录
├── demos/                      # Demo 视频 / GIF
└── requirements.txt
```

## 快速开始（规划）

```bash
# 安装依赖
pip install -r requirements.txt

# 配置 Hy3 API Key
export HY3_API_KEY=your_key

# 运行示例解题 + 过程评估
python -m app.main --problem datasets/math/easy/001.json
```

## 参考

- Hy3 仓库：https://github.com/Tencent-Hunyuan/Hy3
- 本次任务要求：过程评估与错误定位

## License

Apache-2.0（与 Hy3 保持一致）
