"""离线 Mock 后端。

设计原则：它不是“假装聪明的随机文本生成器”，而是一个**确定性的替身**：
* Solver 角色 —— 返回由题集预先绑定的解答（通常是精心构造的伪正确样本），
  使得整条链路在无 Key 环境下仍可端到端复现。
* Critic 角色 —— 直接把提示词中 `<signals>` 块里的规则信号翻译成结构化裁决，
  等价于“只启用规则通道、关闭 LLM 通道”的评估模式。

这样 Mock 模式跑出来的结果是可解释、可复现的，而不是噪声。
"""

from __future__ import annotations

import json
import re

from .base import BaseLLM, ChatMessage, LLMResponse

_ROLE_RE = re.compile(r"\[ROLE=(\w+)\]")
_SIGNALS_RE = re.compile(r"<signals>\s*(.*?)\s*</signals>", re.S)
_JSON_RE = re.compile(r"\{.*\}", re.S)


class MockLLM(BaseLLM):
    name = "mock"

    def __init__(self) -> None:
        self._bound_solution: str | None = None

    # ------------------------------------------------------------ 绑定

    def bind_solution(self, solution_text: str) -> None:
        """绑定下一次 Solver 调用要返回的解答。"""
        self._bound_solution = solution_text

    # ------------------------------------------------------------ 主入口

    def chat(
        self,
        messages: list[ChatMessage],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format_json: bool = False,
    ) -> LLMResponse:
        blob = "\n".join(m.content for m in messages)
        role_match = _ROLE_RE.search(blob)
        role = role_match.group(1) if role_match else "generic"

        if role == "solver":
            text = self._bound_solution or self._fallback_solution()
        elif role == "critic":
            text = self._critic_response(blob)
        else:
            text = ""

        return LLMResponse(text=text, raw={"mock": True}, model="mock", simulated=True)

    # ------------------------------------------------------------ 各角色

    def _fallback_solution(self) -> str:
        return "\n\n".join(
            [
                "## 1. 解题思路\n（Mock 后端：未绑定预置解答）",
                "## 2. 复杂度分析\n时间复杂度 O(n)，空间复杂度 O(1)。",
                "## 3. 关键边界与处理策略\n处理空输入与单元素输入。",
                "## 4. 代码实现\n```python\npass\n```",
                "## 5. 自测说明\n以公开测试用例验证。",
            ]
        )

    def _critic_response(self, blob: str) -> str:
        signals = self._extract_signals(blob)
        verdict = self._verdict_from_signals(signals)
        return json.dumps(verdict, ensure_ascii=False, indent=2)

    @staticmethod
    def _extract_signals(blob: str) -> dict:
        match = _SIGNALS_RE.search(blob)
        if not match:
            return {}
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _verdict_from_signals(signals: dict) -> dict:
        """把规则信号翻译成 Critic 的结构化输出（规则通道单独生效时的等价物）。"""
        findings = signals.get("rule_findings", [])
        section = signals.get("section_title", "")

        section_hits = [
            f
            for f in findings
            if f.get("section") in (section, "", None) or f.get("section") == section
        ]
        # 与本节无关的规则命中不作为本节裁决依据
        if not section_hits:
            section_hits = [f for f in findings if f.get("section") == section]

        if section_hits:
            top = max(section_hits, key=lambda f: float(f.get("confidence", 0.0)))
            confidence = float(top.get("confidence", 0.6))
            return {
                "verdict": "flawed" if confidence >= 0.5 else "suspicious",
                "confidence": round(confidence, 2),
                "error_types": [top.get("error_type", "实现逻辑缺陷")],
                "reason": top.get("detail", "规则信号命中"),
                "evidence": top.get("evidence", ""),
            }

        # 无规则命中时的兜底：根据执行信号给出有限判断
        pub = signals.get("public", {}) or {}
        adv = signals.get("adversarial", {}) or {}
        if section == "代码实现" and adv.get("total") and adv.get("failed", 0) > 0:
            return {
                "verdict": "flawed",
                "confidence": 0.7,
                "error_types": ["实现逻辑缺陷"],
                "reason": (
                    f"对抗测试 {adv.get('failed')}/{adv.get('total')} 失败，"
                    "实现存在未被公开测试覆盖的逻辑缺陷。"
                ),
                "evidence": str(adv.get("failure_samples", []))[:300],
            }

        return {
            "verdict": "valid",
            "confidence": 0.55,
            "error_types": [],
            "reason": "未见明确反证；注意：Mock 模式仅启用规则通道，结论强度有限。",
            "evidence": (
                f"公开测试 {pub.get('passed', 0)}/{pub.get('total', 0)} 通过，"
                f"对抗测试 {adv.get('passed', 0)}/{adv.get('total', 0)} 通过。"
            ),
        }
