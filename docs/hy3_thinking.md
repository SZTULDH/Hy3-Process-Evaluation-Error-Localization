# Hy3 思考开关接入说明

默认**关闭思考**，与官方 SDK 一致：

```python
from openai import OpenAI
client = OpenAI(api_key="...", base_url="https://tokenhub.tencentmaas.com/v1")
response = client.chat.completions.create(
    model="hy3",
    messages=[{"role": "user", "content": "..."}],
    extra_body={"thinking": {"type": "disabled"}},
)
print(response.choices[0].message.content)
```

开启思考时：

```python
response = client.chat.completions.create(
    model="hy3",
    messages=[{"role": "user", "content": "..."}],
    extra_body={"thinking": {"type": "enabled"}},
)
msg = response.choices[0].message
print("思考过程:", getattr(msg, "reasoning_content", None))
print("最终回答:", msg.content)
```

仓库封装：`Hy3LLM.chat(..., thinking="disabled"|"enabled")`。  
环境变量 `HY3_THINKING` 默认 `disabled`。Producer / Checker / Critic 均显式传 `thinking="disabled"`。
