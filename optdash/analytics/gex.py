"""GEX analytics — Net GEX series, regime, max pain, spot summary."""
import duckdb
from loguru import logger
from optdash.config import settings
from optdash.models import GEXRegime


def get_net_gex(conn: duckdb.DuckDBPyConnection, trade_date: str, snap_time: str,
               underlying: str) -> dict:
    """Latest-snap GEX snapshot with regime classification."""
    try:
        row = conn.execute("""
            SELECT
                snap_time,
                SUM(gex)                                             AS gex_all_raw,
                SUM(CASE WHEN expiry_tier IN ('TIER1','TIER2') THEN gex ELSE 0 END) AS gex_near_raw,
                SUM(CASE WHEN expiry_tier = 'TIER3' THEN gex ELSE 0 END)            AS gex_far_raw,
                MAX(spot) AS spot
            FROM options_data
            WHERE trade_date = ? AND snap_time = ? AND underlying = ?
            GROUP BY snap_time
        """, [trade_date, snap_time, underlying]).fetchone()
        if not row:
            return {}
        gex_all  = (row[1] or 0) / settings.GEX_SCALING
        gex_near = (row[2] or 0) / settings.GEX_SCALING
        gex_far  = (row[3] or 0) / settings.GEX_SCALING
        peak     = _get_gex_peak(conn, trade_date, underlying)
        pct      = (abs(gex_all) / peak * 100) if peak else 100.0
        regime   = GEXRegime.NEGATIVE_TREND if gex_all < 0 else GEXRegime.POSITIVE_CHOP
        return {
            "snap_time": row[0], "gex_all_B": round(gex_all, 3),
            "gex_near_B": round(gex_near, 3), "gex_far_B": round(gex_far, 3),
            "pct_of_peak": round(pct, 1), "regime": regime.value, "spot": row[4],
        }
    except Exception as e:
        logger.warning("get_net_gex error: {}", e)
        return {}


def get_gex_series(conn: duckdb.DuckDBPyConnection, trade_date: str, underlying: str) -> list[dict]:
    """Full-day GEX series with pct_of_peak for charting."""
    try:
        rows = conn.execute("""
            SELECT
                snap_time,
                SUM(gex) / ? AS gex_all_B,
                SUM(CASE WHEN expiry_tier IN ('TIER1','TIER2') THEN gex ELSE 0 END) / ? AS gex_near_B,
                SUM(CASE WHEN expiry_tier = 'TIER3' THEN gex ELSE 0 END) / ?            AS gex_far_B
            FROM options_data
            WHERE trade_date = ? AND underlying = ?
            GROUP BY snap_time ORDER BY snap_time
        """, [settings.GEX_SCALING, settings.GEX_SCALING, settings.GEX_SCALING,
               trade_date, underlying]).fetchall()
        if not rows:
            return []
        peak = max(abs(r[1]) for r in rows) or 1.0
        result = []
        for r in rows:
            gex = r[1] or 0
            regime = GEXRegime.NEGATIVE_TREND if gex < 0 else GEXRegime.POSITIVE_CHOP
            result.append({
                "snap_time": r[0], "gex_all_B": round(r[1] or 0, 3),
                "gex_near_B": round(r[2] or 0, 3), "gex_far_B": round(r[3] or 0, 3),
                "pct_of_peak": round(abs(gex) / peak * 100, 1),
                "regime": regime.value,
            })
        return result
    except Exception as e:
        logger.warning("get_gex_series error: {}", e)
        return []


def get_spot_summary(conn: duckdb.DuckDBPyConnection, trade_date: str, underlying: str) -> dict:
    """Latest spot with day OHLC and change pct."""
    try:
        row = conn.execute("""
            SELECT
                MAX(snap_time)  AS snap_time,
                MAX(spot)       AS spot,
                FIRST(spot ORDER BY snap_time ASC)  AS day_open,
                MAX(spot)       AS day_high,
                MIN(spot)       AS day_low
            FROM options_data
            WHERE trade_date = ? AND underlying = ?
        """, [trade_date, underlying]).fetchone()
        if not row or not row[1]:
            return {}
        spot, open_ = row[1], row[2] or row[1]
        return {
            "snap_time": row[0], "spot": spot, "day_open": open_,
            "day_high": row[3], "day_low": row[4],
            "change_pct": round((spot - open_) / open_ * 100, 2) if open_ else 0,
        }
    except Exception as e:
        logger.warning("get_spot_summary error: {}", e)
        return {}


def get_max_pain(conn: duckdb.DuckDBPyConnection, trade_date: str, snap_time: str,
                underlying: str, expiry_date: str) -> dict:
    """Max pain strike — strike at which total option writers pay minimum."""
    try:
        rows = conn.execute("""
            SELECT strike_price,
                   SUM(CASE WHEN option_type='CE' THEN oi ELSE 0 END) AS ce_oi,
                   SUM(CASE WHEN option_type='PE' THEN oi ELSE 0 END) AS pe_oi,
                   MAX(spot) AS spot
            FROM options_data
            WHERE trade_date=? AND snap_time=? AND underlying=? AND expiry_date=?
            GROUP BY strike_price ORDER BY strike_price
        """, [trade_date, snap_time, underlying, expiry_date]).fetchall()
        if not rows:
            return {"max_pain": None, "distance_pct": None}
        strikes = [r[0] for r in rows]
        ce_oi   = [r[1] or 0 for r in rows]
        pe_oi   = [r[2] or 0 for r in rows]
        spot    = rows[0][3] or 0
        min_pain, min_strike = float("inf"), strikes[0]
        for i, s in enumerate(strikes):
            pain = sum(max(0, s - k) * c for k, c in zip(strikes, ce_oi)) + \
                   sum(max(0, k - s) * p for k, p in zip(strikes, pe_oi))
            if pain < min_pain:
                min_pain, min_strike = pain, s
        dist = ((spot - min_strike) / min_strike * 100) if min_strike else None
        return {"max_pain": min_strike, "distance_pct": round(dist, 3) if dist else None, "spot": spot}
    except Exception as e:
        logger.warning("get_max_pain error: {}", e)
        return {"max_pain": None, "distance_pct": None}


def _get_gex_peak(conn: duckdb.DuckDBPyConnection, trade_date: str, underlying: str) -> float:
    """Day peak absolute GEX (for pct_of_peak)."""
    try:
        row = conn.execute("""
            SELECT MAX(ABS(gex_sum)) FROM (
                SELECT snap_time, SUM(gex) / ? AS gex_sum
                FROM options_data WHERE trade_date=? AND underlying=?
                GROUP BY snap_time
            )
        """, [settings.GEX_SCALING, trade_date, underlying]).fetchone()
        return row[0] if row and row[0] else 1.0
    except Exception:
        return 1.0
