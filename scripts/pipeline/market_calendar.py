"""
calendar.py — NSE trading calendar and market hours logic.

Critical: record_time from BigQuery is labelled UTC but contains IST values.
All time comparisons use naive datetimes with IST values directly.
No pytz/zoneinfo conversion is applied anywhere in this codebase.
"""
from datetime import date, datetime, time, timedelta
from typing import Optional


# ── NSE Trading Holidays 2026 ─────────────────────────────────────────────────
NSE_HOLIDAYS_2026: frozenset[str] = frozenset({
    "2026-01-15",   # Municipal Corporation Election
    "2026-01-26",   # Republic Day
    "2026-03-03",   # Holi
    "2026-03-26",   # Ram Navami
    "2026-03-31",   # Mahavir Jayanti
    "2026-04-03",   # Good Friday
    "2026-04-14",   # Dr. Ambedkar Jayanti
    "2026-05-01",   # Maharashtra Day
    "2026-05-28",   # Bakri Id / Eid-ul-Adha
    "2026-06-26",   # Muharram
    "2026-09-14",   # Ganesh Chaturthi
    "2026-10-02",   # Gandhi Jayanti
    "2026-10-20",   # Dussehra
    "2026-11-08",   # Diwali Laxmi Pujan (Muhurat only — pipeline skips)
    "2026-11-10",   # Diwali-Balipratipada
    "2026-11-24",   # Guru Nanak Jayanti
    "2026-12-25",   # Christmas
})

MARKET_OPEN  = time(9, 15, 0)
MARKET_CLOSE = time(15, 30, 0)
FIRST_PULL   = time(9, 16, 30)   # 90 sec after open


def is_trading_day(check_date: date) -> bool:
    """Return True if NSE F&O is open for normal trading on check_date."""
    if check_date.weekday() >= 5:
        return False
    return check_date.strftime("%Y-%m-%d") not in NSE_HOLIDAYS_2026


def is_within_market_hours(dt: datetime) -> bool:
    """Return True if dt (naive IST) is within NSE F&O trading hours."""
    return is_trading_day(dt.date()) and MARKET_OPEN <= dt.time() <= MARKET_CLOSE


def get_trading_days(start: date, end: date) -> list[date]:
    """Return sorted list of NSE trading days in [start, end] inclusive."""
    days: list[date] = []
    current = start
    while current <= end:
        if is_trading_day(current):
            days.append(current)
        current += timedelta(days=1)
    return days


def first_run_dt_today() -> Optional[datetime]:
    """Return the datetime of the first scheduled pull today (09:16:30 IST)."""
    today = date.today()
    if not is_trading_day(today):
        return None
    return datetime.combine(today, FIRST_PULL)


def next_scheduled_run(from_dt: Optional[datetime] = None) -> Optional[datetime]:
    """Return the next scheduled 5-min pull datetime from from_dt (default: now)."""
    now   = from_dt or datetime.now()
    today = now.date()
    if not is_trading_day(today):
        return None

    first = datetime.combine(today, FIRST_PULL)
    if now < first:
        return first

    elapsed_secs = (now - first).total_seconds()
    periods = int(elapsed_secs / 300) + 1
    nxt = first + timedelta(seconds=periods * 300)

    if nxt.time() > MARKET_CLOSE:
        return None
    return nxt
