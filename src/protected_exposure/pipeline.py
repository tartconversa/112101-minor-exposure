"""报告批次流水线。

每个批次 (report run) 固定一个 as-of 截止时间：
- 仅纳入 ingest_at <= cutoff 的记录（迟到/补传数据在更早批次中不可见）；
- 事件时间窗口 [window_start, window_end) 外的事件不纳入；
- 逐条计算年龄判定、策略匹配与保护桶，结果整体保留，
  后续批次不覆盖历史批次，从而可按规则版本/时间窗口/入口复核与重述。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from .domain import assess_age, classify_event, match_rule
from .time_utils import iso, parse_ts


def _visible(rows: list[dict[str, Any]], cutoff: datetime) -> list[dict[str, Any]]:
    return [r for r in rows if r["ingest_at"] <= cutoff]


def _write_snapshots(conn: sqlite3.Connection, run_id: str,
                     rules: list[dict[str, Any]], signals: list[dict[str, Any]],
                     events: list[dict[str, Any]]) -> None:
    conn.executemany(
        "INSERT INTO stg_rules VALUES (?,?,?,?,?,?,?,?,?)",
        [(run_id, r["record_id"], r["rule_id"], r["age_band"], r["region"],
          r["channel"], iso(r["effective_from"]), iso(r["effective_to"]),
          r["rule_version"]) for r in rules])
    conn.executemany(
        "INSERT INTO stg_age_signals VALUES (?,?,?,?,?,?,?,?,?)",
        [(run_id, s["record_id"], s["signal_id"], s["subject_key"], s["age_band"],
          iso(s["valid_from"]), iso(s["valid_to"]), s["signal_state"], s["region"])
         for s in signals])
    conn.executemany(
        "INSERT INTO stg_distributions VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(run_id, e["record_id"], e["distribution_id"], e["content_id"],
          e["subject_key"], e["channel"], iso(e["occurred_at"]),
          e["restriction_state"], e["event_ref"], e["region"]) for e in events])


def _build_facts(conn: sqlite3.Connection, run_id: str,
                 rules: list[dict[str, Any]], signals: list[dict[str, Any]],
                 events: list[dict[str, Any]]) -> None:
    signals_by_subject: dict[str, list[dict[str, Any]]] = {}
    for signal in signals:
        signals_by_subject.setdefault(signal["subject_key"], []).append(signal)

    rows = []
    for event in events:
        subject_signals = signals_by_subject.get(event["subject_key"], [])
        age = assess_age(subject_signals, event["occurred_at"])
        rule = match_rule(rules, event, age["age_band"])
        verdict = classify_event(event, age, rule)
        rows.append((
            run_id, event["event_ref"], event["distribution_id"], event["content_id"],
            event["subject_key"], event["channel"], event["region"],
            iso(event["occurred_at"]), event["restriction_state"],
            age["assessment"], age["age_band"], age["reason"], age["signal_record_id"],
            verdict["matched_rule_id"], verdict["matched_rule_version"],
            verdict["rule_status"], verdict["restriction_applied"],
            verdict["exposure_bucket"], verdict["exposure_reason"],
        ))

    conn.executemany(
        "INSERT INTO fact_exposure VALUES ("
        "?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)


def _build_summary(conn: sqlite3.Connection, run_id: str) -> None:
    """按 规则版本 × 入口 × 地区 × 保护桶 汇总，并附 ALL 汇总行。"""
    facts = conn.execute(
        "SELECT * FROM fact_exposure WHERE run_id = ?", (run_id,)).fetchall()

    keys: set[tuple[str, str, str, str]] = set()
    counts: dict[tuple[str, str, str, str], int] = {}
    for fact in facts:
        rule_version = fact["matched_rule_version"] or "NONE"
        for combo in (
            (rule_version, fact["channel"], fact["region"], fact["exposure_bucket"]),
            (rule_version, fact["channel"], "ALL", fact["exposure_bucket"]),
            (rule_version, "ALL", fact["region"], fact["exposure_bucket"]),
            (rule_version, "ALL", "ALL", fact["exposure_bucket"]),
            ("ALL", fact["channel"], fact["region"], fact["exposure_bucket"]),
            ("ALL", fact["channel"], "ALL", fact["exposure_bucket"]),
            ("ALL", "ALL", fact["region"], fact["exposure_bucket"]),
            ("ALL", "ALL", "ALL", fact["exposure_bucket"]),
        ):
            keys.add(combo)
            counts[combo] = counts.get(combo, 0) + 1

    conn.executemany(
        "INSERT INTO report_summary VALUES (?,?,?,?,?,?)",
        [(run_id, rv, ch, rg, bucket, counts[(rv, ch, rg, bucket)])
         for rv, ch, rg, bucket in sorted(keys)])


def _build_restatements(conn: sqlite3.Connection, run_id: str,
                        prior_run_id: str | None) -> None:
    """与上一批次逐条对比，追踪年龄信号/策略变更对先前报告的影响。"""
    if prior_run_id is None:
        return
    prior = {r["event_ref"]: r for r in conn.execute(
        "SELECT * FROM fact_exposure WHERE run_id = ?", (prior_run_id,)).fetchall()}
    current = {r["event_ref"]: r for r in conn.execute(
        "SELECT * FROM fact_exposure WHERE run_id = ?", (run_id,)).fetchall()}

    statements = []
    for event_ref, cur in sorted(current.items()):
        old = prior.get(event_ref)
        if old is None:
            statements.append((
                run_id, prior_run_id, event_ref, "(none)", cur["exposure_bucket"],
                None, cur["matched_rule_version"], "new_event",
                "该事件在先前批次截止后才到达（迟到补传）"))
            continue

        drivers = []
        if old["age_assessment"] != cur["age_assessment"] or old["age_reason"] != cur["age_reason"]:
            drivers.append("age_signal_change")
        if (old["matched_rule_version"], old["rule_status"]) != (
                cur["matched_rule_version"], cur["rule_status"]):
            drivers.append("policy_change")
        if not drivers:
            continue

        detail_parts = []
        if "age_signal_change" in drivers:
            detail_parts.append(
                f"年龄判定 {old['age_assessment']}/{old['age_reason']} -> "
                f"{cur['age_assessment']}/{cur['age_reason']}")
        if "policy_change" in drivers:
            detail_parts.append(
                f"策略上下文 {old['matched_rule_version'] or 'NONE'}/"
                f"{old['rule_status']} -> "
                f"{cur['matched_rule_version'] or 'NONE'}/{cur['rule_status']}")

        statements.append((
            run_id, prior_run_id, event_ref,
            old["exposure_bucket"], cur["exposure_bucket"],
            old["matched_rule_version"], cur["matched_rule_version"],
            "+".join(drivers), "；".join(detail_parts)))

    # 先前批次存在、当前批次消失（理论上窗口不变时不会发生，保留以防数据回退）。
    for event_ref, old in sorted(prior.items()):
        if event_ref not in current:
            statements.append((
                run_id, prior_run_id, event_ref, old["exposure_bucket"], "(removed)",
                old["matched_rule_version"], None, "data_withdrawal",
                "事件在当前批次不可见"))

    conn.executemany(
        "INSERT OR REPLACE INTO report_restatements VALUES (?,?,?,?,?,?,?,?,?)",
        statements)


def run_batch(conn: sqlite3.Connection, all_rules: list[dict[str, Any]],
              all_signals: list[dict[str, Any]], all_events: list[dict[str, Any]],
              run_id: str, cutoff_text: str, window_start_text: str,
              window_end_text: str, prior_run_id: str | None,
              created_at: str) -> dict[str, int]:
    """执行一个完整报告批次并返回关键计数。"""
    cutoff = parse_ts(cutoff_text)
    window_start = parse_ts(window_start_text)
    window_end = parse_ts(window_end_text)

    rules = _visible(all_rules, cutoff)
    signals = _visible(all_signals, cutoff)
    events = [e for e in _visible(all_events, cutoff)
              if window_start <= e["occurred_at"] < window_end]

    conn.execute(
        "INSERT OR REPLACE INTO report_runs VALUES (?,?,?,?,?)",
        (run_id, cutoff_text, window_start_text, window_end_text, created_at))
    for table, key in (("report_restatements", "run_id"), ("stg_rules", "run_id"),
                       ("stg_age_signals", "run_id"), ("stg_distributions", "run_id"),
                       ("fact_exposure", "run_id"), ("report_summary", "run_id")):
        conn.execute(f"DELETE FROM {table} WHERE {key} = ?", (run_id,))

    _write_snapshots(conn, run_id, rules, signals, events)
    _build_facts(conn, run_id, rules, signals, events)
    _build_summary(conn, run_id)
    _build_restatements(conn, run_id, prior_run_id)
    conn.commit()

    bucket_counts = dict(conn.execute(
        "SELECT exposure_bucket, COUNT(*) FROM fact_exposure "
        "WHERE run_id = ? GROUP BY exposure_bucket", (run_id,)).fetchall())
    return {
        "run_id": run_id,
        "events": len(events),
        "rules_visible": len(rules),
        "signals_visible": len(signals),
        "restatements": conn.execute(
            "SELECT COUNT(*) FROM report_restatements WHERE run_id = ?",
            (run_id,)).fetchone()[0],
        **bucket_counts,
    }
