"""结果输出：JSONL 明细 + 人类可读 Markdown 报告。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..config import RESULTS_DIR, llm_backend_name
from ..evaluator.validation import ValidationReport
from ..utils import shorten_seq

_ICON = {"ok": "PASS", "wrong_answer": "FAIL", "timeout": "TIMEOUT",
         "runtime_error": "ERROR", "import_error": "ERROR",
         "missing_entry": "ERROR", "not_run": "SKIP", "unserializable": "ERROR"}


def _status_icon(status: str) -> str:
    return _ICON.get(status, status.upper())


def _fmt_case(r: dict) -> str:
    return f"- `{_status_icon(r['status'])}` 输入 `{r['args']}` → 期望 `{r['expected']}`" + (
        f"，实际 `{r['actual']}`" if r["status"] == "wrong_answer" else ""
    ) + (f"（{r['error']}）" if r.get("error") else "")


def write_jsonl(records: list[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False, default=repr) + "\n")
    return path


def write_markdown(
    records: list[dict],
    validation: ValidationReport,
    path: Path,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    total = len(records)
    pseudo = sum(1 for r in records if r["evaluation"]["false_positive_solution"])
    result_ok = sum(1 for r in records if r["evaluation"]["result_correct"])
    truly_ok = sum(1 for r in records if r["evaluation"]["truly_correct"])

    lines += [
        "# 过程评估与错误定位 · 评测报告",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- LLM 后端：`{llm_backend_name()}`",
        f"- 样本数：{total}",
        "",
        "## 一、总体结论",
        "",
        "| 指标 | 数值 | 说明 |",
        "| --- | --- | --- |",
        f"| 结果正确率（公开测试） | {result_ok}/{total} | 传统意义上“做对了”的比例 |",
        f"| 真实正确率（公开+对抗） | {truly_ok}/{total} | 逻辑层面站得住的比例 |",
        f"| 伪正确样本数 | {pseudo}/{total} | 公开测试通过但过程/逻辑不成立 |",
        f"| 伪正确识别率 | {validation.pseudo_correct_recall:.1%} | 伪正确样本中被抓出的比例 |",
        f"| 步骤定位准确率（top-1） | {validation.localization_accuracy:.1%} | 首个错误步骤完全命中 |",
        f"| 步骤定位准确率（±1 容差） | {validation.localization_accuracy_tolerant:.1%} | 允许相邻步骤 |",
        f"| 误报率 | {validation.false_alarm_rate:.1%} | 正确样本被判为有错的比例 |",
        f"| 过程判定准确率 | {validation.process_judgement_accuracy:.1%} | 过程是否成立的整体判断 |",
        "",
        "**核心发现**：结果正确率与真实正确率之间的差值，就是“测试用例通过但逻辑有缺陷”",
        "的伪正确区间。只看公开测试会系统性高估模型的代码能力。",
        "",
        "## 二、逐题结果",
        "",
        "| 题目 | 难度 | 公开 | 对抗 | 过程成立 | 伪正确 | 首个错误步骤 | 错误类型 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    for rec in records:
        ev = rec["evaluation"]
        pub = ev["public"]
        adv = ev["adversarial"]
        lines.append(
            "| {title} | {diff} | {pub} | {adv} | {valid} | {fp} | {step} | {types} |".format(
                title=f"`{ev['problem_id']}` {ev['title']}",
                diff=ev["difficulty"],
                pub=f"{pub['passed']}/{pub['total']}",
                adv=f"{adv['passed']}/{adv['total']}",
                valid="是" if ev["process_valid"] else "**否**",
                fp="**是**" if ev["false_positive_solution"] else "否",
                step=ev["first_error_section"] or "—",
                types="、".join(ev["error_types"]) or "—",
            )
        )

    lines += ["", "## 三、逐题详情", ""]

    for rec in records:
        ev = rec["evaluation"]
        gt = (rec["problem"].get("ground_truth") or {})
        pub, adv = ev["public"], ev["adversarial"]

        lines += [
            f"### `{ev['problem_id']}` {ev['title']}（{ev['difficulty']}）",
            "",
            f"- 公开测试：**{pub['passed']}/{pub['total']}**　对抗测试：**{adv['passed']}/{adv['total']}**",
            f"- 结果正确：{'是' if ev['result_correct'] else '否'}　"
            f"真实正确：{'是' if ev['truly_correct'] else '否'}　"
            f"过程成立：{'是' if ev['process_valid'] else '否'}",
            f"- 伪正确判定：**{'是' if ev['false_positive_solution'] else '否'}**",
            f"- 首个错误步骤：`{ev['first_error_step'] or '—'}`"
            f"（人工标注：`{gt.get('first_error_step') or '—'}`）",
            f"- 错误类型：{'、'.join(ev['error_types']) or '—'}",
            f"- 耗时：{ev['elapsed_sec']}s",
            "",
        ]

        if pub.get("failure_samples"):
            lines += ["**公开测试失败用例**", ""]
            lines += [_fmt_case(r) for r in pub["failure_samples"]]
            lines.append("")

        if adv.get("failure_samples"):
            lines += ["**对抗测试失败用例（暴露真实逻辑缺陷）**", ""]
            lines += [_fmt_case(r) for r in adv["failure_samples"]]
            lines.append("")

        if ev["section_verdicts"]:
            lines += ["**分步裁决**", "", "| 步骤 | 判定 | 置信度 | 依据 | 信号来源 |", "| --- | --- | --- | --- | --- |"]
            for v in ev["section_verdicts"]:
                lines.append(
                    "| {s} | {v} | {c} | {r} | {a} |".format(
                        s=v["section"],
                        v={"flawed": "**不成立**", "suspicious": "存疑", "valid": "成立"}.get(
                            v["verdict"], v["verdict"]
                        ),
                        c=v["confidence"],
                        r=(v["reason"] or "").replace("|", "\\|")[:150],
                        a={
                            "agree": "规则+审查一致",
                            "rule_only": "仅执行证据",
                            "llm_only": "仅 LLM 审查",
                            "conflict": "冲突（以执行证据为准）",
                        }.get(v["signal_agreement"], v["signal_agreement"]),
                    )
                )
            lines.append("")

        if gt.get("note"):
            lines += [f"> 人工标注说明：{gt['note']}", ""]

        lines += ["<details><summary>模型提取到的代码</summary>", "", "```python", ev["code"], "```", "", "</details>", ""]

    lines += [
        "## 四、评估器有效性分析",
        "",
        f"- 定位准确率（top-1）：**{validation.localization_accuracy:.1%}**"
        f"（{validation.localization_top1}/{validation.gt_flawed}）",
        f"- 定位准确率（±1 容差）：**{validation.localization_accuracy_tolerant:.1%}**",
        f"- 误报率：**{validation.false_alarm_rate:.1%}**"
        f"（{validation.false_alarms}/{validation.gt_clean}）",
        "",
    ]

    if validation.localization_details:
        lines += ["**逐样本定位对照**", "", "| 题目 | 人工标注 | 系统定位 | 结论 |", "| --- | --- | --- | --- |"]
        for d in validation.localization_details:
            verdict = "命中" if d["top1_hit"] else ("相邻" if d["tolerant_hit"] else "**偏差**")
            lines.append(
                f"| `{d['problem_id']}` | {d['ground_truth']} | {d['predicted'] or '—'} | {verdict} |"
            )
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_reports(records: list[dict], validation: ValidationReport, tag: str = "run") -> dict:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    jsonl = RESULTS_DIR / f"{tag}_{stamp}.jsonl"
    md = RESULTS_DIR / f"report_{stamp}.md"
    write_jsonl(records, jsonl)
    write_markdown(records, validation, md)
    # 最新报告固定路径，便于 README 与脚本引用
    latest = RESULTS_DIR / "latest_report.md"
    latest.write_text(md.read_text(encoding="utf-8"), encoding="utf-8")
    return {"jsonl": str(jsonl), "markdown": str(md), "latest": str(latest)}
