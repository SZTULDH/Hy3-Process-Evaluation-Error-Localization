"""LLM 后端工厂。有 Key 走 Hy3，无 Key 自动降级到离线 Mock。"""

from __future__ import annotations

from ..config import HY3_API_KEY
from .base import BaseLLM
from .hy3 import Hy3LLM
from .mock import MockLLM


def get_llm(force: str | None = None) -> BaseLLM:
    """获取 LLM 后端。

    force: "hy3" | "mock" | None（None 表示按环境变量自动选择）
    """
    if force == "mock":
        return MockLLM()
    if force == "hy3":
        return Hy3LLM()

    if HY3_API_KEY:
        try:
            return Hy3LLM()
        except Exception:
            # Key 无效时不阻塞整条流水线，降级到规则通道
            return MockLLM()
    return MockLLM()
