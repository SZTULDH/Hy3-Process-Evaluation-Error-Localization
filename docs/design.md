# 详细设计

## 1. 整体链路

```
题目 JSON
   │
   ├─► [Solver]          生成五段式结构化解题过程
   │        │
   │        ▼
   ├─► [Splitter]        按标题切成 5 个段落，抽出代码块
   │        │
   │        ▼
   ├─► [Answer Checker]  沙盒执行：公开测试 + 对抗测试（双轨）
   │        │
   │        ▼
   ├─► [Rules]           执行信号 + AST 静态分析 → 硬证据
   │        │
   │        ▼
   ├─► [Critic]          逐段 LLM 审查，与硬证据融合
   │        │
   │        ▼
   └─► [Aggregate]       双通道裁决 + 首个错误步骤 + 错误类型
            │
            ▼
      JSONL 明细 + Markdown 报告 + 有效性指标
```

## 2. 两条解耦的通道

传统评测只有一个信号：测试通过与否。本项目显式拆成两条：

| 通道 | 定义 | 含义 |
| --- | --- | --- |
| **结果正确性** | 公开测试全部通过 | 传统意义上的"做对了" |
| **真实正确性** | 公开 + 对抗测试均通过 | 逻辑层面站得住 |
| **过程正确性** | 五个步骤均未被判 `flawed` | 推理链条成立 |

三者组合产生本项目最关心的判定：

```
伪正确 = 结果正确 AND (NOT 真实正确 OR NOT 过程正确)
```

即：**公开测试过了，但对不过对抗测试，或者推理过程本身有洞**。

## 3. 沙盒设计

* 一个测试套件一个子进程（进程创建是 Windows 上的主要开销，逐用例起进程太慢）
* 靠**增量落盘**保住逐用例的归因能力：每跑完一个用例写一次结果文件，
  并预先写入即将执行的用例下标
* 子进程内还有一层**看门狗线程**，按单用例 deadline 掐断并 `os._exit(2)`。
  没有它的话，排在后面的用例会"继承"前面省下的时间，单用例 timeout 形同虚设
* 结果写入文件而非 stdout：`python -I` 会忽略 `PYTHONIOENCODING`，
  子进程 stdout 在中文 Windows 上退化成本地代码页，父进程按 UTF-8 解码必然乱码
* 尽力而为地禁用 `open` / `exec` / `eval` 与一批危险模块导入——
  **这不是安全边界**，处理不可信代码仍应使用容器

结果比较刻意严格：`True` 不等于 `1`，避免类型混淆被误判为通过；
浮点走相对+绝对容差；`list` 与 `tuple` 视为同构序列。

## 4. 多信号融合

规则信号来自真实执行，是硬证据；LLM 审查是软判断。融合策略：

| 规则 | LLM | 结果 | 标记 |
| --- | --- | --- | --- |
| flawed | flawed | flawed（置信度取大） | `agree` |
| flawed | valid | **flawed**（硬证据优先） | `rule_only` |
| — | flawed | flawed | `llm_only` |
| — | suspicious | suspicious | `llm_only` |
| — | valid | valid | `agree` |

冲突时以硬证据为准，但如实记录 `signal_agreement`，便于事后分析误报来源。

## 5. LLM 后端可插拔

```
HY3_API_KEY 存在 → Hy3LLM（OpenAI 兼容端点）
否则            → MockLLM（离线，仅启用规则通道）
```

MockLLM 不是随机文本生成器：

* Solver 角色返回题集预置的解答（多为精心构造的伪正确样本）
* Critic 角色把提示词里 `<signals>` 块中的规则信号翻译成结构化裁决

因此 Mock 模式等价于"只开规则通道"，结果可解释、可复现，
在没有 API Key 的环境下也能完整演示链路与核心结论。

## 6. 目录说明

```
app/
├── config.py            全局配置与路径
├── datasets.py          题集加载
├── main.py              CLI 入口
├── llm/                 可插拔后端（base / hy3 / mock / factory）
├── solver/              Solver Agent 与提示词
├── sandbox/             沙盒 runner + 子进程 harness
├── evaluator/           splitter / rules / critic / taxonomy / pipeline / validation
└── reporting/           JSONL + Markdown 报告
datasets/code/           easy / medium / hard / adversarial 分层题集
scripts/                 各阶段独立入口 + 性能用例注入
results/                 评测产物
docs/                    design / error_taxonomy / analysis_report
```
