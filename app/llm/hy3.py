"""Hy3 后端：OpenAI 兼容 Chat Completions 接口。

仅依赖标准库（urllib），避免为跑通最小闭环引入额外安装负担。
若环境中已安装官方 `openai` SDK，则优先使用它。
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
    LLM_MAX_RETRIES,
    LLM_TIMEOUT,
)
from .base import BaseLLM, ChatMessage, LLMResponse


class Hy3LLM(BaseLLM):
    name = "hy3"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self.api_key = api_key or HY3_API_KEY
        if not self.api_key:
            raise ValueError("缺少 HY3_API_KEY，无法初始化 Hy3 后端")
        self.base_url = (base_url or HY3_BASE_URL).rstrip("/")
        self.model = model or HY3_MODEL
        self._sdk = self._try_load_sdk()

    # ------------------------------------------------------------ 内部

    @staticmethod
    def _try_load_sdk():
        try:
            import openai  # type: ignore

            return openai
        except Exception:
            return None

    def _payload(
        self,
        messages: list[ChatMessage],
        temperature: float,
        max_tokens: int | None,
        json_mode: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _call_http(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        data = json.dumps(payload).encode("utf-8")
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
        client = self._sdk.OpenAI(
            api_key=self.api_key, base_url=self.base_url, timeout=LLM_TIMEOUT
        )
        resp = client.chat.completions.create(**payload)
        return json.loads(resp.model_dump_json())

    # ------------------------------------------------------------ 对外

    def chat(
        self,
        messages: list[ChatMessage],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format_json: bool = False,
    ) -> LLMResponse:
        payload = self._payload(
            messages,
            temperature if temperature is not None else 0.2,
            max_tokens,
            response_format_json,
        )

        last_err: Exception | None = None
        for attempt in range(1, LLM_MAX_RETRIES + 1):
            try:
                raw = (
                    self._call_sdk(payload) if self._sdk else self._call_http(payload)
                )
                text = raw["choices"][0]["message"]["content"] or ""
                return LLMResponse(
                    text=text, raw=raw, model=raw.get("model", self.model)
                )
            except Exception as err:  # noqa: BLE001 - 重试后统一抛出
                last_err = err
                if attempt == LLM_MAX_RETRIES:
                    break
        raise RuntimeError(f"Hy3 调用失败（重试 {LLM_MAX_RETRIES} 次）: {last_err}")
