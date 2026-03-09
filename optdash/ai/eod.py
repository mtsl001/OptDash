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

    Fix-J (F-02): Pending (GENERATED) recommendations are expired here so
    they do not survive into the next trading session.

    P4-F11: Read phase (DuckDB LTP fetches) is now separated from the write
    phase (journal updates). All writes are guarded in a single try/except
    that re-raises with a clear diagnostic message on failure so the operator
    knows to check for residual ACCEPTED/GENERATED trades on next startup.
    """
    pending     = trades.get_pending_trades(jconn)
    open_trades = trades.get_open_trades(jconn)
    snap_time   = settings.EOD_FORCE_CLOSE_TIME

    # ── Read phase: pre-fetch all LTPs before any writes ─────────────────────
    # A DuckDB error mid-loop cannot interleave with half-written journal rows
    # if we fetch everything first and write in a separate, guarded phase.
    close_payloads: list[tuple[dict, dict]] = []
    for trade in open_trades:
        current = _fetch_strike_current(
            conn, trade_date, snap_time,
            trade["underlying"], trade["strike_price"],
            trade["expiry_date"], trade["option_type"]
        )
        # Use actual_entry_price (slippage-adjusted) if trader set one on accept.
        # Falls back to recommended entry_premium only if actual not recorded.
        entry   = trade["actual_entry_price"] or trade["entry_premium"]
        ltp     = (current or {}).get("ltp") or entry
        lot     = settings.LOT_SIZES.get(trade["underlying"], 1)
        # Fix-K (F-01): pnl_abs is monetary (point_diff * lot); pnl_pct stays per-unit %
        pnl_abs = round((ltp - entry) * lot, 2)
        pnl_pct = round((ltp - entry) / entry * 100, 2) if entry else 0.0
        close_payloads.append((trade, {
            "exit_premium":   ltp,
            "exit_snap_time": snap_time,
            "exit_reason":    ExitReason.EOD_FORCE.value,
            "final_pnl_abs":  pnl_abs,
            "final_pnl_pct":  pnl_pct,
        }))

    # ── Write phase: all journal updates in one guarded block ───────────────
    # P4-F11: if any write fails, log a clear diagnostic so the operator
    # can identify and manually reconcile residual positions on next startup.
    # Full single-transaction atomicity (all-or-nothing rollback) requires
    # commit=False support in trades.close_trade() / trades.update_status();
    # that is a trades.py change deferred to a follow-up commit.
    try:
        # Step 1: Expire all pending (GENERATED) recommendations.
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
        for trade, payload in close_payloads:
            trades.close_trade(jconn, trade["id"], payload)
            logger.info(
                "EOD force-close: {} {} entry={} ltp={} pnl={:+.1f}%",
                trade["underlying"], trade["option_type"],
                trade["actual_entry_price"] or trade["entry_premium"],
                payload["exit_premium"], payload["final_pnl_pct"]
            )

    except Exception:
        logger.error(
            "EOD sweep failed mid-write — journal may have partial closes. "
            "Check for ACCEPTED/GENERATED trades on next startup.",
            exc_info=True,
        )
        raise


def finalize_all_shadows(
    conn:       duckdb.DuckDBPyConnection,
    jconn:      sqlite3.Connection,
    trade_date: str,
) -> None:
    """Close any shadow still open at EOD with final hypothetical PnL.

    P4-F10: switched from get_active_shadows(jconn, trade_date) to
    get_all_unclosed_shadows(jconn) so shadows from days when the app
    crashed before EOD are finalized on the next EOD run rather than
    accumulating with is_closed=0 indefinitely.

    Each shadow uses its own s["trade_date"] for the DuckDB lookup so
    historical data for prior trading days is correctly referenced.
    """
    shadows = shadow.get_all_unclosed_shadows(jconn)
    for s in shadows:
        shadow_date = s["trade_date"]   # use shadow's own date, not today
        current = _fetch_strike_current(
            conn, shadow_date, settings.EOD_FORCE_CLOSE_TIME,
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
            "EOD shadow close: id={} date={} outcome={} pnl={:+.1f}%",
            s["id"], shadow_date, outcome, pnl
        )
