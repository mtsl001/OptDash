"""Shared DuckDB query helpers used across ai/ modules.

fetch_strike_current() was previously defined as a private symbol
(_fetch_strike_current) in ai/tracker.py and imported by both eod.py
and shadow_tracker.py via cross-module private-symbol imports -- a
coupling anti-pattern that makes the function invisible to any caller
outside the ai/ package and creates silent breakage on rename.

Moving it here gives eod.py, shadow_tracker.py, and tracker.py a
single public import path with no circular dependency.
"""
import duckdb
from loguru import logger


def fetch_strike_current(
    conn:         duckdb.DuckDBPyConnection,
    trade_date:   str,
    snap_time:    str,
    underlying:   str,
    strike_price: float,
    expiry_date:  str,
    option_type:  str,
) -> dict | None:
    """Most-recent LTP + Greeks for one contract at or before snap_time.

    Uses snap_time<=? + ORDER BY DESC LIMIT 1 to handle BQ feed latency
    during EOD force-close windows (Fix-C). Column names are derived from
    cursor.description so schema changes never silently misalign values
    (Fix TRK-1).
    """
    try:
        cur = conn.execute("""
            SELECT ltp, iv, delta, theta, gamma, vega, spot
            FROM options_data
            WHERE trade_date=? AND snap_time<=? AND underlying=?
              AND strike_price=? AND expiry_date=? AND option_type=?
            ORDER BY snap_time DESC
            LIMIT 1
        """, [trade_date, snap_time, underlying,
               strike_price, expiry_date, option_type])
        cols = [d[0] for d in cur.description]
        row  = cur.fetchone()
        if not row:
            return None
        return dict(zip(cols, row))
    except Exception as e:
        logger.warning("fetch_strike_current error: {}", e)
        return None
