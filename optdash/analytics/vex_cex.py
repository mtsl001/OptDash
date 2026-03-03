"""VEX/CEX analytics — Vanna Exposure + Charm Exposure."""
import duckdb
from loguru import logger
from optdash.config import settings
from optdash.models import VexSignal, CexSignal


def get_vex_cex_current(conn: duckdb.DuckDBPyConnection, trade_date: str,
                        snap_time: str, underlying: str) -> dict:
    """Current VEX/CEX snapshot."""
    try:
        row = conn.execute("""
            SELECT
                SUM(CASE WHEN option_type='CE' THEN vex ELSE 0 END) / 1e6  AS vex_ce_M,
                SUM(CASE WHEN option_type='PE' THEN vex ELSE 0 END) / 1e6  AS vex_pe_M,
                SUM(vex) / 1e6                                              AS vex_total_M,
                SUM(CASE WHEN option_type='CE' THEN cex ELSE 0 END) / 1e6  AS cex_ce_M,
                SUM(CASE WHEN option_type='PE' THEN cex ELSE 0 END) / 1e6  AS cex_pe_M,
                SUM(cex) / 1e6                                              AS cex_total_M,
                AVG(spot)                                                   AS spot,
                MIN(dte)                                                    AS dte
            FROM options_data
            WHERE trade_date=? AND snap_time=? AND underlying=?
              AND expiry_tier='TIER1'
        """, [trade_date, snap_time, underlying]).fetchone()
        if not row:
            return {}
        vex_total = row[2] or 0
        cex_total = row[5] or 0
        dte       = row[7] or 7
        dealer_oc = _is_dealer_oclock(snap_time, dte)
        vex_signal = _classify_vex(vex_total)
        cex_signal = _classify_cex(cex_total)
        interp = _interpret(vex_signal, cex_signal, dealer_oc)
        return {
            "snap_time": snap_time,
            "vex_ce_M": round(row[0] or 0, 2), "vex_pe_M": round(row[1] or 0, 2),
            "vex_total_M": round(vex_total, 2),
            "cex_ce_M": round(row[3] or 0, 2), "cex_pe_M": round(row[4] or 0, 2),
            "cex_total_M": round(cex_total, 2),
            "spot": row[6], "dte": dte,
            "vex_signal": vex_signal, "cex_signal": cex_signal,
            "dealer_oclock": dealer_oc, "interpretation": interp,
        }
    except Exception as e:
        logger.warning("get_vex_cex_current error: {}", e)
        return {}


def get_vex_cex_full(conn: duckdb.DuckDBPyConnection, trade_date: str,
                     snap_time: str, underlying: str) -> dict:
    """Full series + by-strike breakdown."""
    series    = _get_vex_cex_series(conn, trade_date, underlying)
    by_strike = _get_by_strike(conn, trade_date, snap_time, underlying)
    current   = get_vex_cex_current(conn, trade_date, snap_time, underlying)
    return {
        "series": series, "by_strike": by_strike,
        "current": current if current else None,
        "dealer_oclock": current.get("dealer_oclock", False),
        "interpretation": current.get("interpretation", ""),
    }


