# 评测题集说明

## 规模与分层

| 难度 | 题数 | 依据 |
|------|------|------|
| easy | 12 | 单循环/内置操作可解，边界简单 |
| medium | 15 | 需哈希/双指针/排序/位运算等常规技巧 |
| hard | 12 | DP/图/堆等，期望较优复杂度 |
| adversarial | 11 | 专为伪正确或过程缺陷设计 |

合计 **50** 题。每题均已标注 `ground_truth` 与完整 `mock_solution`（五段过程）。

## 来源与构造

- 题型改编自常见算法面试 / LeetCode 风格，用例与伪正确解为自构。
- 正确对照样本：过程成立，公开+对抗均过。
- 过程不成立但结果可对：暴力却声称 O(n) 等。
- 伪正确样本：公开测过、对抗暴露缺陷。

## 编号约定

- easy-001 … easy-012
- medium-001 … medium-015
- hard-001 … hard-012
- adversarial-001 … adversarial-011

## 自动校验

- 公开测试 → result_correct
- 对抗测试 → truly_correct
- 过程评估 → process_valid / first_error_step

## 扩展说明

由 17 题扩至 50 题：新增 easy×7、medium×10、hard×8、adversarial×8。
