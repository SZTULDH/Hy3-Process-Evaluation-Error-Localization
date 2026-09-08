"""兼容层：让 ``python -m app.sandbox.mcp_server`` 仍然可用（实现在 submodule 里）。"""
from __future__ import annotations

import os

from ._sandbox_bridge import EXT_DIR, load

_ext = load("mcp_server")

globals().update({k: v for k, v in vars(_ext).items() if not k.startswith("_")})

__all__ = [k for k in vars(_ext) if not k.startswith("_")]


if __name__ == "__main__":
    import runpy

    runpy.run_path(os.path.join(EXT_DIR, "mcp_server.py"), run_name="__main__")
