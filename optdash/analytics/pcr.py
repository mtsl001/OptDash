"""PCR analytics — Put-Call ratio divergence and OBI smoothing."""
import duckdb
from loguru import logger
from optdash.config import settings


def get_pcr(conn: duckdb.DuckDBPyConnection, trade_date: str,
            snap_time: str, underlying: str) -> dict:
    """Current PCR snapshot with divergence signal."""
    try:
        row = conn.execute("""
            SELECT
                SUM(CASE WHEN option_type='PE' THEN volume ELSE 0 END) /
                NULLIF(SUM(CASE WHEN option_type='CE' THEN volume ELSE 0 END), 0) AS pcr_vol,
                SUM(CASE WHEN option_type='PE' THEN oi ELSE 0 END) /
                NULLIF(SUM(CASE WHEN option_type='CE' THEN oi ELSE 0 END), 0)     AS pcr_oi
            FROM options_data
            WHERE trade_date=? AND snap_time=? AND underlying=?
              AND expiry_tier='TIER1'
        """, [trade_date, snap_time, underlying]).fetchone()
        if not row:
            return {}
        pcr_vol = row[0] or 1.0
        pcr_oi  = row[1] or 1.0
        div     = round(pcr_vol - pcr_oi, 4)
        obi     = _smoothed_obi(conn, trade_date, snap_time, underlying)
        return {
            "snap_time": snap_time, "pcr_vol": round(pcr_vol, 3),
            "pcr_oi": round(pcr_oi, 3), "pcr_divergence": div,
            "smoothed_obi": round(obi, 4), "signal": _pcr_signal(div),
        }
    except Exception as e:
        logger.warning("get_pcr error: {}", e)
        return {}


def get_pcr_series(conn: duckdb.DuckDBPyConnection, trade_date: str, underlying: str) -> list[dict]:
    """Full-day PCR series."""
    try:
        rows = conn.execute("""
            SELECT snap_time,
                SUM(CASE WHEN option_type='PE' THEN volume ELSE 0 END) /
                NULLIF(SUM(CASE WHEN option_type='CE' THEN volume ELSE 0 END), 0) AS pcr_vol,
                SUM(CASE WHEN option_type='PE' THEN oi ELSE 0 END) /
                NULLIF(SUM(CASE WHEN option_type='CE' THEN oi ELSE 0 END), 0)     AS pcr_oi
            FROM options_data
            WHERE trade_date=? AND underlying=? AND expiry_tier='TIER1'
            GROUP BY snap_time ORDER BY snap_time
        """, [trade_date, underlying]).fetchall()
        result = []
        for r in rows:
            pcr_vol = r[1] or 1.0
            pcr_oi  = r[2] or 1.0
            div     = round(pcr_vol - pcr_oi, 4)
            result.append({
                "snap_time": r[0], "pcr_vol": round(pcr_vol, 3),
                "pcr_oi": round(pcr_oi, 3), "pcr_divergence": div,
                "smoothed_obi": 0.0, "signal": _pcr_signal(div),
            })
        return result
    except Exception as e:
        logger.warning("get_pcr_series error: {}", e)
        return []


def _smoothed_obi(conn, trade_date, snap_time, underlying) -> float:
    try:
        rows = conn.execute("""
            SELECT snap_time,
                (SUM(bid_qty) - SUM(ask_qty)) / NULLIF(SUM(bid_qty + ask_qty), 0) AS obi
            FROM options_data
            WHERE trade_date=? AND underlying=? AND snap_time <= ? AND expiry_tier='TIER1'
            GROUP BY snap_time ORDER BY snap_time DESC LIMIT 3
        """, [trade_date, underlying, snap_time]).fetchall()
        if not rows:
            return 0.0
        return sum(r[1] or 0 for r in rows) / len(rows)
    except Exception:
        return 0.0


def _pcr_signal(div: float) -> str:
    if div > settings.PCR_DIV_BULL_THRESHOLD:
        return "RETAIL_PANIC_PUTS"
    if div < settings.PCR_DIV_BEAR_THRESHOLD:
        return "RETAIL_PANIC_CALLS"
    if abs(div) > 0.10:
        return "DIVERGENCE_BUILDING"
    return "BALANCED"
