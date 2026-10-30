"""日期工具：统一使用本地时间的 'YYYY-MM-DD' 字符串表示"天"。"""

from __future__ import annotations

from datetime import date, datetime

DAY_FORMAT = "%Y-%m-%d"
WEEKDAY_NAMES = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def today_str() -> str:
    return date.today().isoformat()


def iso_now() -> str:
    """当前本地时间，精确到秒，用于 created_at / updated_at。"""
    return datetime.now().isoformat(timespec="seconds")


def timestamp_iso(ts: float) -> str:
    """文件时间戳 → 本地 ISO 时间字符串。"""
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def day_from_timestamp(ts: float) -> str:
    """文件时间戳 → 本地日期字符串。"""
    return datetime.fromtimestamp(ts).date().isoformat()


def parse_day(text: str) -> date:
    """严格解析 'YYYY-MM-DD'，失败抛 ValueError。"""
    return datetime.strptime((text or "").strip(), DAY_FORMAT).date()


def try_parse_day(text: str) -> str | None:
    try:
        return parse_day(text).isoformat()
    except (TypeError, ValueError):
        return None


def format_day_cn(day: str) -> str:
    """'2026-10-29' → '10月29日'。"""
    try:
        d = date.fromisoformat(day)
    except ValueError:
        return day or ""
    return f"{d.month}月{d.day}日"


def weekday_cn(day: str) -> str:
    try:
        d = date.fromisoformat(day)
    except ValueError:
        return ""
    return WEEKDAY_NAMES[d.weekday()]


def short_time(iso: str | None) -> str:
    """'2026-10-29T14:02:11' → '2026-10-29 14:02'；兼容纯日期。"""
    if not iso:
        return ""
    try:
        if "T" in iso:
            return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
        return iso
    except ValueError:
        return iso


def short_day(day: str | None) -> str:
    """'2026-10-29' → '10-29'。"""
    if not day:
        return ""
    return day[5:] if len(day) >= 10 else day
