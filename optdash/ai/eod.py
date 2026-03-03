"""EOD sweep — force-close all open positions and finalize shadows at 15:20."""
import duckdb
import sqlite3
from loguru import logger
from optdash.config import settings
from optdash.models import ExitReason, ShadowOutcome
from optdash.ai.journal import trades, shadow
from optdash.ai.tracker import _fetch_strike_current
from optdash.ai.shadow_tracker import _classify_shadow_outcome


def eod_force_close(
    conn:       duckdb.DuckDBPyConnection,
    jconn:      sqlite3.Connection,
    trade_date: str,
) -> None:
    """Force-close all ACCEPTED trades still open at EOD."""
    open_trades = trades.get_open_trades(jconn)
    snap_time   = settings.EOD_FORCE_CLOSE_TIME

    for trade in open_trades:
        current = _fetch_strike_current(
            conn, trade_date, snap_time,
            trade["underlying"], trade["strike_price"],
            trade["expiry_date"], trade["option_type"]
        )
        ltp = (current or {}).get("ltp") or trade["entry_premium"]
        pnl = round((ltp - trade["entry_premium"]) / trade["entry_premium"] * 100, 2)

        trades.close_trade(jconn, trade["id"], {
            "exit_premium":   ltp,
            "exit_snap_time": snap_time,
            "exit_reason":    ExitReason.EOD_FORCE.value,
            "final_pnl_abs":  round(ltp - trade["entry_premium"], 2),
            "final_pnl_pct":  pnl,
        })
        logger.info(
            "EOD force-close: {} {} pnl={:+.1f}%",
            trade["underlying"], trade["option_type"], pnl
        )


def finalize_all_shadows(
    conn:       duckdb.DuckDBPyConnection,
    jconn:      sqlite3.Connection,
    trade_date: str,
) -> None:
    """Close any shadows still open at EOD."""
    shadows = shadow.get_active_shadows(jconn, trade_date)
    for s in shadows:
        current = _fetch_strike_current(
            conn, trade_date, settings.EOD_FORCE_CLOSE_TIME,
            s["underlying"], s["strike_price"], s["expiry_date"], s["option_type"]
        )
        ltp = (current or {}).get("ltp") or s["entry_premium"]
        pnl = round((ltp - s["entry_premium"]) / s["entry_premium"] * 100, 2)
        outcome = _classify_shadow_outcome(pnl)
        shadow.close_shadow(jconn, s["id"], {
            "final_pnl_pct": pnl,
            "outcome":       outcome,
            "closed_snap":   settings.EOD_FORCE_CLOSE_TIME,
        })
