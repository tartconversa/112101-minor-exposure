"""SQLite 连接管理与 raw 层类型化落地。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .schema import DDL, VIEWS
from .time_utils import iso, parse_ts


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(DDL)
    conn.executescript(VIEWS)
    return conn


def _load_csv(path: Path) -> list[dict[str, str]]:
    import csv

    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def load_raw(conn: sqlite3.Connection, sources_dir: Path) -> dict[str, int]:
    """把三份合成 CSV 落地到 raw_* 表（全量替换，保持可重复执行）。"""
    counts: dict[str, int] = {}

    rules = _load_csv(sources_dir / "protection_rules.csv")
    conn.execute("DELETE FROM raw_protection_rules")
    conn.executemany(
        "INSERT INTO raw_protection_rules VALUES (?,?,?,?,?,?,?,?,?)",
        [(r["record_id"], r["rule_id"], r["age_band"], r["region"], r["channel"],
          iso(parse_ts(r["effective_from"])), iso(parse_ts(r["effective_to"])),
          r["rule_version"], iso(parse_ts(r["ingest_at"]))) for r in rules])
    counts["raw_protection_rules"] = len(rules)

    signals = _load_csv(sources_dir / "age_signals.csv")
    conn.execute("DELETE FROM raw_age_signals")
    conn.executemany(
        "INSERT INTO raw_age_signals VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(s["record_id"], s["signal_id"], s["subject_key"], s["age_band"] or None,
          iso(parse_ts(s["observed_at"])), iso(parse_ts(s["valid_from"])),
          iso(parse_ts(s["valid_to"])), s["signal_state"], s["region"],
          iso(parse_ts(s["ingest_at"]))) for s in signals])
    counts["raw_age_signals"] = len(signals)

    distributions = _load_csv(sources_dir / "distributions.csv")
    conn.execute("DELETE FROM raw_distributions")
    conn.executemany(
        "INSERT INTO raw_distributions VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(d["record_id"], d["distribution_id"], d["content_id"], d["subject_key"],
          d["channel"], iso(parse_ts(d["occurred_at"])), d["restriction_state"],
          d["event_ref"], d["region"], iso(parse_ts(d["ingest_at"])))
         for d in distributions])
    counts["raw_distributions"] = len(distributions)

    _load_dim_entry(conn, distributions)
    conn.commit()
    return counts


def _load_dim_entry(conn: sqlite3.Connection,
                    distributions: Iterable[dict[str, str]]) -> None:
    """入口维度由合成数据约定生成：演示中的入口均为青少年可能进入的入口。"""
    names = {
        "feed": "主推荐流",
        "search": "搜索结果",
        "short_video": "短视频推荐",
    }
    channels = sorted({d["channel"] for d in distributions})
    conn.execute("DELETE FROM dim_entry")
    conn.executemany(
        "INSERT INTO dim_entry VALUES (?,?,?)",
        [(ch, names.get(ch, ch), 1) for ch in channels])


def fetch_rules(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM raw_protection_rules").fetchall()
    return [_rule_row(r) for r in rows]


def fetch_signals(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM raw_age_signals").fetchall()
    return [_signal_row(r) for r in rows]


def fetch_distributions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM raw_distributions").fetchall()
    return [_event_row(r) for r in rows]


def _rule_row(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "record_id": r["record_id"], "rule_id": r["rule_id"],
        "age_band": r["age_band"], "region": r["region"],
        "channel": r["channel"],
        "effective_from": parse_ts(r["effective_from"]),
        "effective_to": parse_ts(r["effective_to"]),
        "rule_version": r["rule_version"], "ingest_at": parse_ts(r["ingest_at"]),
    }


def _signal_row(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "record_id": r["record_id"], "signal_id": r["signal_id"],
        "subject_key": r["subject_key"], "age_band": r["age_band"],
        "observed_at": parse_ts(r["observed_at"]),
        "valid_from": parse_ts(r["valid_from"]),
        "valid_to": parse_ts(r["valid_to"]),
        "signal_state": r["signal_state"], "region": r["region"],
        "ingest_at": parse_ts(r["ingest_at"]),
    }


def _event_row(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "record_id": r["record_id"], "distribution_id": r["distribution_id"],
        "content_id": r["content_id"], "subject_key": r["subject_key"],
        "channel": r["channel"], "occurred_at": parse_ts(r["occurred_at"]),
        "restriction_state": r["restriction_state"], "event_ref": r["event_ref"],
        "region": r["region"], "ingest_at": parse_ts(r["ingest_at"]),
    }
