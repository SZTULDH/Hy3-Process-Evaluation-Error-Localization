"""Hy3 后端：OpenAI 兼容 Chat Completions。

对齐官方文档：
- 慢思考：`reasoning_effort` = no_think | low | high（推荐 high）
- 交错式思考：同一 user 轮内，tool 结果回填时原样带回 `reasoning_content`
- 保留式思考：跨 user 轮时 messages 中保留全部历史 assistant 的 `reasoning_content`
- 携带 `tools` 时平台默认开启 preserved_thinking；可用 `preserved_thinking` 显式控制

SDK 路径：非标字段经 `extra_body` 透传（openai 官方 SDK 类型签名较严）。
HTTP 路径：字段直接写入 JSON body。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from ..config import (
    HY3_API_KEY,
    HY3_BASE_URL,
    HY3_MODEL,
    HY3_REASONING_EFFORT,
    LLM_MAX_RETRIES,
    LLM_TIMEOUT,
)
from .base import (
    BaseLLM,
    ChatMessage,
    LLMResponse,
    ToolCall,
    ToolCallFunction,
    ToolSpec,
)


class Hy3LLM(BaseLLM):
    name = "hy3"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        self.api_key = api_key or HY3_API_KEY
        if not self.api_key:
            raise ValueError("缺少 HY3_API_KEY，无法初始化 Hy3 后端")
        self.base_url = (base_url or HY3_BASE_URL).rstrip("/")
        self.model = model or HY3_MODEL
        self.default_reasoning_effort = reasoning_effort or HY3_REASONING_EFFORT
        self._sdk = self._try_load_sdk()

    # ------------------------------------------------------------ 内部

    @staticmethod
    def _try_load_sdk():
        try:
            import openai  # type: ignore

            return openai
        except Exception:
            return None

    def _build_payload(
        self,
        messages: list[ChatMessage],
        temperature: float,
        max_tokens: int | None,
        json_mode: bool,
        tools: list[ToolSpec] | None,
        tool_choice: str | dict | None,
        reasoning_effort: str | None,
        preserved_thinking: bool | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_api_dict() for m in messages],
            "stream": False,
        }
        # temperature：慢思考场景下官方示例常省略；保留可配
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        # 慢思考 / 保留式思考
        effort = reasoning_effort if reasoning_effort is not None else self.default_reasoning_effort
        if effort:
            payload["reasoning_effort"] = effort
        if preserved_thinking is not None:
            payload["preserved_thinking"] = preserved_thinking
        return payload

    def _call_http(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _call_sdk(self, payload: dict[str, Any]) -> dict[str, Any]:
        """官方 openai SDK：标准字段走 kwargs，Hy3 扩展字段走 extra_body。"""
        client = self._sdk.OpenAI(
            api_key=self.api_key, base_url=self.base_url, timeout=LLM_TIMEOUT
        )
        # 从 payload 拆出可能不被 SDK 类型识别的字段
        body = dict(payload)
        extra: dict[str, Any] = {}
        for key in ("reasoning_effort", "preserved_thinking"):
            if key in body:
                extra[key] = body.pop(key)

        create_kwargs: dict[str, Any] = {
            "model": body.pop("model"),
            "messages": body.pop("messages"),
            "stream": body.pop("stream", False),
        }
        if "temperature" in body:
            create_kwargs["temperature"] = body.pop("temperature")
        if "max_tokens" in body:
            create_kwargs["max_tokens"] = body.pop("max_tokens")
        if "tools" in body:
            create_kwargs["tools"] = body.pop("tools")
        if "tool_choice" in body:
            create_kwargs["tool_choice"] = body.pop("tool_choice")
        if "response_format" in body:
            create_kwargs["response_format"] = body.pop("response_format")
        # 剩余一并塞进 extra_body
        extra.update(body)
        if extra:
            create_kwargs["extra_body"] = extra

        resp = client.chat.completions.create(**create_kwargs)
        # model_dump 保证 reasoning_content 等扩展字段保留
        if hasattr(resp, "model_dump"):
            return resp.model_dump()
        return json.loads(resp.model_dump_json())

    @staticmethod
    def _parse_response(raw: dict[str, Any], fallback_model: str) -> LLMResponse:
        choices = raw.get("choices") or []
        if not choices:
            raise RuntimeError(f"Hy3 响应无 choices: {raw!r}")
        choice = choices[0]
        msg = choice.get("message") or {}
        content = msg.get("content") or ""
        reasoning = msg.get("reasoning_content")
        # 部分 SDK 把扩展字段放在 model_extra
        if reasoning is None and isinstance(msg.get("model_extra"), dict):
            reasoning = msg["model_extra"].get("reasoning_content")

        tool_calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args = fn.get("arguments", "{}")
            if not isinstance(args, str):
                args = json.dumps(args, ensure_ascii=False)
            tool_calls.append(
                ToolCall(
                    id=tc.get("id", ""),
                    type=tc.get("type", "function"),
                    function=ToolCallFunction(
                        name=fn.get("name", ""),
                        arguments=args,
                    ),
                )
            )

        assistant = ChatMessage.from_api_message(msg)
        return LLMResponse(
            text=content,
            raw=raw,
            model=raw.get("model", fallback_model),
            reasoning_content=reasoning,
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason"),
            message=assistant,
        )

    # ------------------------------------------------------------ 对外

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
        # 允许 kwargs 透传同名参数
        if "tools" in kwargs and tools is None:
            tools = kwargs.pop("tools")
        if "tool_choice" in kwargs and tool_choice is None:
            tool_choice = kwargs.pop("tool_choice")
        if "reasoning_effort" in kwargs and reasoning_effort is None:
            reasoning_effort = kwargs.pop("reasoning_effort")
        if "preserved_thinking" in kwargs and preserved_thinking is None:
            preserved_thinking = kwargs.pop("preserved_thinking")

        payload = self._build_payload(
            messages=messages,
            temperature=0.2 if temperature is None else temperature,
            max_tokens=max_tokens,
            json_mode=response_format_json,
            tools=tools,
            tool_choice=tool_choice,
            reasoning_effort=reasoning_effort,
            preserved_thinking=preserved_thinking,
        )

        last_err: Exception | None = None
        for attempt in range(1, LLM_MAX_RETRIES + 1):
            try:
                raw = (
                    self._call_sdk(payload) if self._sdk else self._call_http(payload)
                )
                return self._parse_response(raw, self.model)
            except Exception as err:  # noqa: BLE001
                last_err = err
                if attempt == LLM_MAX_RETRIES:
                    break
        raise RuntimeError(f"Hy3 调用失败（重试 {LLM_MAX_RETRIES} 次）: {last_err}")
