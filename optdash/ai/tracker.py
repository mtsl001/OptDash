"""Live position tracking -- called every scheduler tick."""
import duckdb
import sqlite3
from loguru import logger
from optdash.config import settings
from optdash.models import IVCrushSeverity, ExitReason, TradeStatus
from optdash.analytics.environment import get_environment_score
from optdash.analytics.pnl import compute_theta_sl, compute_pnl_attribution
from optdash.ai.journal import trades, snaps


def track_open_positions(
    conn:       duckdb.DuckDBPyConnection,
    jconn:      sqlite3.Connection,
    trade_date: str,
    snap_time:  str,
) -> None:
    """Check every ACCEPTED trade and update position snaps."""
    open_trades = trades.get_open_trades(jconn)
    for trade in open_trades:
        underlying = trade["underlying"]
        strike     = trade["strike_price"]
        expiry     = trade["expiry_date"]
        opt_type   = trade["option_type"]

        current = _fetch_strike_current(
            conn, trade_date, snap_time, underlying, strike, expiry, opt_type
        )
        if not current:
            logger.warning("No data for {} {} at {}", underlying, strike, snap_time)
            continue

        ltp   = current["ltp"]
        iv    = current["iv"]
        delta = current["delta"]
        theta = current["theta"]
        vega  = current["vega"]

        # Use actual_entry_price (slippage-adjusted fill) if the trader
        # recorded one on ACCEPT. Falls back to recommended entry_premium.
        # This matches eod.py and ensures all PnL/trailing-stop math is
        # based on what the trader actually paid, not what was recommended.
        entry   = trade["actual_entry_price"] or trade["entry_premium"]
        pnl_abs = round(ltp - entry, 2)
        pnl_pct = round((pnl_abs / entry) * 100, 2) if entry else 0.0

        # Theta SL
        t_elapsed   = _minutes_since_entry(trade["snap_time"], snap_time)
        sl_adjusted = compute_theta_sl(entry, trade["theta"] or 0, t_elapsed)

        theta_sl_status = (
            "STOP_HIT"                 if ltp < sl_adjusted else
            "GUARANTEED_PROFIT_ZONE"   if pnl_pct > 50      else
            "PROFIT_ZONE_PARTIAL_EXIT" if pnl_pct > 30      else
            "IN_TRADE"
        )

        # Trailing stop
        if pnl_pct >= settings.TRAILING_STOP_ACTIVATION * 100:
            peak_ltp = snaps.get_peak_ltp(jconn, trade["id"])
            trail_sl = max(peak_ltp * 0.90, sl_adjusted)
        else:
            trail_sl = sl_adjusted

        # IV crush -- resolve per-underlying Vega threshold from config dict.
        # Vega is stored in option price points per 1% IV change; thresholds
        # are calibrated per index (see IV_CRUSH_HIGH_VEGA in config.py).
        iv_change    = iv - (trade["iv_at_entry"] or iv)
        iv_crush_thr = settings.IV_CRUSH_HIGH_VEGA.get(underlying, 15.0)
        iv_crush = (
            IVCrushSeverity.HIGH.value
            if iv_change < -3.0 and abs(vega or 0) > iv_crush_thr
            else IVCrushSeverity.LOW.value  if iv_change < -1.0
            else IVCrushSeverity.NONE.value
        )

        # Gate check
        gate       = get_environment_score(
            conn, trade_date, snap_time, underlying, direction=opt_type
        )
        gate_no_go = gate["verdict"] == "NO_GO"

        # PnL attribution
        pnl_attr = compute_pnl_attribution(
            entry={
                "spot":      trade["spot_at_entry"],
                "iv":        trade["iv_at_entry"],
                "delta":     trade["delta"],
                "gamma":     trade["gamma"],
                "vega":      trade["vega"],
                "theta":     trade["theta"],
                "premium":   entry,               # use actual entry price
                "snap_time": trade["snap_time"],
            },
            current={
                "spot":      current.get("spot"),
                "iv":        iv,
                "ltp":       ltp,
                "snap_time": snap_time,
            },
        )

        # Write snap
        snaps.insert_snap(jconn, {
            "trade_id":        trade["id"],
            "snap_time":       snap_time,
            "ltp":             ltp,
            "pnl_abs":         pnl_abs,
            "pnl_pct":         pnl_pct,
            "sl_adjusted":     trail_sl,
            "theta_sl_status": theta_sl_status,
            "iv":              iv,
            "iv_crush":        iv_crush,
            "gate_score":      gate["score"],
            "gate_verdict":    gate["verdict"],
            "delta_pnl":       pnl_attr["delta_pnl"],
            "gamma_pnl":       pnl_attr["gamma_pnl"],
            "vega_pnl":        pnl_attr["vega_pnl"],
            "theta_pnl":       pnl_attr["theta_pnl"],
            "unexplained":     pnl_attr["unexplained"],
        })

        # Auto-close logic
        exit_reason = None
        if ltp <= trail_sl:
            exit_reason = (
                ExitReason.THETA_SL_HIT.value
                if theta_sl_status == "STOP_HIT"
                else ExitReason.SL_HIT.value
            )
        elif ltp >= trade["target_price"]:
            exit_reason = ExitReason.TARGET_HIT.value
        elif gate_no_go and _consecutive_no_go_count(
            jconn, trade["id"]
        ) >= settings.GATE_SUSTAINED_NO_GO_SNAPS:
            exit_reason = ExitReason.GATE_NO_GO.value
        elif iv_crush == IVCrushSeverity.HIGH.value and pnl_pct < -15:
            exit_reason = ExitReason.IV_CRUSH.value

        if exit_reason:
            trades.close_trade(jconn, trade["id"], {
                "exit_premium":   ltp,
                "exit_snap_time": snap_time,
                "exit_reason":    exit_reason,
                "final_pnl_abs":  pnl_abs,
                "final_pnl_pct":  pnl_pct,
            })
            logger.info(
                "Auto-closed {} {} @ {:.1f} | reason={} | pnl={:+.1f}%",
                underlying, opt_type, ltp, exit_reason, pnl_pct
            )


