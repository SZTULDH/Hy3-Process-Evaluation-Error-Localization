"""分步 Critic：LLM 判断 + 规则信号融合。

融合策略（对应 PROPOSAL“多信号融合，降低纯 LLM 审查的主观性与误报”）：
* 规则信号来自**真实执行**，属于硬证据；
* LLM 审查属于软判断，能发现规则覆盖不到的语义问题；
* 二者冲突时以硬证据为准，但如实记录分歧，便于后续分析误报来源。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..config import CRITIC_CONFIDENCE_THRESHOLD
from ..llm.base import BaseLLM, ChatMessage
from .rules import Finding
from .taxonomy import ERROR_TYPES, label_of, normalize_error_type

_JSON_RE = re.compile(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", re.S)

SYSTEM_PROMPT = """[ROLE=critic]
你是代码解题过程的严谨评审专家。你的任务是审查解题过程中的**某一个步骤**，判断它是否成立。

重要原则：
1. 你只审查被指定的那一个步骤，不要越界评价其他步骤。
2. 执行结果是硬证据。如果 `<signals>` 里显示测试失败，那么实现一定有问题，不要为它辩护。
3. 区分"结果正确"和"过程正确"：即使测试全部通过，思路描述、复杂度分析、边界讨论仍可能不成立。
4. 只在你有具体依据时才判定为 flawed，不要臆测。
5. 必须严格输出 JSON，不要输出任何额外文字。

输出格式：
{
  "verdict": "valid" | "suspicious" | "flawed",
  "confidence": 0.0 到 1.0 之间的数字,
  "error_types": ["错误类型标签"],
  "reason": "一句话说明判断依据",
  "evidence": "引用的具体证据"
}

错误类型只能从以下列表中选择（可多选，无问题时为空数组）：
""" + "、".join(t.label for t in ERROR_TYPES.values())


USER_TEMPLATE = """## 题目
{description}

函数入口：`{entry_point}`
题目期望复杂度：{expected_complexity}

## 待审查步骤
**步骤名称**：{section_title}
**步骤内容**：
{section_content}

## 完整代码实现（供对照）
```python
{code}
```

## 执行与静态分析信号
<signals>
{signals}
</signals>

