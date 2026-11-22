"""带时区的 ISO 8601 时间解析与比较工具。

所有合成时间均为 UTC（后缀 Z），解析为 aware datetime，
避免事件时间、有效期与报告截止时间之间的时区混淆。
"""

from __future__ import annotations

from datetime import date, datetime, timezone


def parse_ts(value: str | None) -> datetime | None:
    """解析 ISO 8601 时间；纯日期解释为 UTC 零点；空值返回 None。

    带时间的值必须带时区（合成数据统一使用 Z 后缀）。
    """
    if value is None or value == "":
        return None
    text = value.strip()
    if len(text) == 10:  # YYYY-MM-DD，年龄信号有效期按 UTC 零点处理
        day = date.fromisoformat(text)
        return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise ValueError(f"时间值缺少时区信息: {value!r}")
    return dt.astimezone(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    """统一回写为 UTC ISO 8601（Z 后缀）。"""
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def as_of_window(valid_from: datetime | None, valid_to: datetime | None,
                 moment: datetime) -> bool:
    """moment 是否落在 [valid_from, valid_to) 半开区间内；开放端用 None 表示。"""
    if valid_from is not None and moment < valid_from:
        return False
    if valid_to is not None and moment >= valid_to:
        return False
    return True
