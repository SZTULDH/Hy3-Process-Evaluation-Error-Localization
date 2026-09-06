"""把沙盒调试能力暴露成标准 MCP Server（stdio，零第三方依赖）。

在支持 MCP 的客户端里加一条即可：

    {
      "mcpServers": {
        "hy3-sandbox": {
          "command": "<python>",
          "args": ["-m", "app.sandbox.mcp_server"],
          "cwd": "<仓库根目录>"
        }
      }
    }

实现的是 MCP 的最小可用子集：initialize / tools/list / tools/call。
只依赖标准库，不引入 mcp SDK。
"""

from __future__ import annotations

import json
import sys
from typing import Any

from . import debug_api

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "hy3-sandbox-debugger", "version": "0.1.0"}


def _ok(mid: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _err(mid: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": code, "message": message}}


def _tools_list() -> dict:
    return {"tools": debug_api.TOOLS}


def _tools_call(args: dict) -> dict:
    name = args.get("name")
    arguments = args.get("arguments") or {}
    result = debug_api.call_tool(name, arguments)
    # MCP 要求 content 为数组；这里统一回一段 JSON 文本，客户端自行解析
    return {
        "content": [{"type": "text",
                     "text": json.dumps(result, ensure_ascii=False,
                                        indent=2, default=str)}],
        "isError": not result.get("ok", True),
    }


def handle(msg: dict) -> dict | None:
    mid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}

    if method == "initialize":
        return _ok(mid, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })
    if method == "notifications/initialized" or method == "initialized":
        return None  # 通知类消息不回包
    if method == "tools/list":
        return _ok(mid, _tools_list())
    if method == "tools/call":
        return _ok(mid, _tools_call(params))
    if method == "ping":
        return _ok(mid, {})
    if method == "shutdown":
        debug_api._shutdown_all()
        return _ok(mid, {})
    return _err(mid, -32601, f"不支持的方法: {method}")


def main() -> int:
    # 协议走 stdout；诊断信息一律走 stderr，避免污染
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            sys.stderr.write(f"[mcp] 无法解析: {line[:120]}\n")
            continue
        try:
            resp = handle(msg)
        except BaseException as exc:  # noqa: BLE001
            resp = _err(msg.get("id"), -32603, f"{type(exc).__name__}: {exc}")
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
