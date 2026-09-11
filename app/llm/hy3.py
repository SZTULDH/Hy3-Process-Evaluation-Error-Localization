"""Hy3 后端：OpenAI 兼容 Chat Completions。

对齐官方 SDK：
- 关闭思考：extra_body={"thinking": {"type": "disabled"}}
- 开启思考：extra_body={"thinking": {"type": "enabled"}}
  开启后用 getattr(message, "reasoning_content") 读取思考过程

默认关闭思考。开启时仍支持 tools / 交错回填 reasoning_content。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterator

from ..config import (
    HY3_API_KEY,
    HY3_BASE_URL,
    HY3_MODEL,
    HY3_REASONING_EFFORT,
    HY3_THINKING,
    LLM_MAX_RETRIES,
    LLM_TIMEOUT,
    resolve_thinking,
)
from .base import (
    BaseLLM,
    ChatMessage,
    LLMResponse,
    ToolCall,
    ToolCallFunction,
    ToolSpec,
)


def _normalize_thinking(value: Any) -> str:
    """归一化为 enabled / disabled。"""
    if value is True:
        return "enabled"
    if value is False or value is None:
        return "disabled"
    s = str(value).strip().lower()
    if s in {"enabled", "enable", "on", "true", "1", "yes"}:
        return "enabled"
    return "disabled"


# 思考强度档位词 + 旧式开关词（统一交给 resolve_thinking 按模型能力展开）
_LEVEL_WORDS = {
    "auto", "off", "none", "minimal", "low", "medium", "high", "xhigh", "max",
    "enabled", "disabled",
}


def _resolve(
    thinking: Any,
    effort: Any,
    model: str,
    default_thinking: str,
    default_effort: str,
) -> tuple[str, str]:
    """把「思考强度 or 开关」 + 推理深度 解析成请求参数。

    强度档位（off/low/medium/high/auto…）会按模型能力展开成
    `thinking.type` 与 `reasoning_effort` 两个字段；纯开关
    （enabled/disabled）只决定 thinking，推理深度沿用默认。
    """
    raw = default_thinking if thinking is None else thinking
    s = "" if raw is None else str(raw).strip().lower()
    if s in _LEVEL_WORDS:
        think, level_effort = resolve_thinking(s, model)
    elif s == "":
        think, level_effort = _normalize_thinking(default_thinking), ""
    else:
        think, level_effort = _normalize_thinking(raw), ""
    eff = default_effort if effort is None else effort
    return think, (str(eff or level_effort or "").strip().lower() or "")


@dataclass
class StreamResult:
    """流式生成的累积结果，供生成结束后读取。"""

    text: str = ""
    reasoning: str = ""
    finish_reason: str | None = None


@dataclass
class Delta:
    """一个流式增量。`kind` 为 `content`（正文）或 `reasoning`（思考）。"""

    kind: str
    text: str


class Hy3LLM(BaseLLM):
    name = "hy3"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        thinking: str | bool | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        self.api_key = api_key or HY3_API_KEY
        if not self.api_key:
            raise ValueError("缺少 HY3_API_KEY，无法初始化 Hy3 后端")
        self.base_url = (base_url or HY3_BASE_URL).rstrip("/")
        self.model = model or HY3_MODEL
        self.default_thinking, self.default_reasoning_effort = _resolve(
            thinking if thinking is not None else HY3_THINKING,
            reasoning_effort if reasoning_effort is not None else (HY3_REASONING_EFFORT or None),
            self.model,
            "disabled",
            "",
        )
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
        thinking: str,
        reasoning_effort: str | None,
        preserved_thinking: bool | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_api_dict() for m in messages],
            "stream": False,
            "thinking": {"type": thinking},
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        # 推理深度是独立字段，与 thinking 开关无关（官方文档分开列的两个参数）
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort
        if thinking == "enabled" and preserved_thinking is not None:
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
        """官方 openai SDK：thinking / reasoning 等扩展字段走 extra_body。"""
        client = self._sdk.OpenAI(
            api_key=self.api_key, base_url=self.base_url, timeout=LLM_TIMEOUT
        )
        body = dict(payload)
        extra: dict[str, Any] = {}
        for key in ("thinking", "reasoning_effort", "preserved_thinking"):
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
        extra.update(body)
        if extra:
            create_kwargs["extra_body"] = extra

        resp = client.chat.completions.create(**create_kwargs)
        if hasattr(resp, "model_dump"):
            dumped = resp.model_dump()
        else:
            dumped = json.loads(resp.model_dump_json())
        # SDK 不声明 reasoning_content，用 getattr 补进 dump
        try:
            msg = resp.choices[0].message
            reasoning = getattr(msg, "reasoning_content", None)
            if reasoning is not None:
                dumped.setdefault("choices", [{}])
                if dumped.get("choices"):
                    dumped["choices"][0].setdefault("message", {})
                    dumped["choices"][0]["message"]["reasoning_content"] = reasoning
        except Exception:  # noqa: BLE001
            pass
        return dumped

    @staticmethod
    def _retry_delay(err: Exception, attempt: int) -> float:
        """失败重试前的退避秒数。

        429（限流）与 5xx 必须等更久，否则连续重试只会继续撞墙；
        其余错误用较短的指数退避。实测全量上百次调用必然踩到 429。
        """
        code = getattr(err, "code", None)
        if code == 429:
            return 5.0 * attempt
        if isinstance(code, int) and 500 <= code < 600:
            return 2.0 * attempt
        return min(2.0**attempt, 8.0)

    @staticmethod
    def _parse_response(raw: dict[str, Any], fallback_model: str) -> LLMResponse:
        choices = raw.get("choices") or []
        if not choices:
            raise RuntimeError(f"Hy3 响应无 choices: {raw!r}")
        choice = choices[0]
        msg = choice.get("message") or {}
        content = msg.get("content") or ""
        reasoning = msg.get("reasoning_content")
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
        thinking: str | bool | None = None,
        reasoning_effort: str | None = None,
        preserved_thinking: bool | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        if "tools" in kwargs and tools is None:
            tools = kwargs.pop("tools")
        if "tool_choice" in kwargs and tool_choice is None:
            tool_choice = kwargs.pop("tool_choice")
        if "thinking" in kwargs and thinking is None:
            thinking = kwargs.pop("thinking")
        if "reasoning_effort" in kwargs and reasoning_effort is None:
            reasoning_effort = kwargs.pop("reasoning_effort")
        if "preserved_thinking" in kwargs and preserved_thinking is None:
            preserved_thinking = kwargs.pop("preserved_thinking")

        think, effort = _resolve(
            thinking,
            reasoning_effort,
            self.model,
            self.default_thinking,
            self.default_reasoning_effort or "",
        )

        payload = self._build_payload(
            messages=messages,
            temperature=0.2 if temperature is None else temperature,
            max_tokens=max_tokens,
            json_mode=response_format_json,
            tools=tools,
            tool_choice=tool_choice,
            thinking=think,
            reasoning_effort=effort,
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
                time.sleep(self._retry_delay(err, attempt))
        raise RuntimeError(f"Hy3 调用失败（重试 {LLM_MAX_RETRIES} 次）: {last_err}")

    # ------------------------------------------------------------ 流式

    def _stream_payload(
        self,
        messages: list[ChatMessage],
        temperature: float | None,
        max_tokens: int | None,
        response_format_json: bool,
        think: str,
        effort: str | None,
    ) -> dict[str, Any]:
        payload = self._build_payload(
            messages=messages,
            temperature=0.2 if temperature is None else temperature,
            max_tokens=max_tokens,
            json_mode=response_format_json,
            tools=None,
            tool_choice=None,
            thinking=think,
            reasoning_effort=effort,
            preserved_thinking=None,
        )
        payload["stream"] = True
        return payload

    def iter_stream(
        self,
        messages: list[ChatMessage],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format_json: bool = False,
        *,
        thinking: str | bool | None = None,
        reasoning_effort: str | None = None,
    ) -> tuple[Iterator[str], StreamResult]:
        """流式生成：返回 `(正文增量迭代器, 累积结果)`。只要正文。"""
        deltas, result = self.iter_deltas(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format_json=response_format_json,
            thinking=thinking,
            reasoning_effort=reasoning_effort,
        )

        def only_content() -> Iterator[str]:
            for d in deltas:
                if d.kind == "content":
                    yield d.text

        return only_content(), result

    def iter_deltas(
        self,
        messages: list[ChatMessage],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format_json: bool = False,
        *,
        thinking: str | bool | None = None,
        reasoning_effort: str | None = None,
    ) -> tuple[Iterator[Delta], StreamResult]:
        """流式生成：返回 `(全部增量迭代器, 累积结果)`。

        增量分 `content`（正文）与 `reasoning`（思考）两类。**重试只在吐出
        首个增量之前发生** —— 一旦开始输出就不能重放，否则调用方会收到重复内容。

        思考也要流式：实测开启深度思考时思考过程要跑 5 分钟以上，攒到最后
        再推会让前端白屏干等。
        """
        think, effort = _resolve(
            thinking,
            reasoning_effort,
            self.model,
            self.default_thinking,
            self.default_reasoning_effort or "",
        )
        payload = self._stream_payload(
            messages, temperature, max_tokens, response_format_json, think, effort
        )
        result = StreamResult()
        iterator = (
            self._iter_sdk(payload, result)
            if self._sdk
            else self._iter_http(payload, result)
        )
        return iterator, result

    def _iter_http(self, payload: dict[str, Any], result: StreamResult) -> Iterator[Delta]:
        url = f"{self.base_url}/chat/completions"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "text/event-stream",
        }
        for attempt in range(1, LLM_MAX_RETRIES + 1):
            started = False
            try:
                req = urllib.request.Request(url, data=data, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
                    for raw_line in resp:
                        line = raw_line.decode("utf-8", "replace").strip()
                        if not line.startswith("data:"):
                            continue
                        body = line[5:].strip()
                        if body == "[DONE]":
                            return
                        try:
                            chunk = json.loads(body)
                        except json.JSONDecodeError:
                            continue
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        ch = choices[0]
                        delta = ch.get("delta") or {}
                        reason = delta.get("reasoning_content") or ""
                        piece = delta.get("content") or ""
                        if reason:
                            result.reasoning += reason
                            started = True
                            yield Delta("reasoning", reason)
                        if piece:
                            started = True
                            result.text += piece
                            yield Delta("content", piece)
                        if ch.get("finish_reason"):
                            result.finish_reason = ch["finish_reason"]
                return
            except Exception as err:  # noqa: BLE001
                if started or attempt == LLM_MAX_RETRIES:
                    raise
                time.sleep(self._retry_delay(err, attempt))

    def _iter_sdk(self, payload: dict[str, Any], result: StreamResult) -> Iterator[str]:
        client = self._sdk.OpenAI(
            api_key=self.api_key, base_url=self.base_url, timeout=LLM_TIMEOUT
        )
        body = dict(payload)
        extra: dict[str, Any] = {}
        for key in ("thinking", "reasoning_effort", "preserved_thinking"):
            if key in body:
                extra[key] = body.pop(key)
        kwargs: dict[str, Any] = {
            "model": body.pop("model"),
            "messages": body.pop("messages"),
            "stream": True,
        }
        if "temperature" in body:
            kwargs["temperature"] = body.pop("temperature")
        if "max_tokens" in body:
            kwargs["max_tokens"] = body.pop("max_tokens")
        if "response_format" in body:
            kwargs["response_format"] = body.pop("response_format")
        extra.update(body)
        if extra:
            kwargs["extra_body"] = extra

        for attempt in range(1, LLM_MAX_RETRIES + 1):
            started = False
            try:
                for chunk in client.chat.completions.create(**kwargs):
                    choices = getattr(chunk, "choices", None) or []
                    if not choices:
                        continue
                    ch = choices[0]
                    delta = getattr(ch, "delta", None)
                    reason = getattr(delta, "reasoning_content", None) or ""
                    piece = getattr(delta, "content", None) or ""
                    if reason:
                        result.reasoning += reason
                        started = True
                        yield Delta("reasoning", reason)
                    if piece:
                        started = True
                        result.text += piece
                        yield Delta("content", piece)
                    fr = getattr(ch, "finish_reason", None)
                    if fr:
                        result.finish_reason = fr
                return
            except Exception as err:  # noqa: BLE001
                if started or attempt == LLM_MAX_RETRIES:
                    raise
                time.sleep(self._retry_delay(err, attempt))
