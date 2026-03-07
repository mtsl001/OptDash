"""EOD sweep - force-close all open positions and finalize shadows at market close."""
import duckdb
import sqlite3
from loguru import logger
from optdash.config import settings
from optdash.models import ExitReason, ShadowOutcome, TradeStatus
from optdash.ai.journal import trades, shadow
from optdash.ai.tracker import _fetch_strike_current
from optdash.ai.shadow_tracker import _classify_shadow_outcome


def eod_force_close(
    conn:       duckdb.DuckDBPyConnection,
    jconn:      sqlite3.Connection,
    trade_date: str,
) -> None:
    """Force-close all ACCEPTED trades and expire all GENERATED recommendations at EOD.

    Fix-J (F-02): Pending (GENERATED) recommendations are now expired here.
    Previously they survived into the next trading day, causing
    generate_recommendation() to skip generation all morning because it
    found a stale pending trade from the previous session's close.
    """
    # Step 1: Expire all pending (GENERATED) recommendations across all underlyings.
    # Must run before the open-position loop so the journal is clean for next session.
    pending = trades.get_pending_trades(jconn)
    for p in pending:
        trades.update_status(
            jconn, p["id"], TradeStatus.EXPIRED.value,
            state_reason="EOD sweep -- expired unseen recommendation"
        )
        logger.info(
            "EOD expired pending recommendation: id={} underlying={} snap={}",
            p["id"], p["underlying"], p["snap_time"]
        )

    # Step 2: Force-close all ACCEPTED (open) positions.
    open_trades = trades.get_open_trades(jconn)
    snap_time   = settings.EOD_FORCE_CLOSE_TIME

    for trade in open_trades:
        current = _fetch_strike_current(
            conn, trade_date, snap_time,
            trade["underlying"], trade["strike_price"],
            trade["expiry_date"], trade["option_type"]
        )

        # Use actual_entry_price (slippage-adjusted) if trader set one on accept.
        # Falls back to recommended entry_premium only if actual not recorded.
        entry    = trade["actual_entry_price"] or trade["entry_premium"]
        ltp      = (current or {}).get("ltp") or entry
        lot      = settings.LOT_SIZES.get(trade["underlying"], 1)
        # Fix-K (F-01): pnl_abs is monetary (point_diff * lot); pnl_pct stays per-unit %
        pnl_abs  = round((ltp - entry) * lot, 2)
        pnl_pct  = round((ltp - entry) / entry * 100, 2) if entry else 0.0

        trades.close_trade(jconn, trade["id"], {
            "exit_premium":   ltp,
            "exit_snap_time": snap_time,
            "exit_reason":    ExitReason.EOD_FORCE.value,
            "final_pnl_abs":  pnl_abs,
            "final_pnl_pct":  pnl_pct,
        })
        logger.info(
            "EOD force-close: {} {} entry={} ltp={} pnl={:+.1f}%",
            trade["underlying"], trade["option_type"], entry, ltp, pnl_pct
        )


def finalize_all_shadows(
    conn:       duckdb.DuckDBPyConnection,
    jconn:      sqlite3.Connection,
    trade_date: str,
) -> None:
    """Close any shadows still open at EOD with final hypothetical PnL."""
    shadows = shadow.get_active_shadows(jconn, trade_date)
    for s in shadows:
        current = _fetch_strike_current(
            conn, trade_date, settings.EOD_FORCE_CLOSE_TIME,
            s["underlying"], s["strike_price"], s["expiry_date"], s["option_type"]
        )
        # Shadows always use entry_premium (no actual fill for hypotheticals)
        ltp     = (current or {}).get("ltp") or s["entry_premium"]
        pnl     = round((ltp - s["entry_premium"]) / s["entry_premium"] * 100, 2)
        outcome = _classify_shadow_outcome(pnl)
        shadow.close_shadow(jconn, s["id"], {
            "final_pnl_pct": pnl,
            "outcome":       outcome,
            "closed_snap":   settings.EOD_FORCE_CLOSE_TIME,
        })
        logger.debug(
            "EOD shadow close: id={} outcome={} pnl={:+.1f}%",
            s["id"], outcome, pnl
        )
