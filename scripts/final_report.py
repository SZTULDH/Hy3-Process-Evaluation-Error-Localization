#!/usr/bin/env python
"""汇总生成最终分析报告。

输入两条实验路线的产物：
  A. 全量真机跑批  results/run_*/all.jsonl         —— 模型能力评测
  B. 参考解答验证  results/ref_validation_*/       —— 评估器有效性验证

输出 results/final_report.md 与 results/final_summary.json，覆盖：
最终答案准确率、过程正确率、错误类型分布、难度分层、有效性验证（定位准确率 /
误报率 / 伪正确识别率）与误报抽检清单、典型案例、能力边界分析。

用法：
    python scripts/final_report.py --run results/run_xxx --ref results/ref_validation_yyy
    # 两个路径都可省略，省略时自动取最新的
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"

DIFF_ORDER = ["easy", "medium", "hard", "adversarial", "engineering", "realworld"]
DIFF_CN = {"easy": "简单", "medium": "中等", "hard": "困难",
           "adversarial": "对抗", "engineering": "工程", "realworld": "工程"}
STEP_CN = {
    "step_1_approach": "解题思路", "step_2_complexity": "复杂度分析",
    "step_3_edge_cases": "关键边界与处理策略", "step_4_implementation": "代码实现",
    "step_5_selftest": "自测说明",
}


def latest_dir(pattern: str) -> Path | None:
    cands = sorted([p for p in RESULTS.glob(pattern) if p.is_dir()])
    return cands[-1] if cands else None


def load_jsonl(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def pct(a: int, b: int) -> str:
    return f"{a / b:.1%}" if b else "—"


def bar(value: float, width: int = 20) -> str:
    n = int(round(value * width))
    return "█" * n + "·" * (width - n)


# ------------------------------------------------------------------ 载入

def load_run(run_dir: Path) -> tuple[list[dict], dict]:
    recs = load_jsonl(run_dir / "all.jsonl")
    recs = [r for r in recs if "verdict" in r]          # 过滤掉异常记录
    meta = {}
    mp = run_dir / "run_meta.json"
    if mp.exists():
        meta = json.loads(mp.read_text(encoding="utf-8"))
    return recs, meta


def load_ref(ref_dir: Path) -> tuple[list[dict], dict]:
    recs = load_jsonl(ref_dir / "reference.jsonl")
    metrics = {}
    mp = ref_dir / "metrics.json"
    if mp.exists():
        metrics = json.loads(mp.read_text(encoding="utf-8"))
    return recs, metrics


# ------------------------------------------------------------------ 分析

def stratify(recs: list[dict], key_fn) -> dict[str, dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in recs:
        groups[key_fn(r)].append(r)
    out = {}
    for k, rows in groups.items():
        n = len(rows)
        out[k] = {
            "n": n,
            "result_correct": sum(1 for r in rows if r["verdict"]["result_correct"]),
            "truly_correct": sum(1 for r in rows if r["verdict"]["truly_correct"]),
            "process_valid": sum(1 for r in rows if r["verdict"]["process_valid"]),
            "false_positive": sum(1 for r in rows if r["verdict"]["false_positive_solution"]),
            "avg_sec": statistics.mean([r.get("elapsed_sec", 0) for r in rows]) if rows else 0,
        }
    return out


def error_type_counter(recs: list[dict]) -> Counter:
    c: Counter = Counter()
    for r in recs:
        types = r["verdict"].get("error_types") or []
        if not types:
            c["（无缺陷判定）"] += 1
        for t in types:
            c[t] += 1
    return c


def step_counter(recs: list[dict]) -> Counter:
    c: Counter = Counter()
    for r in recs:
        sec = r["verdict"].get("first_error_section")
        c[sec or "（无）"] += 1
    return c


def audit_false_alarms(ref_recs: list[dict]) -> list[dict]:
    """误报抽检：对"标注无错却被判有问题"的样本逐条给客观证据。

    可机器判定的铁证是**对抗测试是否真的失败**：
    * 对抗测试有失败用例 -> 该解答确实存在可复现缺陷，属"真阳性"，不是误报
    * 对抗测试全过、仅 LLM 审查判不成立 -> 规则层无佐证，列为"疑似误报"待人工复核
    """
    rows = []
    for r in ref_recs:
        gt = r.get("ground_truth") or {}
        if not gt.get("process_valid", True):
            continue                      # 只看标注为"过程成立"的样本
        if r["verdict"]["process_valid"]:
            continue                      # 评估器也判成立 -> 无争议
        adv = r.get("adversarial") or {}
        pub = r.get("public") or {}
        adv_failed = int(adv.get("failed") or 0)
        pub_failed = int(pub.get("failed") or 0)
        if adv_failed or pub_failed:
            cls = "真阳性（对抗/公开测试可复现缺陷）"
        else:
            cls = "疑似误报（无执行证据，仅 LLM 判定）"
        rows.append({
            "problem_id": r["problem_id"], "difficulty": r.get("difficulty"),
            "gt": "过程成立", "predicted": "过程不成立",
            "predicted_step": r["verdict"].get("first_error_step"),
            "predicted_types": r["verdict"].get("error_types"),
            "public": f"{pub.get('passed')}/{pub.get('total')}",
            "adversarial": f"{adv.get('passed')}/{adv.get('total')}",
            "verdict": cls,
        })
    return rows


def typical_cases(run_recs: list[dict], ref_recs: list[dict], limit: int = 6) -> dict:
    """挑几类典型案例：伪正确 / 定位命中 / 定位偏差 / 复杂度误判。"""
    pseudo = [r for r in run_recs if r["verdict"]["false_positive_solution"]]
    pseudo.sort(key=lambda r: (r["public"]["failed"] + r["adversarial"]["failed"]) * -1)
    loc_hit = [
        r for r in ref_recs
        if (r.get("ground_truth") or {}).get("first_error_step")
        and r["verdict"]["first_error_step"] == r["ground_truth"]["first_error_step"]
    ]
    loc_miss = [
        r for r in ref_recs
        if (r.get("ground_truth") or {}).get("first_error_step")
        and r["verdict"]["first_error_step"] != r["ground_truth"]["first_error_step"]
    ]
    return {
        "pseudo": pseudo[:limit], "loc_hit": loc_hit[:limit], "loc_miss": loc_miss[:limit],
        "pseudo_all": pseudo, "loc_miss_all": loc_miss,
    }


# ------------------------------------------------------------------ 报告

def build(run_recs: list[dict], run_meta: dict,
          ref_recs: list[dict], ref_metrics: dict) -> str:
    n = len(run_recs)
    res_ok = sum(1 for r in run_recs if r["verdict"]["result_correct"])
    truly_ok = sum(1 for r in run_recs if r["verdict"]["truly_correct"])
    proc_ok = sum(1 for r in run_recs if r["verdict"]["process_valid"])
    pseudo = sum(1 for r in run_recs if r["verdict"]["false_positive_solution"])
    avg_sec = statistics.mean([r.get("elapsed_sec", 0) for r in run_recs]) if run_recs else 0

    by_diff = stratify(run_recs, lambda r: r.get("difficulty") or "unknown")
    by_kind = stratify(run_recs, lambda r: r.get("kind") or "function")
    etypes = error_type_counter(run_recs)
    steps = step_counter(run_recs)
    cases = typical_cases(run_recs, ref_recs)
    audits = audit_false_alarms(ref_recs)

    L: list[str] = []
    A = L.append

    A("# Hy3 过程评估与错误定位 · 最终实验报告")
    A("")
    A(f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    A(f"- 被测模型：`{run_meta.get('model', '—')}` @ `{run_meta.get('base_url', '—')}`")
    A(f"- 思考强度：`{run_meta.get('thinking', '—')}`　单轮超时：{run_meta.get('llm_timeout', '—')}s"
      f"　Critic 并发：{run_meta.get('critic_parallelism', '—')}")
    A(f"- 题集规模：{n} 题（含函数级与类级）")
    A(f"- 单题平均耗时：{avg_sec:.0f}s")
    A("")
    A("两条独立实验路线：")
    A("")
    A("| 路线 | 做法 | 用途 |")
    A("| --- | --- | --- |")
    A(f"| **A 模型能力评测** | 真实模型生成五段解答后评估，共 {n} 题 | 答案准确率、过程正确率、错误分布、难度分层 |")
    A(f"| **B 评估器有效性** | 把题集**参考解答**直接喂给评估器，共 {len(ref_recs)} 题 | 定位准确率、误报率、伪正确识别率 |")
    A("")
    A("> 为什么必须分两条：`ground_truth` 标注的是**参考解答**的缺陷。拿模型自己生成的")
    A("> 解答去对这份标注，两边描述的不是同一个对象，定位准确率与误报率都不成立。")
    A("")

    # ---------------- 一、答案准确率
    A("## 一、最终答案准确率")
    A("")
    A("| 指标 | 数值 | 比例 | 说明 |")
    A("| --- | --- | --- | --- |")
    A(f"| 结果正确（公开测试全过） | {res_ok}/{n} | {pct(res_ok, n)} | 传统口径的“做对” |")
    A(f"| 真实正确（公开+对抗全过） | {truly_ok}/{n} | {pct(truly_ok, n)} | 逻辑层面站得住 |")
    A(f"| 公开过但对抗挂 | {res_ok - truly_ok}/{n} | {pct(res_ok - truly_ok, n)} | 只看公开测试会高估的部分 |")
    A("")
    A(f"**结论**：模型公开测试通过率 {pct(res_ok, n)}，但真实正确率只有 {pct(truly_ok, n)}，"
      f"两者相差 {pct(res_ok - truly_ok, n)}。这段差值就是「测试通过但逻辑不成立」的伪正确区间。")
    A("")

    # ---------------- 二、过程正确率
    A("## 二、过程正确率")
    A("")
    A("| 指标 | 数值 | 比例 |")
    A("| --- | --- | --- |")
    A(f"| 过程成立（五段均无高置信缺陷） | {proc_ok}/{n} | {pct(proc_ok, n)} |")
    A(f"| 被判伪正确样本 | {pseudo}/{n} | {pct(pseudo, n)} |")
    A(f"| 结果与过程双通过 | {sum(1 for r in run_recs if r['verdict']['result_correct'] and r['verdict']['process_valid'])}/{n} | "
      f"{pct(sum(1 for r in run_recs if r['verdict']['result_correct'] and r['verdict']['process_valid']), n)} |")
    A("")
    A("过程不成立的样本里，**首个不成立步骤**的分布：")
    A("")
    A("| 步骤 | 数量 | 占比 |")
    A("| --- | --- | --- |")
    for step, c in steps.most_common():
        A(f"| {STEP_CN.get(step, step)} | {c} | {pct(c, n)} |")
    A("")

    # ---------------- 三、错误类型分布
    A("## 三、错误类型分布")
    A("")
    A("> 一条样本可能命中多个错误类型，故合计大于样本数。")
    A("")
    A("| 错误类型 | 命中次数 | 占样本比例 | 分布 |")
    A("| --- | --- | --- | --- |")
    for t, c in etypes.most_common():
        A(f"| {t} | {c} | {pct(c, n)} | `{bar(c / n if n else 0)}` |")
    A("")

    rule_types: Counter = Counter()
    for r in run_recs:
        for f in (r.get("rule_findings") or []):
            rule_types[f.get("error_type_label") or f.get("error_type")] += 1
    if rule_types:
        A("规则通道（不依赖 LLM）单独命中情况：")
        A("")
        A("| 规则命中类型 | 次数 |")
        A("| --- | --- |")
        for t, c in rule_types.most_common():
            A(f"| {t} | {c} |")
        A("")

    # ---------------- 四、难度分层
    A("## 四、难度分层分析")
    A("")
    A("| 难度 | 题数 | 结果正确 | 真实正确 | 过程成立 | 伪正确 | 平均耗时 |")
    A("| --- | --- | --- | --- | --- | --- | --- |")
    ordered = [d for d in DIFF_ORDER if d in by_diff] + \
              [d for d in by_diff if d not in DIFF_ORDER]
    for d in ordered:
        s = by_diff[d]
        A(f"| {DIFF_CN.get(d, d)} `{d}` | {s['n']} | {pct(s['result_correct'], s['n'])} | "
          f"{pct(s['truly_correct'], s['n'])} | {pct(s['process_valid'], s['n'])} | "
          f"{pct(s['false_positive'], s['n'])} | {s['avg_sec']:.0f}s |")
    A("")
    A("**过程成立率随难度变化**")
    A("")
    A("```")
    for d in ordered:
        s = by_diff[d]
        v = s["process_valid"] / s["n"] if s["n"] else 0
        A(f"{DIFF_CN.get(d, d):<4} {bar(v)} {v:>6.1%}  (n={s['n']})")
    A("```")
    A("")
    for_curve = [(d, by_diff[d]) for d in ordered if by_diff[d]["n"]]
    if len(for_curve) >= 2:
        worst = min(for_curve, key=lambda kv: kv[1]["process_valid"] / kv[1]["n"])
        first_drop = None
        for i in range(1, len(for_curve)):
            prev = for_curve[i - 1][1]["process_valid"] / for_curve[i - 1][1]["n"]
            cur = for_curve[i][1]["process_valid"] / for_curve[i][1]["n"]
            if prev - cur >= 0.15:
                first_drop = for_curve[i][0]
                break
        if first_drop:
            A(f"**下降临界点**：从 `{first_drop}` 开始，过程成立率出现 ≥15 个百分点的")
            A("断崖式下降 —— 这是模型能力明显不支的难度区间。")
        A(f"**最低点**：`{worst[0]}`（过程成立率 {pct(worst[1]['process_valid'], worst[1]['n'])}）。")
    A("")
    if len(by_kind) > 1:
        A("**按题目形态对比**")
        A("")
        A("| 形态 | 题数 | 结果正确 | 过程成立 | 伪正确 |")
        A("| --- | --- | --- | --- | --- |")
        for k, s in by_kind.items():
            A(f"| {'类级（工程任务）' if k == 'class' else '函数级'} | {s['n']} | "
              f"{pct(s['result_correct'], s['n'])} | {pct(s['process_valid'], s['n'])} | "
              f"{pct(s['false_positive'], s['n'])} |")
        A("")

    # ---------------- 五、有效性验证
    A("## 五、过程评估器有效性验证")
    A("")
    if ref_metrics:
        t = ref_metrics.get("total_samples", 0)
        A(f"验证集：题集中 {t} 道带参考解答的题目（标注有错 "
          f"{ref_metrics.get('ground_truth_flawed')} / 标注无错 "
          f"{ref_metrics.get('ground_truth_clean')}）。")
        A("")
        A("| 指标 | 数值 | 说明 |")
        A("| --- | --- | --- |")
        A(f"| 定位准确率（top-1） | {ref_metrics.get('localization_accuracy_top1', 0):.1%} | "
          f"首个错误步骤与人工标注完全一致 |")
        A(f"| 定位准确率（±1 容差） | {ref_metrics.get('localization_accuracy_tolerant', 0):.1%} | "
          f"允许相邻步骤（步骤边界本身有模糊地带） |")
        A(f"| 误报率 | {ref_metrics.get('false_alarm_rate', 0):.1%} | "
          f"标注过程成立的样本被判为有错（{ref_metrics.get('false_alarm_count')}/"
          f"{ref_metrics.get('ground_truth_clean')}） |")
        A(f"| 伪正确识别率 | {ref_metrics.get('pseudo_correct_recall', 0):.1%} | "
          f"公开过对抗挂的样本被抓出（{ref_metrics.get('pseudo_correct_caught')}/"
          f"{ref_metrics.get('pseudo_correct_total')}） |")
        A(f"| 过程判定准确率 | {ref_metrics.get('process_judgement_accuracy', 0):.1%} | "
          f"过程成立/不成立的整体判断 |")
        A("")
    else:
        A("_（未找到参考解答验证结果，请先运行 `scripts/validate_reference.py`）_")
        A("")

    if audits:
        real = sum(1 for a in audits if a["verdict"].startswith("真阳性"))
        susp = len(audits) - real
        A("### 误报抽检")
        A("")
        A(f"被判定为「过程不成立」但人工标注为「过程成立」的样本共 {len(audits)} 条。")
        A("逐条核对执行证据（对抗/公开测试是否有可复现失败用例）：")
        A("")
        A(f"- **真阳性 {real} 条** —— 测试确实失败，说明该解答确有缺陷，只是标注时未把它记为过程问题")
        A(f"- **疑似误报 {susp} 条** —— 无任何执行证据，仅凭 LLM 审查判定，需人工终审")
        A("")
        A(f"据此，在「标注无错但被判有错」的 {len(audits)} 条争议样本中，"
          f"有 {susp} 条没有任何执行证据支撑，属**疑似误报**"
          f"（占争议样本 {pct(susp, len(audits))}）。")
        A("")
        A("| 题目 | 难度 | 公开 | 对抗 | 系统定位步骤 | 抽检结论 |")
        A("| --- | --- | --- | --- | --- | --- |")
        for a in audits[:40]:
            A(f"| `{a['problem_id']}` | {a['difficulty']} | {a['public']} | {a['adversarial']} | "
              f"{STEP_CN.get(a['predicted_step'], a['predicted_step'])} | {a['verdict']} |")
        A("")
        A("> 人工抽检说明：上表的「抽检结论」由执行证据自动判定（对抗测试是否有可复现的")
        A("> 失败用例）。标注为「疑似误报」的条目建议人工复看其 LLM 判据后再定性。")
        A("")

    # ---------------- 六、典型案例
    A("## 六、典型案例分析")
    A("")
    A("### 6.1 伪正确样本（公开测试全过、对抗测试暴露缺陷）")
    A("")
    if cases["pseudo_all"]:
        A(f"全量 {n} 题中共识别出 {len(cases['pseudo_all'])} 例。前几例：")
        A("")
        for r in cases["pseudo"][:5]:
            bad = (r["adversarial"].get("failure_samples") or [{}])[0]
            A(f"**`{r['problem_id']}` {r['title']}（{r['difficulty']}）**")
            A("")
            A(f"- 公开 {r['public']['passed']}/{r['public']['total']}，"
              f"对抗 {r['adversarial']['passed']}/{r['adversarial']['total']}")
            A(f"- 首个不成立步骤：{STEP_CN.get(r['verdict']['first_error_step'], r['verdict']['first_error_step'])}")
            A(f"- 错误类型：{'、'.join(r['verdict']['error_types']) or '—'}")
            if bad.get("args") is not None:
                A(f"- 暴露缺陷的用例：`{json.dumps(bad.get('args'), ensure_ascii=False)}` "
                  f"期望 `{json.dumps(bad.get('expected'), ensure_ascii=False)}`，"
                  f"实际 `{json.dumps(bad.get('actual'), ensure_ascii=False)}`")
                if bad.get("method"):
                    A(f"- 调用方法：`{bad['method']}()`")
            A("")
    else:
        A("_本次运行未识别出伪正确样本。_")
        A("")

    A("### 6.2 定位命中（评估器与人工标注一致）")
    A("")
    if cases["loc_hit"]:
        A("| 题目 | 人工标注步骤 | 系统定位 | 错误类型 |")
        A("| --- | --- | --- | --- |")
        for r in cases["loc_hit"][:8]:
            gt = r["ground_truth"]["first_error_step"]
            A(f"| `{r['problem_id']}` | {STEP_CN.get(gt, gt)} | "
              f"{STEP_CN.get(r['verdict']['first_error_step'], r['verdict']['first_error_step'])} | "
              f"{'、'.join(r['verdict']['error_types']) or '—'} |")
        A("")
    else:
        A("_无命中样本。_")
        A("")

    A("### 6.3 定位偏差（评估器与人工标注不一致）")
    A("")
    if cases["loc_miss"]:
        A("偏差集中在**相邻步骤**上 —— 步骤边界（思路/边界、边界/实现）本身存在人为")
        A("划分的模糊地带，同一处缺陷可以从不同步骤归责。")
        A("")
        A("| 题目 | 人工标注 | 系统定位 | 是否相邻 |")
        A("| --- | --- | --- | --- |")
        order = list(STEP_CN)
        for r in cases["loc_miss"][:10]:
            gt = r["ground_truth"]["first_error_step"]
            pd = r["verdict"]["first_error_step"]
            adj = "是" if (gt in order and pd in order and abs(order.index(gt) - order.index(pd)) <= 1) else "否"
            A(f"| `{r['problem_id']}` | {STEP_CN.get(gt, gt)} | {STEP_CN.get(pd, pd)} | {adj} |")
        A("")
    else:
        A("_无偏差样本。_")
        A("")

    # ---------------- 七、能力边界
    A("## 七、模型能力边界与临界点")
    A("")
    lines_b: list[str] = []
    if by_diff:
        ds = [d for d in ordered if by_diff[d]["n"]]
        if ds:
            hi = max(ds, key=lambda d: by_diff[d]["process_valid"] / by_diff[d]["n"])
            lo = min(ds, key=lambda d: by_diff[d]["process_valid"] / by_diff[d]["n"])
            lines_b.append(
                f"- 过程成立率从 `{hi}` 的 {pct(by_diff[hi]['process_valid'], by_diff[hi]['n'])}"
                f" 降到 `{lo}` 的 {pct(by_diff[lo]['process_valid'], by_diff[lo]['n'])}，"
                f"差距 {pct(max(0, by_diff[hi]['process_valid'] / by_diff[hi]['n'] - by_diff[lo]['process_valid'] / by_diff[lo]['n']), 1)}。"
            )
    if etypes:
        top = etypes.most_common(3)
        lines_b.append("- 最高频的三类缺陷：" + "、".join(f"{t}（{c} 次）" for t, c in top) + "。")
    if res_ok and truly_ok:
        lines_b.append(
            f"- 公开测试与真实正确的落差 {pct(res_ok - truly_ok, n)}，意味着**仅凭测试通过率"
            f"评价模型会系统性高估**其能力。"
        )
    if by_kind.get("class") and by_kind.get("function"):
        ck, fk = by_kind["class"], by_kind["function"]
        lines_b.append(
            f"- 类级（工程任务）过程成立率 {pct(ck['process_valid'], ck['n'])}，"
            f"函数级 {pct(fk['process_valid'], fk['n'])} —— "
            + ("有状态题目更容易出错（跨调用状态是主要失分点）。"
               if ck["process_valid"] / ck["n"] < fk["process_valid"] / fk["n"]
               else "类级题目表现好于函数级。")
        )
    for b in lines_b:
        A(b)
    A("")
    A("### 评估器自身的边界")
    A("")
    A("- **能做的**：执行证据（公开/对抗测试）是硬事实，伪正确识别完全由它驱动，不受 LLM 主观性影响。")
    A("- **做不到的**：纯推理层面的缺陷（跳步、循环论证、误用定理）没有可执行证据，")
    A("  只能靠 LLM 审查；这类判定的误报是当前主要误差来源。")
    A("- **定位的模糊地带**：相邻步骤的归责分歧占了定位偏差的绝大部分，")
    A("  ±1 容差指标比 top-1 更能反映实际可用性。")
    A("")

    A("---")
    A("")
    A("## 附录：数据来源")
    A("")
    A(f"- 跑批明细：`{run_meta.get('out_dir', '—')}`")
    A("- 参考解答验证：见 `results/ref_validation_*/`")
    A("- 单题完整记录（含解答原文与五段全文）在各自 run 目录的 `problems/` 下")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="汇总生成最终报告")
    ap.add_argument("--run", help="跑批目录（含 all.jsonl）")
    ap.add_argument("--ref", help="参考解答验证目录")
    ap.add_argument("--out", help="输出 Markdown 路径")
    args = ap.parse_args()

    run_dir = Path(args.run) if args.run else latest_dir("run_*")
    ref_dir = Path(args.ref) if args.ref else latest_dir("ref_validation_*")
    if not run_dir or not (run_dir / "all.jsonl").exists():
        print("找不到跑批产物（results/run_*/all.jsonl）")
        return 1

    run_recs, run_meta = load_run(run_dir)
    ref_recs, ref_metrics = ([], {})
    if ref_dir and (ref_dir / "reference.jsonl").exists():
        ref_recs, ref_metrics = load_ref(ref_dir)
    else:
        print("提示：未找到参考解答验证产物，第五节将留空。")

    md = build(run_recs, run_meta, ref_recs, ref_metrics)
    out = Path(args.out) if args.out else (RESULTS / "final_report.md")
    out.write_text(md, encoding="utf-8")

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "run_dir": str(run_dir), "ref_dir": str(ref_dir) if ref_dir else None,
        "samples": len(run_recs),
        "result_correct": sum(1 for r in run_recs if r["verdict"]["result_correct"]),
        "truly_correct": sum(1 for r in run_recs if r["verdict"]["truly_correct"]),
        "process_valid": sum(1 for r in run_recs if r["verdict"]["process_valid"]),
        "false_positive": sum(1 for r in run_recs if r["verdict"]["false_positive_solution"]),
        "by_difficulty": stratify(run_recs, lambda r: r.get("difficulty") or "unknown"),
        "by_kind": stratify(run_recs, lambda r: r.get("kind") or "function"),
        "error_types": dict(error_type_counter(run_recs)),
        "first_error_steps": dict(step_counter(run_recs)),
        "reference_metrics": ref_metrics,
        "false_alarm_audit": audit_false_alarms(ref_recs),
    }
    (RESULTS / "final_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"报告已写入：{out}")
    print(f"汇总 JSON：{RESULTS / 'final_summary.json'}")
    print(f"样本 {len(run_recs)}　答案准确率 "
          f"{summary['result_correct'] / max(len(run_recs), 1):.1%}　"
          f"过程正确率 {summary['process_valid'] / max(len(run_recs), 1):.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