def expire_stale_recommendations(
    jconn:      sqlite3.Connection,
    trade_date: str,
    snap_time:  str,
) -> None:
    """Mark GENERATED trades as EXPIRED after AI_EXPIRY_MAX_SNAPS unactioned."""
    pending = trades.get_pending_trades(jconn)
    for trade in pending:
        age = _snaps_since(trade["snap_time"], snap_time)
        if age >= settings.AI_EXPIRY_MAX_SNAPS:
            trades.update_status(
                jconn, trade["id"], TradeStatus.EXPIRED.value,
                state_reason="Not actioned within expiry window"
            )


def _fetch_strike_current(
    conn, trade_date, snap_time, underlying, strike_price, expiry, option_type
) -> dict | None:
    """Fetch the most-recent options row at or before snap_time for the given contract.

    Fix-C: changed from exact snap_time=? to snap_time<=? with
    ORDER BY snap_time DESC LIMIT 1.

    Rationale: during EOD force-close (15:20-15:25) the scheduler may request
    a snap that has not yet been committed to DuckDB due to BQ feed latency.
    An exact match returns None in that window, causing tracker/eod.py to fall
    back to pnl=0 and write a phantom zero-PnL closure in the journal.
    The <= query always returns the last known LTP for the contract, so exit
    premium and PnL are always based on real market data.
    """
    try:
        row = conn.execute("""
            SELECT ltp, iv, delta, theta, gamma, vega, spot
            FROM options_data
            WHERE trade_date=? AND snap_time<=? AND underlying=?
              AND strike_price=? AND expiry_date=? AND option_type=?
            ORDER BY snap_time DESC
            LIMIT 1
        """, [trade_date, snap_time, underlying,
               strike_price, expiry, option_type]).fetchone()
        if not row:
            return None
        return dict(zip(["ltp", "iv", "delta", "theta", "gamma", "vega", "spot"], row))
    except Exception as e:
        logger.warning("_fetch_strike_current error: {}", e)
        return None


def _minutes_since_entry(entry_snap: str, current_snap: str) -> int:
    try:
        h1, m1 = map(int, entry_snap[:5].split(":"))
        h2, m2 = map(int, current_snap[:5].split(":"))
        return max(0, (h2 * 60 + m2) - (h1 * 60 + m1))
    except Exception:
        return 0


def _snaps_since(entry_snap: str, current_snap: str) -> int:
    return _minutes_since_entry(entry_snap, current_snap) // 5


def _consecutive_no_go_count(jconn: sqlite3.Connection, trade_id: int) -> int:
    try:
        rows = jconn.execute("""
            SELECT gate_verdict FROM position_snaps
            WHERE trade_id=? ORDER BY snap_time DESC LIMIT 10
        """, [trade_id]).fetchall()
        count = 0
        for r in rows:
            if r[0] == "NO_GO":
                count += 1
            else:
                break
        return count
    except Exception:
        return 0
