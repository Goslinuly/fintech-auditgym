"""Time helpers. All timestamps are timezone-aware; offsets are fixed (no DST)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

UTC = timezone.utc


def parse_tz(spec: str) -> timezone:
    sign = 1 if spec[0] == "+" else -1
    hours, minutes = int(spec[1:3]), int(spec[4:6])
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def fmt(dt: datetime, tz: timezone) -> str:
    return dt.astimezone(tz).isoformat(timespec="seconds")


def parse_ts(text: str) -> datetime:
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise ValueError(f"timestamp without offset: {text!r}")
    return dt


def local_date(dt: datetime, tz: timezone) -> date:
    return dt.astimezone(tz).date()


def is_business_day(d: date) -> bool:
    return d.weekday() < 5
