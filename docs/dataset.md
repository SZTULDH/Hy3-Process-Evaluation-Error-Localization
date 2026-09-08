# 评测题集说明

## 规模与分层

| 难度 | 题数 | 依据 |
|------|------|------|
| easy | 12 | 单循环/内置操作可解，边界简单 |
| medium | 15 | 需哈希/双指针/排序/位运算等常规技巧 |
| hard | 12 | DP/图/堆等，期望较优复杂度 |
| adversarial | 11 | 专为伪正确或过程缺陷设计：公开测可过、对抗或复杂度审查暴露问题 |

合计 **50** 题。每题均已标注 `ground_truth`（结果正确性、过程是否成立、首错步骤、错误类型）与完整 `mock_solution`（五段过程）。

## 来源与构造

- 题型改编自常见算法面试 / LeetCode 风格，**非直接照抄原题编号**；用例与伪正确解为自构。
- **正确对照样本**（easy/medium/hard 多数）：`mock_solution` 逻辑成立，公开+对抗均过，用于测误报率。
- **过程不成立但结果可对**：如暴力却声称 O(n)、用字符串违反约束、额外空间却声称 O(1)。
- **伪正确样本**（adversarial）：公开用例刻意避开缺陷；`adversarial_tests` 专打边界/进位/端点等漏洞。
- 每题含 `ground_truth`：`first_error_step`、`error_types`、`truly_correct` 等，供评估器有效性验证。

## 编号约定

- `easy-001` … `easy-012`
- `medium-001` … `medium-015`
- `hard-001` … `hard-012`
- `adversarial-001` … `adversarial-011`

## 自动校验

- 公开测试 → 结果正确性（`result_correct`）
- 对抗测试 → 真实正确性（`truly_correct`）
- 过程评估 → 规则 + Critic + Checker 取证（`process_valid` / `first_error_step`）

## 扩展说明（本批）

由 17 题扩至 50 题：新增 easy×7、medium×10、hard×8、adversarial×8。标注在编写时同步完成，并与 mock 代码对测试用例做了一致性抽检。
