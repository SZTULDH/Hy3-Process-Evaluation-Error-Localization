# Hy3 慢思考 / 交错式思考 / 保留式思考 接入说明

本仓库 LLM 客户端（`app/llm/hy3.py`）已按腾讯混元 Hy3 SDK 改造。

## 参数

| 参数 | 说明 | 默认 |
|------|------|------|
| `reasoning_effort` | `no_think` / `low` / `high` | `high`（环境变量 `HY3_REASONING_EFFORT`） |
| `preserved_thinking` | 跨 user 轮保留思考草稿 | 携带 `tools` 时平台默认开启；可显式传 `true` |
| `tools` / `tool_choice` | function calling | Checker 可选工具见 `app/agents/tools.py` |

## 回填规则（必须遵守）

1. 响应中的 `reasoning_content` **原样**放回对应 `assistant` 消息，禁止改写/截断。
2. 工具轮：`assistant`（含 `reasoning_content` + `tool_calls`）+ `role=tool` 结果一并加入 `messages`。
3. **保留式思考**：用户下一轮追问时，历史所有 assistant 的 `reasoning_content` 仍保留在 `messages` 中。

## 本仓库中的 Agent

| Agent | 调用方式 | 工具 |
|-------|----------|------|
| **Producer**（Solver） | `chat(..., reasoning_effort="high")` 单轮生成五段解 | 无（纯生成） |
| **Checker** | 默认：沙盒 + 规则 + `chat(reasoning_effort="high")` 总评；可选 `diagnose_with_tools()` 走 `chat_with_tools` | `run_public_tests` / `run_adversarial_tests` / `run_forensics` |
| **Critic** | 分步 `chat(..., reasoning_effort="high", response_format_json=True)` | 无（信号由规则层注入 prompt） |

## SDK 示例（与官方一致）

```python
from openai import OpenAI
client = OpenAI(api_key=..., base_url=...)
resp = client.chat.completions.create(
    model="hy3",
    messages=messages,
    tools=tools,
    tool_choice="auto",
    extra_body={"reasoning_effort": "high"},  # 扩展字段经 extra_body
)
msg = resp.choices[0].message
# 回填时带上 msg.reasoning_content 与 tool_calls
```

仓库封装见 `Hy3LLM.chat` / `Hy3LLM.chat_with_tools`（`app/llm/base.py`）。
