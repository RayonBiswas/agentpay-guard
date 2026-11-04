"""Calendar budget windows (day / ISO week from Monday / month) in the policy timezone."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

PERIODS = ("day", "week", "month")


def period_start(period: str, now: datetime, tz: str) -> datetime:
    """Start of the current period, returned as an aware UTC datetime."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    local = now.astimezone(ZoneInfo(tz))
    day0 = datetime.combine(local.date(), time.min, tzinfo=local.tzinfo)
    if period == "day":
        start = day0
    elif period == "week":
        start = day0 - timedelta(days=local.weekday())
    elif period == "month":
        start = day0.replace(day=1)
    else:
        raise ValueError(f"unknown period {period!r}")
    return start.astimezone(timezone.utc)


def in_quiet_hours(now: datetime, tz: str, start: str, end: str) -> bool:
    """True if local time is inside [start, end). Handles windows crossing midnight."""
    local_t = now.astimezone(ZoneInfo(tz)).time()
    s = time.fromisoformat(start)
    e = time.fromisoformat(end)
    if s == e:
        return False
    if s < e:
        return s <= local_t < e
    return local_t >= s or local_t < e
