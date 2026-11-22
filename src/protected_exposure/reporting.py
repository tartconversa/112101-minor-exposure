"""面向保护团队与运营团队的复核查询。

所有查询都以报告批次（run_id）为第一过滤条件，再支持
规则版本、事件时间窗口与入口（channel）下钻。
"""

from __future__ import annotations

import sqlite3
from typing import Any


def _filters(run_id: str, rule_version: str | None, channel: str | None,
             start: str | None, end: str | None) -> tuple[str, list[Any]]:
    where = ["run_id = ?"]
    params: list[Any] = [run_id]
    if rule_version:
        where.append("COALESCE(matched_rule_version, 'NONE') = ?")
        params.append(rule_version)
    if channel:
        where.append("channel = ?")
        params.append(channel)
    if start:
        where.append("occurred_at >= ?")
        params.append(start)
    if end:
        where.append("occurred_at < ?")
        params.append(end)
    return " AND ".join(where), params


def query_facts(conn: sqlite3.Connection, run_id: str, *,
                rule_version: str | None = None, channel: str | None = None,
                start: str | None = None, end: str | None = None,
                bucket: str | None = None) -> list[sqlite3.Row]:
    clauses, params = _filters(run_id, rule_version, channel, start, end)
    if bucket:
        clauses += " AND exposure_bucket = ?"
        params.append(bucket)
    return conn.execute(
        f"SELECT * FROM fact_exposure WHERE {clauses} "
        "ORDER BY occurred_at, event_ref", params).fetchall()


def bucket_totals(conn: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT exposure_bucket, COUNT(*) AS n FROM fact_exposure "
        "WHERE run_id = ? GROUP BY exposure_bucket ORDER BY exposure_bucket",
        (run_id,)).fetchall()


def summary_grid(conn: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    """规则版本 × 入口 × 桶的汇总（仅明细粒度，ALL 行保留在表中供其他查询）。"""
    return conn.execute(
        "SELECT rule_version, channel, region, exposure_bucket, event_count "
        "FROM report_summary WHERE run_id = ? AND region = 'ALL' "
        "AND channel <> 'ALL' ORDER BY rule_version, channel, exposure_bucket",
        (run_id,)).fetchall()


def restatements(conn: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM report_restatements WHERE run_id = ? "
        "ORDER BY change_driver, event_ref", (run_id,)).fetchall()


def list_runs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM report_runs ORDER BY cutoff_at").fetchall()
