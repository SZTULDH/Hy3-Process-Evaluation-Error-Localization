"""评估器自身的有效性验证（对应 PROPOSAL 第 9 节）。

衡量的是**评估器**准不准，而不是模型解题准不准：
* 定位准确率 —— 在“过程确实有错”的样本上，第一个错误步骤定得对不对
* 相邻容差准确率 —— 允许差一个步骤（思路/复杂度、边界/实现的责任边界
  本身就存在人为划定的模糊地带，只看 top-1 会低估实用性）
* 误报率 —— 在“过程确实成立”的样本上，有多少被冤枉成有错
* 伪正确识别率 —— 真正的看家指标：公开测试通过但逻辑有缺陷的样本抓到几个
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import SECTION_TITLES, STEP_LABELS

STEP_ORDER = [STEP_LABELS[t] for t in SECTION_TITLES]


def _step_index(step_id: str | None) -> int | None:
    if not step_id:
        return None
    return STEP_ORDER.index(step_id) if step_id in STEP_ORDER else None


@dataclass
class ValidationReport:
    total: int = 0
    gt_flawed: int = 0
    gt_clean: int = 0

    localization_top1: int = 0
    localization_tolerant: int = 0
    localization_details: list[dict] = field(default_factory=list)

    false_alarms: int = 0
    false_alarm_details: list[dict] = field(default_factory=list)

    pseudo_correct_total: int = 0
    pseudo_correct_caught: int = 0
    pseudo_correct_details: list[dict] = field(default_factory=list)

    process_judgement_correct: int = 0

    @property
    def localization_accuracy(self) -> float:
        return self.localization_top1 / self.gt_flawed if self.gt_flawed else 0.0

    @property
    def localization_accuracy_tolerant(self) -> float:
        return self.localization_tolerant / self.gt_flawed if self.gt_flawed else 0.0

    @property
    def false_alarm_rate(self) -> float:
        return self.false_alarms / self.gt_clean if self.gt_clean else 0.0

    @property
    def pseudo_correct_recall(self) -> float:
        return (
            self.pseudo_correct_caught / self.pseudo_correct_total
            if self.pseudo_correct_total
            else 0.0
        )

    @property
    def process_judgement_accuracy(self) -> float:
        return self.process_judgement_correct / self.total if self.total else 0.0

    def to_dict(self) -> dict:
        return {
            "total_samples": self.total,
            "ground_truth_flawed": self.gt_flawed,
            "ground_truth_clean": self.gt_clean,
            "localization_accuracy_top1": round(self.localization_accuracy, 3),
            "localization_accuracy_tolerant": round(self.localization_accuracy_tolerant, 3),
            "localization_details": self.localization_details,
            "false_alarm_rate": round(self.false_alarm_rate, 3),
            "false_alarm_count": self.false_alarms,
            "false_alarm_details": self.false_alarm_details,
            "pseudo_correct_total": self.pseudo_correct_total,
            "pseudo_correct_caught": self.pseudo_correct_caught,
            "pseudo_correct_recall": round(self.pseudo_correct_recall, 3),
            "pseudo_correct_details": self.pseudo_correct_details,
            "process_judgement_accuracy": round(self.process_judgement_accuracy, 3),
        }


def validate(records: list[dict]) -> ValidationReport:
    """records: 每项含 evaluation 结果与对应题目的 ground_truth。"""
    rep = ValidationReport()
    rep.total = len(records)

    for rec in records:
        ev = rec["evaluation"]
        gt = rec["problem"].get("ground_truth") or {}
        gt_flawed = not gt.get("process_valid", True)
        gt_step = gt.get("first_error_step")
        pred_step = ev["first_error_step"]

        # ---- 过程是否成立的整体判断
        if bool(ev["process_valid"]) == bool(gt.get("process_valid", True)):
            rep.process_judgement_correct += 1

        # ---- 定位准确率 / 误报率
        if gt_flawed:
            rep.gt_flawed += 1
            gi, pi = _step_index(gt_step), _step_index(pred_step)
            hit = pred_step is not None and pred_step == gt_step
            tolerant = (
                hit or (gi is not None and pi is not None and abs(gi - pi) <= 1)
            )
            if hit:
                rep.localization_top1 += 1
            if tolerant:
                rep.localization_tolerant += 1
            rep.localization_details.append(
                {
                    "problem_id": ev["problem_id"],
                    "ground_truth": gt_step,
                    "predicted": pred_step,
                    "top1_hit": hit,
                    "tolerant_hit": tolerant,
                }
            )
        else:
            rep.gt_clean += 1
            if not ev["process_valid"]:
                rep.false_alarms += 1
                rep.false_alarm_details.append(
                    {
                        "problem_id": ev["problem_id"],
                        "predicted_step": pred_step,
                        "predicted_types": ev["error_types"],
                    }
                )

        # ---- 伪正确识别率
        is_pseudo = (
            gt.get("result_correct") is True and gt.get("truly_correct") is False
        )
        if is_pseudo:
            rep.pseudo_correct_total += 1
            caught = bool(ev["false_positive_solution"])
            if caught:
                rep.pseudo_correct_caught += 1
            rep.pseudo_correct_details.append(
                {
                    "problem_id": ev["problem_id"],
                    "public_passed": ev["public"]["all_passed"],
                    "adversarial_failed": ev["adversarial"]["failed"],
                    "caught": caught,
                }
            )

    return rep
