"""小工具：把任意对象转成人眼可读的短字符串。

题集里有 n=10^4 的性能用例，直接 repr 会刷屏几千字符，
在终端输出和 Markdown 报告里都必须截断。
"""

from __future__ import annotations


def short_repr(obj, limit: int = 80) -> str:
    text = repr(obj)
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-limit // 4 :]
    return f"{head} …（共 {len(text)} 字符）… {tail}"


def shorten_seq(obj, limit: int = 80):
    """对超长序列先缩略元素个数，再交给 short_repr。"""
    if isinstance(obj, (list, tuple)) and len(obj) > 12:
        preview = type(obj)(list(obj[:6]) + list(obj[-2:])) if not isinstance(obj, tuple) else tuple(list(obj[:6]) + list(obj[-2:]))
        return short_repr(preview, limit)
    return short_repr(obj, limit)