def _get_vex_cex_series(conn, trade_date, underlying) -> list[dict]:
    try:
        rows = conn.execute("""
            SELECT snap_time,
                SUM(vex)/1e6 AS vex_total_M,
                SUM(CASE WHEN option_type='CE' THEN vex ELSE 0 END)/1e6 AS vex_ce_M,
                SUM(CASE WHEN option_type='PE' THEN vex ELSE 0 END)/1e6 AS vex_pe_M,
                SUM(cex)/1e6 AS cex_total_M,
                SUM(CASE WHEN option_type='CE' THEN cex ELSE 0 END)/1e6 AS cex_ce_M,
                SUM(CASE WHEN option_type='PE' THEN cex ELSE 0 END)/1e6 AS cex_pe_M,
                AVG(spot) AS spot, MIN(dte) AS dte
            FROM options_data
            WHERE trade_date=? AND underlying=? AND expiry_tier='TIER1'
            GROUP BY snap_time ORDER BY snap_time
        """, [trade_date, underlying]).fetchall()
        result = []
        for r in rows:
            vex, cex, dte = r[1] or 0, r[4] or 0, r[8] or 7
            dealer_oc  = _is_dealer_oclock(r[0], dte)
            result.append({
                "snap_time": r[0],
                "vex_total_M": round(vex, 2), "vex_ce_M": round(r[2] or 0, 2),
                "vex_pe_M": round(r[3] or 0, 2), "cex_total_M": round(cex, 2),
                "cex_ce_M": round(r[5] or 0, 2), "cex_pe_M": round(r[6] or 0, 2),
                "spot": r[7], "dte": dte, "dealer_oclock": dealer_oc,
                "vex_signal": _classify_vex(vex), "cex_signal": _classify_cex(cex),
                "interpretation": _interpret(_classify_vex(vex), _classify_cex(cex), dealer_oc),
            })
        return result
    except Exception as e:
        logger.warning("_get_vex_cex_series error: {}", e)
        return []


def _get_by_strike(conn, trade_date, snap_time, underlying) -> list[dict]:
    try:
        rows = conn.execute("""
            SELECT strike_price, option_type,
                   (strike_price - AVG(spot) OVER()) / AVG(spot) OVER() * 100 AS moneyness_pct,
                   SUM(vex)/1e6 AS vex_M, SUM(cex)/1e6 AS cex_M,
                   SUM(oi) AS oi, AVG(iv) AS iv, MIN(dte) AS dte
            FROM options_data
            WHERE trade_date=? AND snap_time=? AND underlying=? AND expiry_tier='TIER1'
            GROUP BY strike_price, option_type ORDER BY strike_price
        """, [trade_date, snap_time, underlying]).fetchall()
        return [{
            "strike_price": r[0], "option_type": r[1],
            "moneyness_pct": round(r[2] or 0, 2),
            "vex_M": round(r[3] or 0, 2), "cex_M": round(r[4] or 0, 2),
            "oi": r[5], "iv": round(r[6] or 0, 2), "dte": r[7],
        } for r in rows]
    except Exception as e:
        logger.warning("_get_by_strike error: {}", e)
        return []


def _classify_vex(vex_total: float) -> str:
    return VexSignal.VEX_BULLISH.value if vex_total > settings.VEX_BULL_THRESHOLD \
        else VexSignal.VEX_BEARISH.value if vex_total < 0 \
        else VexSignal.NEUTRAL.value


def _classify_cex(cex_total: float) -> str:
    if cex_total >= settings.CEX_STRONG_BID:
        return CexSignal.STRONG_CHARM_BID.value
    if cex_total >= settings.CEX_BID:
        return CexSignal.CHARM_BID.value
    if cex_total <= settings.CEX_PRESSURE:
        return CexSignal.CHARM_PRESSURE.value
    return CexSignal.NEUTRAL.value


def _is_dealer_oclock(snap_time: str, dte: int) -> bool:
    """True when DTE=1 and time >= DEALER_OCLOCK_START."""
    return dte <= settings.DEALER_OCLOCK_DTE and snap_time >= settings.DEALER_OCLOCK_START


def _interpret(vex_signal: str, cex_signal: str, dealer_oc: bool) -> str:
    if dealer_oc:
        return "Dealer O'Clock active — charm flows dominate expiry day mechanics."
    if vex_signal == VexSignal.VEX_BULLISH.value:
        return "IV drop forces dealer buying — bullish mechanical bias."
    if vex_signal == VexSignal.VEX_BEARISH.value:
        return "IV rise forces dealer selling — bearish mechanical pressure."
    if cex_signal == CexSignal.STRONG_CHARM_BID.value:
        return "Strong charm bid — time decay buying pressure supports upside."
    return "No dominant dealer flow signal."
