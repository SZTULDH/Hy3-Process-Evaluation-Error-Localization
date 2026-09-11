"""LLM 后端工厂。

默认使用真实 Hy3；仅在显式 force="mock" 时使用离线 Mock。
无 Key 时直接报错，避免静默落到 Mock 造成“假跑通”。
"""

from __future__ import annotations

from ..config import HY3_API_KEY
from .base import BaseLLM
from .hy3 import Hy3LLM
from .mock import MockLLM


def get_llm(force: str | None = None) -> BaseLLM:
    """获取 LLM 后端。

    force: "hy3" | "mock" | None
      - None / "hy3": 必须配置 HY3_API_KEY（或 OPENAI_API_KEY），否则抛错
      - "mock": 离线 Mock（仅本地联调 / 无 Key 验证规则通道时使用）
    """
    if force == "mock":
        return MockLLM()

    if not HY3_API_KEY:
        raise RuntimeError(
            "未设置 HY3_API_KEY（或 OPENAI_API_KEY）。\n"
            "真实评测请先 export HY3_API_KEY=...\n"
            "若仅需离线规则通道联调，请显式传入 --backend mock"
        )
    return Hy3LLM()
