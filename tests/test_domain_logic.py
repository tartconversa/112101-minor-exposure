"""领域判定规则的单元测试：年龄信号、策略匹配、保护桶分类。"""

from __future__ import annotations

import unittest
from datetime import datetime

import _bootstrap  # noqa: F401  # 导入 src/ 路径
from protected_exposure.domain import (assess_age, band_contains, classify_event,
                                       match_rule, parse_age_band)
from protected_exposure.time_utils import parse_ts


def ts(text: str) -> datetime:
    return parse_ts(text)


def signal(record_id: str, subject: str, band: str | None, state: str,
           valid_from: str, valid_to: str | None,
           observed_at: str = "2026-02-01T00:00:00Z") -> dict:
    return {
        "record_id": record_id, "signal_id": record_id, "subject_key": subject,
        "age_band": band, "signal_state": state,
        "valid_from": ts(valid_from), "valid_to": ts(valid_to),
        "observed_at": ts(observed_at), "region": "CN",
        "ingest_at": ts("2026-02-01T00:00:00Z"),
    }


def rule(record_id: str, rule_id: str, band: str, channel: str = "feed",
         region: str = "CN", version: str = "R-1",
         effective_from: str = "2026-02-01T00:00:00Z",
         effective_to: str | None = None) -> dict:
    return {
        "record_id": record_id, "rule_id": rule_id, "age_band": band,
        "channel": channel, "region": region, "rule_version": version,
        "effective_from": ts(effective_from),
        "effective_to": ts(effective_to),
        "ingest_at": ts("2026-02-01T00:00:00Z"),
    }


def event(channel: str = "feed", region: str = "CN",
          moment: str = "2026-03-01T00:00:00Z",
          restriction: str = "limited") -> dict:
    return {
        "event_ref": "EV-T", "channel": channel, "region": region,
        "occurred_at": ts(moment), "restriction_state": restriction,
        "ingest_at": ts(moment),
    }


class AgeBandTests(unittest.TestCase):
    def test_parse_and_contains(self) -> None:
        self.assertEqual(parse_age_band("13-15"), (13, 15))
        self.assertTrue(band_contains("13-17", "13-15"))
        self.assertFalse(band_contains("13-15", "16-17"))


class AgeAssessmentTests(unittest.TestCase):
    def test_verified_minor(self) -> None:
        result = assess_age([signal("s1", "SYN-1", "13-15", "verified",
                                    "2026-02-01", "2026-05-01")],
                            ts("2026-03-01T00:00:00Z"))
        self.assertEqual(result["assessment"], "minor_confirmed")
        self.assertEqual(result["reason"], "ok")

    def test_verified_adult_reference(self) -> None:
        result = assess_age([signal("s1", "SYN-1", "18-24", "verified",
                                    "2026-02-01", None)],
                            ts("2026-03-01T00:00:00Z"))
        self.assertEqual(result["assessment"], "adult_confirmed")

    def test_missing_label_is_unknown_not_adult(self) -> None:
        result = assess_age([], ts("2026-03-01T00:00:00Z"))
        self.assertEqual(result["assessment"], "unknown")
        self.assertEqual(result["reason"], "missing")

    def test_expired_label_is_unknown_not_adult(self) -> None:
        result = assess_age([signal("s1", "SYN-1", "13-15", "verified",
                                    "2026-01-01", "2026-02-01")],
                            ts("2026-03-01T00:00:00Z"))
        self.assertEqual(result["assessment"], "unknown")
        self.assertEqual(result["reason"], "expired")

    def test_unknown_state_label(self) -> None:
        result = assess_age([signal("s1", "SYN-1", None, "unknown",
                                    "2026-02-01", "2026-03-15")],
                            ts("2026-03-01T00:00:00Z"))
        self.assertEqual(result["assessment"], "unknown")
        self.assertEqual(result["reason"], "missing")

    def test_pending_correction_is_unknown(self) -> None:
        result = assess_age([signal("s1", "SYN-1", None, "pending_correction",
                                    "2026-02-01", None)],
                            ts("2026-03-01T00:00:00Z"))
        self.assertEqual(result["reason"], "pending_correction")

    def test_disputed_label_is_unknown(self) -> None:
        result = assess_age([signal("s1", "SYN-1", None, "disputed",
                                    "2026-05-02", None)],
                            ts("2026-05-05T00:00:00Z"))
        self.assertEqual(result["assessment"], "unknown")
        self.assertEqual(result["reason"], "disputed")

    def test_correction_overrides_older_signal_when_overlapping(self) -> None:
        old = signal("s1", "SYN-1", "13-15", "verified", "2026-02-01", None,
                     observed_at="2026-02-01T08:00:00Z")
        new = signal("s2", "SYN-1", "16-17", "corrected", "2026-04-18", None,
                     observed_at="2026-04-20T09:00:00Z")
        result = assess_age([old, new], ts("2026-04-25T00:00:00Z"))
        self.assertEqual(result["age_band"], "16-17")
        self.assertEqual(result["signal_record_id"], "s2")

    def test_correction_restates_history_when_validity_reaches_back(self) -> None:
        # 纠正信号的有效期追溯覆盖 2026-03-01：历史事件按纠正后口径重述，
        # 这正是重述追踪要捕获的年龄信号变更。
        old = signal("s1", "SYN-1", "13-15", "verified", "2026-02-01", "2026-05-01",
                     observed_at="2026-02-01T08:00:00Z")
        retroactive = signal("s2", "SYN-1", "16-17", "corrected", "2026-03-01", None,
                             observed_at="2026-04-20T09:00:00Z")
        result = assess_age([old, retroactive], ts("2026-03-20T00:00:00Z"))
        self.assertEqual(result["age_band"], "16-17")
        self.assertEqual(result["signal_record_id"], "s2")

    def test_signal_not_valid_at_event_time_is_ignored(self) -> None:
        # 纠正信号 2026-04-18 才生效，不追溯历史：历史事件仍用旧信号。
        old = signal("s1", "SYN-1", "13-15", "verified", "2026-02-01", "2026-05-01",
                     observed_at="2026-02-01T08:00:00Z")
        new = signal("s2", "SYN-1", "16-17", "corrected", "2026-04-18", None,
                     observed_at="2026-04-20T09:00:00Z")
        result = assess_age([old, new], ts("2026-04-01T00:00:00Z"))
        self.assertEqual(result["age_band"], "13-15")
        self.assertEqual(result["signal_record_id"], "s1")


