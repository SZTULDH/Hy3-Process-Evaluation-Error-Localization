"""把 submodule（submodules/sandbox）的调试能力桥接到主仓库 app.sandbox 命名空间。

submodule 内部使用相对导入（例如 ``from .debugger import DebugSession``），
所以这里不是按文件路径单独加载，而是构造一个独立的合成包 ``_sandbox_ext``，
把它的 ``__path__`` 指向 submodule 的 app/sandbox 目录，交由 Python 正常的
import 机制解析相对导入，同时避免与主仓库自身的 ``app.sandbox`` 同名冲突。
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types

_PKG = "_sandbox_ext"

_HERE = os.path.dirname(os.path.abspath(__file__))
EXT_DIR = os.path.normpath(
    os.path.join(_HERE, "..", "..", "submodules", "sandbox", "app", "sandbox")
)


def _ensure_pkg() -> types.ModuleType:
    """确保合成包已注册；submodule 未初始化时给出明确报错。"""
    pkg = sys.modules.get(_PKG)
    if pkg is not None:
        return pkg
    if not os.path.isdir(EXT_DIR):
        raise ImportError(
            "未找到 submodule 内容：%s\n请先执行：git submodule update --init --recursive" % EXT_DIR
        )
    pkg = types.ModuleType(_PKG)
    pkg.__path__ = [EXT_DIR]
    sys.modules[_PKG] = pkg
    return pkg


def load(name: str):
    """加载 submodule 中的 app/sandbox/<name>.py 并返回该模块。"""
    _ensure_pkg()
    full = "%s.%s" % (_PKG, name)
    if full in sys.modules:
        return sys.modules[full]
    path = os.path.join(EXT_DIR, name + ".py")
    if not os.path.exists(path):
        raise ImportError("submodule 中不存在模块：%s" % path)
    spec = importlib.util.spec_from_file_location(full, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full] = mod
    spec.loader.exec_module(mod)
    return mod
