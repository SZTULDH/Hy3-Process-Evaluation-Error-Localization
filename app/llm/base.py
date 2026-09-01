"""LLM 后端统一接口。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class LLMResponse:
    text: str
    raw: dict[str, Any] = field(default_factory=dict)
    model: str = ""
    # 记录调用是否走了降级/模拟，便于报告里如实标注
    simulated: bool = False


class BaseLLM:
    """所有 LLM 后端的基类。"""

    name: str = "base"

    def chat(
        self,
        messages: list[ChatMessage],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format_json: bool = False,
    ) -> LLMResponse:
        raise NotImplementedError

    def complete(self, prompt: str, **kwargs) -> LLMResponse:
        return self.chat([ChatMessage(role="user", content=prompt)], **kwargs)
