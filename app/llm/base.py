"""LLM 后端统一接口。

对齐 Hy3 慢思考 / 交错式思考 / 保留式思考：
- `reasoning_content`：响应中的思考草稿，后续请求必须原样回填
- `tool_calls`：工具调用指令
- 跨轮追问时 messages 中保留全部历史 assistant（含 reasoning_content）
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Callable


@dataclass
class ToolCallFunction:
    name: str
    arguments: str  # JSON 字符串，与 API 一致原样保留


@dataclass
class ToolCall:
    id: str
    type: str  # "function"
    function: ToolCallFunction

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "function": {
                "name": self.function.name,
                "arguments": self.function.arguments,
            },
        }


@dataclass
class ChatMessage:
    """兼容 OpenAI Chat Completions 消息；扩展 reasoning_content / tool_calls。"""

    role: str  # system | user | assistant | tool
    content: str | None = ""
    # 慢思考草稿：仅 assistant 有；回填时必须与模型输出完全一致
    reasoning_content: str | None = None
    # 工具调用（assistant）
    tool_calls: list[ToolCall] | None = None
    # 工具结果回填（role=tool）
    tool_call_id: str | None = None
    name: str | None = None  # 可选：工具名

    def to_api_dict(self) -> dict[str, Any]:
        """序列化为 Chat Completions messages 条目（含 reasoning_content 原样回填）。"""
        d: dict[str, Any] = {"role": self.role}
        if self.content is not None:
            d["content"] = self.content
        if self.reasoning_content is not None:
            # 即使为空串也回填，保持与模型生成时一致
            d["reasoning_content"] = self.reasoning_content
        if self.tool_calls:
            d["tool_calls"] = [tc.to_api_dict() for tc in self.tool_calls]
        if self.tool_call_id is not None:
            d["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            d["name"] = self.name
        return d

    @classmethod
    def from_api_message(cls, msg: dict[str, Any]) -> "ChatMessage":
        """从 API 响应 message 构造（含 reasoning_content / tool_calls）。"""
        tool_calls = None
        raw_tcs = msg.get("tool_calls") or []
        if raw_tcs:
            tool_calls = []
            for tc in raw_tcs:
                fn = tc.get("function") or {}
                tool_calls.append(
                    ToolCall(
                        id=tc.get("id", ""),
                        type=tc.get("type", "function"),
                        function=ToolCallFunction(
                            name=fn.get("name", ""),
                            arguments=fn.get("arguments", "{}")
                            if isinstance(fn.get("arguments"), str)
                            else json_dumps(fn.get("arguments") or {}),
                        ),
                    )
                )
        return cls(
            role=msg.get("role", "assistant"),
            content=msg.get("content") or "",
            reasoning_content=msg.get("reasoning_content"),
            tool_calls=tool_calls,
            tool_call_id=msg.get("tool_call_id"),
            name=msg.get("name"),
        )


def json_dumps(obj: Any) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)


@dataclass
class LLMResponse:
    text: str
    raw: dict[str, Any] = field(default_factory=dict)
    model: str = ""
    simulated: bool = False
    # Hy3 慢思考
    reasoning_content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    # 便于多轮回填：完整的 assistant 消息
    message: ChatMessage | None = None

    def assistant_message(self) -> ChatMessage:
        """构造可原样回填到 messages 的 assistant 条目。"""
        if self.message is not None:
            return self.message
        return ChatMessage(
            role="assistant",
            content=self.text or "",
            reasoning_content=self.reasoning_content,
            tool_calls=self.tool_calls or None,
        )


# 工具定义：OpenAI function calling 格式
ToolSpec = dict[str, Any]
# 业务侧执行器：name -> (arguments_dict) -> str 结果
ToolHandler = Callable[[dict[str, Any]], str]


class BaseLLM:
    """所有 LLM 后端的基类。"""

    name: str = "base"

    def chat(
        self,
        messages: list[ChatMessage],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format_json: bool = False,
        *,
        tools: list[ToolSpec] | None = None,
        tool_choice: str | dict | None = None,
        reasoning_effort: str | None = None,
        preserved_thinking: bool | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        raise NotImplementedError

    def complete(self, prompt: str, **kwargs: Any) -> LLMResponse:
        return self.chat([ChatMessage(role="user", content=prompt)], **kwargs)

    def chat_with_tools(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        handlers: dict[str, ToolHandler],
        *,
        max_rounds: int = 8,
        reasoning_effort: str | None = "high",
        preserved_thinking: bool | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format_json: bool = False,
        **kwargs: Any,
    ) -> LLMResponse:
        """交错式思考 + 工具调用循环。

        每一轮：
        1. 调用模型（带 tools）
        2. 若 finish_reason=tool_calls：执行工具，把 assistant（含 reasoning_content）
           与 role=tool 结果原样追加到 messages，再请求
        3. 直到输出最终 content 或达到 max_rounds

        保留式思考：跨用户提问轮时，调用方应把本方法返回前的完整 messages
        （含全部 reasoning_content）保留到下一轮 user 追问。
        """
        import json

        history = list(messages)
        last: LLMResponse | None = None

        for _ in range(max_rounds):
            last = self.chat(
                history,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format_json=response_format_json,
                tools=tools,
                tool_choice=kwargs.get("tool_choice", "auto"),
                reasoning_effort=reasoning_effort,
                preserved_thinking=preserved_thinking,
                **{k: v for k, v in kwargs.items() if k != "tool_choice"},
            )
            # 回填 assistant（含 reasoning_content / tool_calls）
            history.append(last.assistant_message())

            if not last.tool_calls or last.finish_reason == "stop":
                return last

            for tc in last.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                handler = handlers.get(name)
                if handler is None:
                    result = json.dumps(
                        {"error": f"unknown tool: {name}"}, ensure_ascii=False
                    )
                else:
                    try:
                        result = handler(args if isinstance(args, dict) else {})
                    except Exception as exc:  # noqa: BLE001
                        result = json.dumps(
                            {"error": str(exc)}, ensure_ascii=False
                        )
                if not isinstance(result, str):
                    result = json.dumps(result, ensure_ascii=False)
                history.append(
                    ChatMessage(
                        role="tool",
                        content=result,
                        tool_call_id=tc.id,
                        name=name,
                    )
                )

        assert last is not None
        return last
