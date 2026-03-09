"""Shadow tracking — hypothetical tracking of rejected/expired trades.

Shadow trades are created when a recommendation is REJECTED or EXPIRED.
Every scheduler tick (when a shadow is open), we record what would have
happened to the position if the trader had taken it.

The scheduler handles EOD via eod.py -> finalize_all_shadows().
This module only handles intra-day snap recording and SL/target close.
"""
import duckdb
import sqlite3
from loguru import logger
from optdash.config import settings
from optdash.models import ShadowOutcome
from optdash.ai.journal import shadow
from optdash.analytics.query import fetch_strike_current as _fetch_strike_current


def track_shadow_positions(
    conn:       duckdb.DuckDBPyConnection,
    jconn:      sqlite3.Connection,
    trade_date: str,
    snap_time:  str,
) -> None:
    """Record a snap for every active shadow and auto-close on SL/target hit."""
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

        # F16: when this snap is the closing snap (SL or target hit), pass
        # commit=False so the INSERT stays in the open implicit transaction.
        # close_shadow() follows immediately and commits both writes together,
        # making the snap row and the is_closed flag update atomic.
        # A crash between insert and close previously left is_closed=0
        # forever, causing duplicate snaps and double-close on every
        # subsequent tick for that shadow.
        is_closing = hit_sl or hit_tgt

        shadow.insert_shadow_snap(
            jconn,
            {
                "shadow_id":  s["id"],
                "snap_time":  snap_time,
                "ltp":        ltp,
                "pnl_pct":    pnl,
                "hit_sl":     int(hit_sl),
                "hit_target": int(hit_tgt),
            },
            commit=not is_closing,   # False → snap stays uncommitted until close_shadow()
        )

        # Close shadow if SL or target is hit intra-day.
        # EOD close is handled by finalize_all_shadows() in eod.py
        # (called by scheduler before this function runs).
        if is_closing:
            outcome = _classify_shadow_outcome(pnl)
            shadow.close_shadow(jconn, s["id"], {
                "final_pnl_pct": pnl,
                "outcome":       outcome,
                "closed_snap":   snap_time,
            })  # close_shadow() always commits — this is the single flush
            logger.debug(
                "Shadow {} closed intra-day: outcome={} pnl={:+.1f}%",
                s["id"], outcome, pnl
            )


def _classify_shadow_outcome(pnl_pct: float) -> str:
    """Classify the hypothetical trade outcome for learning analysis.

    CLEAN_MISS  : would have won ≥30%  — costly rejection
    GOOD_SKIP   : would have lost ≥20% — correct rejection
    BREAK_EVEN  : |PnL| < 5%
    RISKY_MISS  : everything else (mixed / moderate outcome)
    """
    if pnl_pct > 30:
        return ShadowOutcome.CLEAN_MISS.value
    if pnl_pct < -20:
        return ShadowOutcome.GOOD_SKIP.value
    if abs(pnl_pct) < 5:
        return ShadowOutcome.BREAK_EVEN.value
    return ShadowOutcome.RISKY_MISS.value