class RuleMatchingTests(unittest.TestCase):
    def test_active_rule_match(self) -> None:
        rules = [rule("r1", "PR-1", "13-15")]
        match = match_rule(rules, event(), "13-15")
        self.assertEqual(match["status"], "active")
        self.assertEqual(match["rule_id"], "PR-1")

    def test_pre_policy_is_distinguished(self) -> None:
        rules = [rule("r1", "PR-1", "13-15", effective_from="2026-02-01T00:00:00Z")]
        match = match_rule(rules, event(moment="2026-01-25T00:00:00Z"), "13-15")
        self.assertEqual(match["status"], "not_yet_effective")
        self.assertEqual(match["reason"], "pre_policy")

    def test_post_withdrawal(self) -> None:
        rules = [rule("r1", "PR-1", "13-15",
                      effective_from="2026-04-01T00:00:00Z",
                      effective_to="2026-04-20T00:00:00Z")]
        match = match_rule(rules, event(moment="2026-04-22T00:00:00Z"), "13-15")
        self.assertEqual(match["status"], "withdrawn")
        self.assertEqual(match["reason"], "post_withdrawal")

    def test_entry_uncovered_vs_no_rule(self) -> None:
        rules = [rule("r1", "PR-1", "13-15", channel="feed")]
        uncovered = match_rule(rules, event(channel="search"), "13-15")
        self.assertEqual(uncovered["reason"], "no_applicable_rule")

        rules.append(rule("r2", "PR-2", "13-17", channel="search"))
        gap = match_rule(rules, event(channel="search"), "13-15")
        self.assertEqual(gap["status"], "active")

    def test_region_scope_is_enforced(self) -> None:
        rules = [rule("r1", "PR-5", "13-15", channel="search", region="CN-S")]
        match = match_rule(rules, event(channel="search", region="CN"), "13-15")
        self.assertEqual(match["status"], "no_match")

    def test_narrower_band_wins(self) -> None:
        rules = [
            rule("r1", "PR-1", "13-17", version="R-1"),
            rule("r2", "PR-2", "13-15", version="R-1"),
        ]
        match = match_rule(rules, event(), "13-15")
        self.assertEqual(match["rule_id"], "PR-2")


class BucketClassificationTests(unittest.TestCase):
    def _age(self, assessment: str, band: str | None = None,
             reason: str = "ok") -> dict:
        return {"assessment": assessment, "age_band": band,
                "reason": reason, "signal_record_id": "s1"}

    def test_minor_with_restriction_is_protected(self) -> None:
        verdict = classify_event(event(restriction="blocked"),
                                 self._age("minor_confirmed", "13-15"),
                                 {"rule_id": "PR-1", "rule_version": "R-1",
                                  "status": "active", "reason": None})
        self.assertEqual(verdict["exposure_bucket"], "PROTECTED_MINOR")

    def test_minor_without_restriction_is_exposure_not_zero(self) -> None:
        verdict = classify_event(event(restriction="served"),
                                 self._age("minor_confirmed", "13-15"),
                                 {"rule_id": "PR-1", "rule_version": "R-1",
                                  "status": "active", "reason": None})
        self.assertEqual(verdict["exposure_bucket"], "MINOR_EXPOSURE")
        self.assertEqual(verdict["exposure_reason"], "restriction_gap")

    def test_unknown_age_never_defaults_to_adult(self) -> None:
        verdict = classify_event(event(restriction="served"),
                                 self._age("unknown", reason="expired"),
                                 {"rule_id": None, "rule_version": None,
                                  "status": "no_match",
                                  "reason": "no_applicable_rule"})
        self.assertEqual(verdict["exposure_bucket"], "UNKNOWN_AGE_EXPOSED")
        self.assertNotEqual(verdict["exposure_bucket"], "ADULT_REFERENCE")

    def test_unknown_age_with_restriction_is_conservative_protection(self) -> None:
        verdict = classify_event(event(restriction="limited"),
                                 self._age("unknown", reason="missing"),
                                 {"rule_id": "PR-1", "rule_version": "R-1",
                                  "status": "active", "reason": None})
        self.assertEqual(verdict["exposure_bucket"], "UNKNOWN_AGE_PROTECTED")

    def test_adult_is_separate_reference_bucket(self) -> None:
        verdict = classify_event(event(restriction="served"),
                                 self._age("adult_confirmed", "18-24"),
                                 {"rule_id": None, "rule_version": None,
                                  "status": "no_match", "reason": None})
        self.assertEqual(verdict["exposure_bucket"], "ADULT_REFERENCE")


if __name__ == "__main__":
    unittest.main()