请只针对「{section_title}」这一步给出裁决。
"""


@dataclass
class SectionVerdict:
    section: str
    step_id: str
    verdict: str  # valid | suspicious | flawed
    confidence: float
    error_types: list[str] = field(default_factory=list)
    reason: str = ""
    evidence: str = ""
    llm_verdict: str | None = None
    rule_types: list[str] = field(default_factory=list)
    agreement: str = "n/a"  # agree | rule_only | llm_only | conflict

    @property
    def is_flawed(self) -> bool:
        return self.verdict == "flawed"

    def to_dict(self) -> dict:
        return {
            "section": self.section,
            "step_id": self.step_id,
            "verdict": self.verdict,
            "confidence": round(self.confidence, 2),
            "error_types": [label_of(t) for t in self.error_types],
            "reason": self.reason,
            "evidence": self.evidence[:400],
            "llm_verdict": self.llm_verdict,
            "rule_types": [label_of(t) for t in self.rule_types],
            "signal_agreement": self.agreement,
        }


def _parse_verdict(text: str) -> dict | None:
    """从 LLM 输出中稳健地抠出 JSON。"""
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    for candidate in (text, *_JSON_RE.findall(text)):
        try:
            data = json.loads(candidate)
            if isinstance(data, dict) and "verdict" in data:
                return data
        except json.JSONDecodeError:
            continue
    return None


class Critic:
    def __init__(self, llm: BaseLLM) -> None:
        self.llm = llm

    def review_section(
        self,
        section_title: str,
        step_id: str,
        section_content: str,
        problem: dict,
        code: str,
        signals: dict,
        rule_findings: list[Finding],
    ) -> SectionVerdict:
        mine = [f for f in rule_findings if f.section == section_title]
        rule_types = sorted({f.error_type for f in mine})
        top_rule = max(mine, key=lambda f: f.confidence, default=None)

        prompt = USER_TEMPLATE.format(
            description=problem.get("description", ""),
            entry_point=problem.get("entry_point", ""),
            expected_complexity=(problem.get("expected_complexity") or {}).get("time", "未指定"),
            section_title=section_title,
            section_content=(section_content or "（该段落缺失或为空）")[:4000],
            code=(code or "（未提取到代码）")[:4000],
            signals=json.dumps(signals, ensure_ascii=False, default=repr),
        )

        llm_verdict = None
        llm_conf = 0.0
        llm_types: list[str] = []
        llm_reason = ""
        llm_evidence = ""

        try:
            resp = self.llm.chat(
                [
                    ChatMessage(role="system", content=SYSTEM_PROMPT),
                    ChatMessage(role="user", content=prompt),
                ],
                response_format_json=True,
            )
            data = _parse_verdict(resp.text)
            if data:
                llm_verdict = str(data.get("verdict", "valid")).lower()
                if llm_verdict not in ("valid", "suspicious", "flawed"):
                    llm_verdict = "suspicious"
                try:
                    llm_conf = float(data.get("confidence", 0.5))
                except (TypeError, ValueError):
                    llm_conf = 0.5
                llm_conf = max(0.0, min(1.0, llm_conf))
                raw_types = data.get("error_types") or []
                if isinstance(raw_types, str):
                    raw_types = [raw_types]
                llm_types = [
                    c for c in (normalize_error_type(str(t)) for t in raw_types) if c
                ]
                llm_reason = str(data.get("reason", ""))
                llm_evidence = str(data.get("evidence", ""))
        except Exception as exc:  # noqa: BLE001 - Critic 失败不应中断整条流水线
            llm_reason = f"Critic 调用失败，仅采用规则信号：{type(exc).__name__}: {exc}"

        return self._fuse(
            section_title,
            step_id,
            top_rule,
            rule_types,
            llm_verdict,
            llm_conf,
            llm_types,
            llm_reason,
            llm_evidence,
        )

    def _fuse(
        self,
        section_title: str,
        step_id: str,
        top_rule: Finding | None,
        rule_types: list[str],
        llm_verdict: str | None,
        llm_conf: float,
        llm_types: list[str],
        llm_reason: str,
        llm_evidence: str,
    ) -> SectionVerdict:
        rule_flawed = top_rule is not None and top_rule.confidence >= CRITIC_CONFIDENCE_THRESHOLD
        llm_flawed = llm_verdict == "flawed" and llm_conf >= CRITIC_CONFIDENCE_THRESHOLD
        llm_suspicious = llm_verdict == "suspicious"

        merged_types = sorted(set(rule_types) | set(llm_types))

        if rule_flawed and llm_flawed:
            agreement = "agree"
            verdict = "flawed"
            confidence = max(top_rule.confidence, llm_conf)
            reason = top_rule.detail
            evidence = top_rule.evidence
            if llm_reason:
                reason = f"{reason} Critic 补充：{llm_reason}"
        elif rule_flawed:
            agreement = "rule_only"
            verdict = "flawed"
            confidence = top_rule.confidence
            reason = top_rule.detail
            evidence = top_rule.evidence
            if llm_verdict == "valid":
                reason += "（LLM 审查未发现此问题，但执行证据更可靠，以执行结果为准。）"
        elif llm_flawed:
            agreement = "llm_only"
            verdict = "flawed"
            confidence = llm_conf
            reason = llm_reason or "Critic 判定该步骤不成立。"
            evidence = llm_evidence
        elif llm_suspicious:
            agreement = "llm_only"
            verdict = "suspicious"
            confidence = llm_conf
            reason = llm_reason
            evidence = llm_evidence
        else:
            agreement = "agree" if llm_verdict == "valid" else "n/a"
            verdict = "valid"
            confidence = llm_conf if llm_verdict == "valid" else 0.5
            reason = llm_reason or "未见明确反证。"
            evidence = llm_evidence

        return SectionVerdict(
            section=section_title,
            step_id=step_id,
            verdict=verdict,
            confidence=round(confidence, 2),
            error_types=merged_types or ([top_rule.error_type] if top_rule else []),
            reason=reason,
            evidence=evidence,
            llm_verdict=llm_verdict,
            rule_types=rule_types,
            agreement=agreement,
        )
