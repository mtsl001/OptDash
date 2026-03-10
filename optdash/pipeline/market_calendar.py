"""market_calendar.py — Trading day utilities for pipeline iteration.

Uses settings.MARKET_HOLIDAYS (list[str] of YYYY-MM-DD) and weekday checks.
No external dependency — pure datetime arithmetic. No BQ calls.

All date/time values are IST-aware. The ZoneInfo lookup is done once at
module import so there is no per-call overhead.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from optdash.config import settings

IST = ZoneInfo("Asia/Kolkata")
_MARKET_OPEN_MIN  = 9 * 60 + 15   # 09:15 as minutes since midnight
_MARKET_CLOSE_MIN = 15 * 60 + 30  # 15:30 as minutes since midnight


def is_trading_day(d: date) -> bool:
    """True if d is Monday–Friday and not listed in settings.MARKET_HOLIDAYS."""
    return (
        d.weekday() < 5
        and d.strftime("%Y-%m-%d") not in set(settings.MARKET_HOLIDAYS)
    )


def is_within_market_hours() -> bool:
    """True if current IST wall-clock time is within 09:15–15:30 inclusive."""
    now = datetime.now(IST)
    t   = now.hour * 60 + now.minute
    return _MARKET_OPEN_MIN <= t <= _MARKET_CLOSE_MIN


def today_ist() -> date:
    """Current calendar date in IST (avoids off-by-one on UTC servers)."""
    return datetime.now(IST).date()


def yesterday_ist() -> date:
    """Yesterday’s calendar date in IST."""
    return today_ist() - timedelta(days=1)


def prev_trading_day(from_date: date | None = None) -> date:
    """Most recent trading day strictly before from_date (default: today IST)."""
    d = (from_date or today_ist()) - timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def get_trading_days(start: date, end: date) -> list[date]:
    """Chronological list of trading days in [start, end] inclusive."""
    days: list[date] = []
    current = start
    while current <= end:
        if is_trading_day(current):
            days.append(current)
        current += timedelta(days=1)
    return days
