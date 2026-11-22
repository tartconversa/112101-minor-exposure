"""端到端流水线测试：合成数据落地、批次截止、保护桶与重述追踪。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401  # 导入 src/ 路径
from protected_exposure.config import SOURCES_DIR
from protected_exposure.pipeline import run_batch
from protected_exposure.warehouse import (connect, fetch_distributions,
                                          fetch_rules, fetch_signals, load_raw)

RUN_1 = ("RUN-T1", "2026-03-15T00:00:00Z",
         "2026-01-01T00:00:00Z", "2026-03-15T00:00:00Z")
RUN_2 = ("RUN-T2", "2026-05-01T00:00:00Z",
         "2026-01-01T00:00:00Z", "2026-05-01T00:00:00Z")
RUN_3 = ("RUN-T3", "2026-05-15T00:00:00Z",
         "2026-01-01T00:00:00Z", "2026-05-15T00:00:00Z")


class PipelineIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "warehouse.sqlite"
        self.conn = connect(self.db)
        load_raw(self.conn, SOURCES_DIR)
        self.rules = fetch_rules(self.conn)
        self.signals = fetch_signals(self.conn)
        self.events = fetch_distributions(self.conn)
        prior = None
        for spec in (RUN_1, RUN_2, RUN_3):
            run_batch(self.conn, self.rules, self.signals, self.events,
                      *spec, prior, "2026-05-15T12:00:00Z")
            prior = spec[0]

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def _fact(self, run_id: str, event_ref: str):
        return self.conn.execute(
            "SELECT * FROM fact_exposure WHERE run_id=? AND event_ref=?",
            (run_id, event_ref)).fetchone()

    def test_raw_ingestion_counts(self) -> None:
        for table, minimum in (("raw_protection_rules", 8),
                               ("raw_age_signals", 15),
                               ("raw_distributions", 25)):
            count = self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            self.assertGreaterEqual(count, minimum)

    def test_late_records_are_invisible_before_their_ingest(self) -> None:
        # PR-6 在 2026-05-10 才补传，前两个批次不可见。
        for run_id in ("RUN-T1", "RUN-T2"):
            count = self.conn.execute(
                "SELECT COUNT(*) FROM stg_rules WHERE run_id=? AND rule_id='PR-6'",
                (run_id,)).fetchone()[0]
            self.assertEqual(count, 0)
        count = self.conn.execute(
            "SELECT COUNT(*) FROM stg_rules WHERE run_id='RUN-T3' AND rule_id='PR-6'"
        ).fetchone()[0]
        self.assertEqual(count, 1)
        # 事件窗口外的记录不入批次。
        count = self.conn.execute(
            "SELECT COUNT(*) FROM stg_distributions WHERE run_id='RUN-T1'").fetchone()[0]
        self.assertEqual(count, 10)

    def test_each_event_lands_in_exactly_one_bucket_per_run(self) -> None:
        for run_id, expected in (("RUN-T1", 10), ("RUN-T2", 22), ("RUN-T3", 25)):
            count = self.conn.execute(
                "SELECT COUNT(*) FROM fact_exposure WHERE run_id=?",
                (run_id,)).fetchone()[0]
            self.assertEqual(count, expected)
            nulls = self.conn.execute(
                "SELECT COUNT(*) FROM fact_exposure WHERE run_id=? "
                "AND exposure_bucket IS NULL", (run_id,)).fetchone()[0]
            self.assertEqual(nulls, 0)

    def test_unknown_age_is_never_counted_as_adult(self) -> None:
        # SYN-2 从未有有效年龄标签，EV-2 必须进入未知桶而非成人对照桶。
        fact = self._fact("RUN-T1", "EV-2")
        self.assertEqual(fact["age_assessment"], "unknown")
        self.assertIn(fact["exposure_bucket"],
                      ("UNKNOWN_AGE_EXPOSED", "UNKNOWN_AGE_PROTECTED"))

    def test_expired_label_produces_expired_reason(self) -> None:
        fact = self._fact("RUN-T2", "EV-12")
        self.assertEqual(fact["age_reason"], "expired")
        self.assertEqual(fact["exposure_bucket"], "UNKNOWN_AGE_PROTECTED")

    def test_pre_policy_minor_exposure(self) -> None:
        fact = self._fact("RUN-T1", "EV-4")
        self.assertEqual(fact["exposure_bucket"], "MINOR_EXPOSURE")
        self.assertEqual(fact["exposure_reason"], "pre_policy")

    def test_post_withdrawal_exposure(self) -> None:
        fact = self._fact("RUN-T2", "EV-24")
        self.assertEqual(fact["exposure_bucket"], "MINOR_EXPOSURE")
        self.assertEqual(fact["exposure_reason"], "post_withdrawal")
        fact = self._fact("RUN-T2", "EV-13")
        self.assertEqual(fact["exposure_reason"], "post_withdrawal")

    def test_entry_uncovered_region_gap(self) -> None:
        fact = self._fact("RUN-T2", "EV-16")
        self.assertEqual(fact["exposure_bucket"], "MINOR_EXPOSURE")
        self.assertEqual(fact["exposure_reason"], "entry_uncovered")

    def test_restriction_gap_when_policy_applies_but_not_enforced(self) -> None:
        fact = self._fact("RUN-T2", "EV-3")
        self.assertEqual(fact["exposure_bucket"], "MINOR_EXPOSURE")
        self.assertEqual(fact["exposure_reason"], "restriction_gap")

    def test_late_policy_restates_prior_report(self) -> None:
        before = self._fact("RUN-T2", "EV-25")
        after = self._fact("RUN-T3", "EV-25")
        self.assertEqual(before["matched_rule_version"], "R-5")
        self.assertEqual(after["matched_rule_version"], "R-6")
        restatement = self.conn.execute(
            "SELECT * FROM report_restatements WHERE run_id='RUN-T3' AND event_ref='EV-25'"
        ).fetchone()
        self.assertEqual(restatement["change_driver"], "policy_change")

    def test_age_signal_change_restates_prior_report(self) -> None:
        before = self._fact("RUN-T2", "EV-19")
        after = self._fact("RUN-T3", "EV-19")
        self.assertEqual(before["exposure_bucket"], "UNKNOWN_AGE_EXPOSED")
        self.assertEqual(after["exposure_bucket"], "MINOR_EXPOSURE")
        restatement = self.conn.execute(
            "SELECT * FROM report_restatements WHERE run_id='RUN-T3' AND event_ref='EV-19'"
        ).fetchone()
        self.assertIn("age_signal_change", restatement["change_driver"])

    def test_late_event_marked_as_new_event_restatement(self) -> None:
        restatement = self.conn.execute(
            "SELECT * FROM report_restatements WHERE run_id='RUN-T3' AND event_ref='EV-21'"
        ).fetchone()
        self.assertEqual(restatement["change_driver"], "new_event")

    def test_summary_reconciles_with_fact_counts(self) -> None:
        for run_id in ("RUN-T1", "RUN-T2", "RUN-T3"):
            fact_total = self.conn.execute(
                "SELECT COUNT(*) FROM fact_exposure WHERE run_id=?",
                (run_id,)).fetchone()[0]
            summary_total = self.conn.execute(
                "SELECT SUM(event_count) FROM report_summary "
                "WHERE run_id=? AND rule_version='ALL' AND channel='ALL' "
                "AND region='ALL'", (run_id,)).fetchone()[0]
            self.assertEqual(fact_total, summary_total)

    def test_summary_supports_rule_version_and_entry_filtering(self) -> None:
        rows = self.conn.execute(
            "SELECT * FROM report_summary WHERE run_id='RUN-T2' "
            "AND rule_version='R-4' AND channel='feed' AND region='ALL'").fetchall()
        self.assertTrue(rows)
        self.assertTrue(all(r["event_count"] > 0 for r in rows))

    def test_synthetic_identifiers_only(self) -> None:
        # 全库主体标识必须是 SYN-N 形式，防止真实个人资料混入。
        subjects = {r[0] for r in self.conn.execute(
            "SELECT DISTINCT subject_key FROM raw_distributions").fetchall()}
        self.assertTrue(subjects)
        for subject in subjects:
            self.assertRegex(subject, r"^SYN-\d+$")


if __name__ == "__main__":
    unittest.main()
