"""时点判定与保护桶分类的纯函数领域逻辑。

两条不可违背的原则：
1. 设备年龄标签缺失、过期或用户主动纠正/争议时，一律判定为 unknown，
   绝不能默认按成人处理。
2. unknown 人群单独成桶，既不计为零，也不计为准确的未成年人数量。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .config import RESTRICTING_STATES
from .time_utils import as_of_window


def parse_age_band(band: str) -> tuple[int, int]:
    """把 '13-15' 解析为闭区间整数年龄。"""
    lo_text, hi_text = band.split("-", 1)
    return int(lo_text), int(hi_text)


def band_contains(rule_band: str, subject_band: str) -> bool:
    """规则年龄段是否覆盖主体年龄段。"""
    r_lo, r_hi = parse_age_band(rule_band)
    s_lo, s_hi = parse_age_band(subject_band)
    return r_lo <= s_lo and s_hi <= r_hi


def assess_age(signals: list[dict[str, Any]], moment: datetime) -> dict[str, Any]:
    """按事件时间 moment 判定主体年龄状态。

    返回 assessment / age_band / reason / signal_record_id。
    只依据 moment 时点有效的信号；信号有效期外一律不可信。
    """
    covering = [s for s in signals
                if as_of_window(s["valid_from"], s["valid_to"], moment)]
    # 重叠时采用观察时间最新的信号（纠正信号覆盖旧信号）。
    covering.sort(key=lambda s: s["observed_at"], reverse=True)

    if covering:
        signal = covering[0]
        state = signal["signal_state"]
        band = signal["age_band"]
        if state in ("verified", "corrected") and band:
            assessment = "minor_confirmed" if _is_minor(band) else "adult_confirmed"
            return {"assessment": assessment, "age_band": band,
                    "reason": "ok", "signal_record_id": signal["record_id"]}
        reason = {
            "unknown": "missing",
            "pending_correction": "pending_correction",
            "disputed": "disputed",
        }.get(state, "missing")
        return {"assessment": "unknown", "age_band": None,
                "reason": reason, "signal_record_id": signal["record_id"]}

    # 没有时点有效信号：区分"从未有过标签"与"标签过期"。
    if signals:
        return {"assessment": "unknown", "age_band": None,
                "reason": "expired", "signal_record_id": None}
    return {"assessment": "unknown", "age_band": None,
            "reason": "missing", "signal_record_id": None}


def _is_minor(band: str) -> bool:
    _, hi = parse_age_band(band)
    return hi < 18


def _scope_compatible(rule: dict[str, Any], event: dict[str, Any],
                      subject_band: str | None) -> bool:
    """规则的入口/地区/年龄段是否与事件兼容（不考虑时间）。"""
    if rule["region"] != event["region"] or rule["channel"] != event["channel"]:
        return False
    if subject_band is None:
        return True  # 年龄未知时保留所有同入口/地区规则作为上下文
    return band_contains(rule["age_band"], subject_band)


def match_rule(rules: list[dict[str, Any]], event: dict[str, Any],
               subject_band: str | None) -> dict[str, Any]:
    """按事件时间匹配保护策略，并区分生效/撤回/未覆盖。

    返回 rule_id / rule_version / status / reason。
    规则在进入本函数前已按报告截止时间过滤（迟到规则不可见）。
    """
    moment = event["occurred_at"]
    compatible = [r for r in rules if _scope_compatible(r, event, subject_band)]

    active = [r for r in compatible
              if as_of_window(r["effective_from"], r["effective_to"], moment)]
    if active:
        # 年龄段最窄（最具体）优先，其次取最高规则版本。
        active.sort(key=lambda r: (parse_age_band(r["age_band"])[1]
                                   - parse_age_band(r["age_band"])[0],
                                   r["rule_version"]))
        chosen = active[0]
        return {"rule_id": chosen["rule_id"],
                "rule_version": chosen["rule_version"],
                "status": "active", "reason": None}

    withdrawn = [r for r in compatible if r["effective_to"] is not None
                 and r["effective_to"] <= moment]
    if withdrawn:
        chosen = max(withdrawn, key=lambda r: r["effective_to"])
        return {"rule_id": chosen["rule_id"],
                "rule_version": chosen["rule_version"],
                "status": "withdrawn", "reason": "post_withdrawal"}

    future = [r for r in compatible if r["effective_from"] > moment]
    if future:
        chosen = min(future, key=lambda r: r["effective_from"])
        return {"rule_id": chosen["rule_id"],
                "rule_version": chosen["rule_version"],
                "status": "not_yet_effective", "reason": "pre_policy"}

    # 同入口+地区存在其他年龄段规则 => 覆盖范围缺口；否则该入口本就无策略。
    same_entry = [r for r in rules
                  if r["channel"] == event["channel"] and r["region"] == event["region"]]
    reason = "entry_uncovered" if same_entry else "no_applicable_rule"
    return {"rule_id": None, "rule_version": None,
            "status": "no_match", "reason": reason}


def classify_event(event: dict[str, Any], age: dict[str, Any],
                   rule: dict[str, Any]) -> dict[str, Any]:
    """汇总年龄判定与策略匹配，给出唯一保护桶与暴露原因。"""
    restricted = event["restriction_state"] in RESTRICTING_STATES
    assessment = age["assessment"]

    if assessment == "adult_confirmed":
        bucket = "ADULT_REFERENCE"
        reason = None
    elif assessment == "unknown":
        bucket = "UNKNOWN_AGE_PROTECTED" if restricted else "UNKNOWN_AGE_EXPOSED"
        # 未知年龄下保留策略上下文原因，说明暴露无法确认的具体形态。
        reason = None if restricted else (rule["reason"] or "restriction_gap")
    else:  # minor_confirmed
        if restricted:
            bucket = "PROTECTED_MINOR"
            reason = None
        else:
            bucket = "MINOR_EXPOSURE"
            reason = rule["reason"] or "restriction_gap"

    return {
        "exposure_bucket": bucket,
        "exposure_reason": reason,
        "restriction_applied": 1 if restricted else 0,
        "matched_rule_id": rule["rule_id"],
        "matched_rule_version": rule["rule_version"],
        "rule_status": rule["status"],
    }
