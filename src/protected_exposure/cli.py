"""命令行入口：本地合成数仓的构建、批次运行与复核查询。

示例：
  python -m protected_exposure.cli build
  python -m protected_exposure.cli run-demo
  python -m protected_exposure.cli runs
  python -m protected_exposure.cli buckets RUN-2026-05-01
  python -m protected_exposure.cli facts RUN-2026-05-01 --channel feed
  python -m protected_exposure.cli restatements RUN-2026-05-15
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import reporting
from .config import DEFAULT_DB_PATH, DEMO_RUNS, SOURCES_DIR
from .pipeline import run_batch
from .warehouse import connect, fetch_distributions, fetch_rules, fetch_signals, load_raw


def _table(rows) -> str:
    if not rows:
        return "（无记录）"
    headers = list(rows[0].keys())
    table = [headers]
    for row in rows:
        table.append(["" if row[h] is None else str(row[h]) for h in headers])
    widths = [max(len(str(line[i])) for line in table) for i in range(len(headers))]
    lines = ["  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(table[0]))]
    lines.append("  ".join("-" * w for w in widths))
    for line in table[1:]:
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(line)))
    return "\n".join(lines)


def cmd_build(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    counts = load_raw(conn, SOURCES_DIR)
    for table, count in counts.items():
        print(f"{table}: {count} 行")
    conn.close()
    return 0


def cmd_run_demo(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    load_raw(conn, SOURCES_DIR)
    rules, signals, events = (fetch_rules(conn), fetch_signals(conn),
                              fetch_distributions(conn))
    prior = None
    for run_id, cutoff, start, end in DEMO_RUNS:
        result = run_batch(
            conn, rules, signals, events, run_id, cutoff, start, end, prior,
            datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
        prior = run_id
        print(f"\n=== {run_id} (cutoff {cutoff}) ===")
        for key, value in result.items():
            print(f"  {key}: {value}")
    conn.close()
    return 0


def cmd_runs(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    print(_table(reporting.list_runs(conn)))
    conn.close()
    return 0


def cmd_buckets(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    print(_table(reporting.bucket_totals(conn, args.run_id)))
    conn.close()
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    print(_table(reporting.summary_grid(conn, args.run_id)))
    conn.close()
    return 0


def cmd_facts(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    rows = reporting.query_facts(
        conn, args.run_id, rule_version=args.rule_version, channel=args.channel,
        start=args.start, end=args.end, bucket=args.bucket)
    print(_table(rows))
    conn.close()
    return 0


def cmd_restatements(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    print(_table(reporting.restatements(conn, args.run_id)))
    conn.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="protected_exposure",
        description="青少年保护暴露窗口合成数仓（纯本地、纯标准库）")
    parser.add_argument("--db", type=Path,
                        default=DEFAULT_DB_PATH, help="SQLite 数据库路径")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("build", help="把合成 CSV 落地到 raw 层").set_defaults(func=cmd_build)
    sub.add_parser("run-demo", help="构建并执行全部演示报告批次").set_defaults(func=cmd_run_demo)
    sub.add_parser("runs", help="列出报告批次").set_defaults(func=cmd_runs)

    p = sub.add_parser("buckets", help="按保护桶统计事件数")
    p.add_argument("run_id")
    p.set_defaults(func=cmd_buckets)

    p = sub.add_parser("summary", help="规则版本 × 入口 × 桶 汇总")
    p.add_argument("run_id")
    p.set_defaults(func=cmd_summary)

    p = sub.add_parser("facts", help="逐条暴露事实，支持过滤")
    p.add_argument("run_id")
    p.add_argument("--rule-version", dest="rule_version")
    p.add_argument("--channel")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--bucket")
    p.set_defaults(func=cmd_facts)

    p = sub.add_parser("restatements", help="相对上一批次的重述差异")
    p.add_argument("run_id")
    p.set_defaults(func=cmd_restatements)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
