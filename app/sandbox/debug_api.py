"""兼容层：保持 ``from app.sandbox import debug_api`` 这一旧导入路径可用。

真实实现已抽到 submodule（submodules/sandbox）。本文件只做加载与符号重导出，
不含任何业务逻辑；下划线开头的内部符号不导出。
"""
from __future__ import annotations

from ._sandbox_bridge import load

_ext = load("debug_api")

globals().update({k: v for k, v in vars(_ext).items() if not k.startswith("_")})

__all__ = [k for k in vars(_ext) if not k.startswith("_")]
