"""把 Solver 的输出切分为固定五段。

Solver 被强制要求使用 `## N. 标题` 形式的标题输出；这里做宽松解析，
以兼容模型偶尔写成 `### 1、解题思路` 之类的变体。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..config import SECTION_TITLES, STEP_LABELS

# 匹配 "## 1. 解题思路" / "### 2、复杂度分析" / "## 解题思路" 等写法
_HEADING_RE = re.compile(
    r"^\s*#{1,6}\s*(?:第?\s*[0-9一二三四五]\s*[.、:：\-]?\s*)?(.+?)\s*$",
    re.M,
)


@dataclass
class Section:
    title: str
    step_id: str
    content: str
    index: int

    @property
    def is_empty(self) -> bool:
        return len(self.content.strip()) == 0


@dataclass
class ParsedSolution:
    raw: str
    sections: list[Section]

    def get(self, title: str) -> Section | None:
        for s in self.sections:
            if s.title == title:
                return s
        return None

    def as_dict(self) -> dict[str, str]:
        return {s.title: s.content for s in self.sections}

    @property
    def missing_titles(self) -> list[str]:
        have = {s.title for s in self.sections}
        return [t for t in SECTION_TITLES if t not in have]


def _canonical_title(raw_title: str) -> str | None:
    """把模型写出的标题归一到标准五段标题之一。"""
    cleaned = re.sub(r"[\s*_`]+", "", raw_title)
    cleaned = re.sub(r"^[0-9一二三四五]+[.、:：\-]", "", cleaned)
    for title in SECTION_TITLES:
        if cleaned == title:
            return title
    # 模糊匹配：包含关系即可（如 “1. 解题思路（Approach）”）
    for title in SECTION_TITLES:
        core = title.replace("关键", "").replace("与处理策略", "")
        if core and core in cleaned:
            return title
    return None


def split_sections(raw: str) -> ParsedSolution:
    """按标题切分；未识别的标题会被合并进上一个已知段落。"""
    matches = list(_HEADING_RE.finditer(raw))
    if not matches:
        return ParsedSolution(raw=raw, sections=[])

    # 先定位所有可识别段落的起点
    anchors: list[tuple[int, str, str]] = []  # (start, canonical_title, raw_title)
    for m in matches:
        canon = _canonical_title(m.group(1))
        if canon:
            anchors.append((m.end(), canon, m.group(1)))

    if not anchors:
        return ParsedSolution(raw=raw, sections=[])

    # 同一标题重复出现时保留首次，避免被后文覆盖
    seen: set[str] = set()
    unique: list[tuple[int, str]] = []
    for start, canon, _raw in anchors:
        if canon in seen:
            continue
        seen.add(canon)
        unique.append((start, canon))

    sections: list[Section] = []
    for i, (start, canon) in enumerate(unique):
        end = unique[i + 1][0] if i + 1 < len(unique) else len(raw)
        # 下一段的起点还包含它自己的标题行，需要回退到标题行开头
        if i + 1 < len(unique):
            next_start = unique[i + 1][0]
            # 从 next_start 往前找最近的换行，回退到该行的行首
            line_break = raw.rfind("\n", 0, next_start)
            if line_break != -1:
                end = line_break
            else:
                end = next_start
        else:
            end = len(raw)
        body = raw[start:end].strip()
        sections.append(
            Section(
                title=canon,
                step_id=STEP_LABELS.get(canon, f"step_{i + 1}"),
                content=body,
                index=i,
            )
        )

    # 按标准顺序排序，保证步骤序号稳定
    order = {t: i for i, t in enumerate(SECTION_TITLES)}
    sections.sort(key=lambda s: order.get(s.title, 99))
    for i, s in enumerate(sections):
        s.index = i

    return ParsedSolution(raw=raw, sections=sections)


_CODE_BLOCK_RE = re.compile(r"```(?:python|py|python3)?\s*\n(.*?)```", re.S)


def _compiles(text: str) -> bool:
    """能否当作 Python 代码。用 compile 校验，中文注释不影响。"""
    stripped = text.strip()
    if not stripped:
        return False
    try:
        compile(stripped, "<candidate>", "exec")
        return True
    except SyntaxError:
        return False
    except ValueError:
        # 源码里混入空字节等，同样不能当代码用
        return False


def extract_code(text: str) -> str:
    """从“代码实现”段落或整段输出中提取 Python 代码。

    按 **围栏块 → 缩进块 → 裸代码** 逐级回退。裸代码只在能通过 compile()
    时才采纳——把散文当代码喂进沙盒会全量判 `missing_entry`（代码根本没跑），
    比"没提取到"更难排查。

    回退的必要性：用户直接粘贴函数定义（无 ``` 围栏）是很常见的操作，
    此前这种情况会静默返回空串，测试全挂却报"代码有错"。
    """
    blocks = _CODE_BLOCK_RE.findall(text)
    if blocks:
        # 取最长的代码块，通常是完整实现
        return max(blocks, key=lambda b: len(b.strip())).strip()

    # 缩进代码块：Markdown 里四个空格（或一个 tab）表示一个代码块
    best = ""
    buf: list[str] = []
    for line in text.splitlines():
        if line.startswith(("    ", "\t")) and line.strip():
            buf.append(line[4:] if line.startswith("    ") else line[1:])
        else:
            if buf:
                cand = "\n".join(buf)
                if len(cand) > len(best) and _compiles(cand):
                    best = cand
                buf = []
    if buf:
        cand = "\n".join(buf)
        if len(cand) > len(best) and _compiles(cand):
            best = cand
    if best:
        return best.strip()

    # 裸代码：整段本身就是代码
    if _compiles(text):
        return text.strip()
    return ""
