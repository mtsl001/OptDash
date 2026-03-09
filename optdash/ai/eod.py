"""EOD sweep - force-close all open positions and finalize shadows at market close."""
import duckdb
import sqlite3
from loguru import logger
from optdash.config import settings
from optdash.models import ExitReason, ShadowOutcome, TradeStatus
from optdash.ai.journal import trades, shadow
from optdash.analytics.query import fetch_strike_current as _fetch_strike_current
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
    with rollback on failure.

    EOD-3: The write phase now uses commit=False on every DAO call and issues
    a single jconn.commit() after all writes succeed. On any exception,
    jconn.rollback() restores the pre-EOD state so the next scheduler startup
    can retry the full sweep cleanly (all-or-nothing atomicity).
    """
    pending     = trades.get_pending_trades(jconn)
    open_trades = trades.get_open_trades(jconn)
    snap_time   = settings.EOD_FORCE_CLOSE_TIME

    # ── Read phase: pre-fetch all LTPs before any writes ───────────────────────
    # A DuckDB error mid-loop cannot interleave with half-written journal rows
    # if we fetch everything first and write in a separate, guarded phase.
    close_payloads: list[tuple[dict, dict]] = []
    for trade in open_trades:
        current = _fetch_strike_current(
            conn, trade_date, snap_time,
            trade["underlying"], trade["strike_price"],
            trade["expiry_date"], trade["option_type"]
        )
        # Fix EOD-2: explicit None check on actual_entry_price so a stored
        # value of 0.0 (data corruption) is not silently promoted to
        # entry_premium via the `or` falsy coercion.
        _actual = trade["actual_entry_price"]
        entry   = _actual if _actual is not None else trade["entry_premium"]

        # Fix EOD-2: explicit None check on ltp so ltp=0 (worthless/expired
        # option) records the correct -100% loss rather than being coerced
        # to `entry` by the `or` operator, which silently hid total-loss
        # outcomes from the learning engine.
        _ltp_raw = (current or {}).get("ltp")
        ltp      = _ltp_raw if _ltp_raw is not None else entry

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

    # ── Write phase: single atomic transaction ─────────────────────────────
    # EOD-3: commit=False on every DAO call keeps all writes in Python's
    # sqlite3 implicit transaction. A single commit() at the end makes
    # expires + closes land atomically. rollback() on any exception
    # restores the full pre-EOD journal state for a clean retry.
    try:
        # Step 1: Expire all pending (GENERATED) recommendations.
        for p in pending:
            trades.update_status(
                jconn, p["id"], TradeStatus.EXPIRED.value,
                state_reason="EOD sweep -- expired unseen recommendation",
                commit=False,
            )
            logger.info(
                "EOD expired pending recommendation: id={} underlying={} snap={}",
                p["id"], p["underlying"], p["snap_time"]
            )

        # Step 2: Force-close all ACCEPTED (open) positions.
        for trade, payload in close_payloads:
            trades.close_trade(jconn, trade["id"], payload, commit=False)
            logger.info(
                "EOD force-close: {} {} entry={} ltp={} pnl={:+.1f}%",
                trade["underlying"], trade["option_type"],
                trade["actual_entry_price"] or trade["entry_premium"],
                payload["exit_premium"], payload["final_pnl_pct"]
            )

        # Single flush -- all expires and closes land atomically.
        jconn.commit()

    except Exception:
        jconn.rollback()
        logger.error(
            "EOD sweep rolled back -- {} open trades and {} pending remain unchanged. "
            "Check for ACCEPTED/GENERATED trades on next startup.",
            len(open_trades), len(pending),
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
        # Fix EOD-2: explicit None check so ltp=0 (worthless shadow option)
        # records the correct -100% loss instead of being coerced to
        # entry_premium (0% PnL) by the falsy `or` operator. Shadows always
        # use entry_premium as the cost basis (no actual fill for hypotheticals).
        _ltp_raw = (current or {}).get("ltp")
        ltp      = _ltp_raw if _ltp_raw is not None else s["entry_premium"]

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
