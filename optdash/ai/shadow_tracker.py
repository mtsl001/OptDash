"""Shadow tracking — hypothetical tracking of rejected/expired trades."""
import duckdb
import sqlite3
from loguru import logger
from optdash.config import settings
from optdash.models import ShadowOutcome
from optdash.ai.journal import shadow
from optdash.ai.tracker import _fetch_strike_current


def track_shadow_positions(
    conn:       duckdb.DuckDBPyConnection,
    jconn:      sqlite3.Connection,
    trade_date: str,
    snap_time:  str,
) -> None:
    """For every REJECTED or EXPIRED trade, track what would have happened."""
    shadows = shadow.get_active_shadows(jconn, trade_date)
    for s in shadows:
        current = _fetch_strike_current(
            conn, trade_date, snap_time,
            s["underlying"], s["strike_price"], s["expiry_date"], s["option_type"]
        )
        if not current:
            continue

        ltp     = current["ltp"]
        pnl     = round((ltp - s["entry_premium"]) / s["entry_premium"] * 100, 2)
        hit_sl  = ltp <= s["entry_premium"] * (1 - settings.AI_SL_PCT)
        hit_tgt = ltp >= s["entry_premium"] * settings.AI_TARGET_MULT

        shadow.insert_shadow_snap(jconn, {
            "shadow_id":  s["id"],
            "snap_time":  snap_time,
            "ltp":        ltp,
            "pnl_pct":    pnl,
            "hit_sl":     int(hit_sl),
            "hit_target": int(hit_tgt),
        })

        if hit_sl or hit_tgt or snap_time == settings.EOD_SWEEP_TIME:
            outcome = _classify_shadow_outcome(pnl)
            shadow.close_shadow(jconn, s["id"], {
                "final_pnl_pct": pnl,
                "outcome":       outcome,
                "closed_snap":   snap_time,
            })


def _classify_shadow_outcome(pnl_pct: float) -> str:
    if pnl_pct > 30:
        return ShadowOutcome.CLEAN_MISS.value
    elif pnl_pct < -20:
        return ShadowOutcome.GOOD_SKIP.value
    elif abs(pnl_pct) < 5:
        return ShadowOutcome.BREAK_EVEN.value
    return ShadowOutcome.RISKY_MISS.value
