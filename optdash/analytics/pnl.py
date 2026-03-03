"""PnL analytics — theta SL, Greeks attribution, theta clock."""
from typing import Any
from loguru import logger


def compute_theta_sl(entry_premium: float, theta: float, minutes_elapsed: int) -> float:
    """
    Theta-adjusted stop loss.
    SL rises as theta erodes time value — locks in decay-adjusted minimum exit.
    theta is negative (daily), so daily = theta/6.5 per hour.
    """
    theta_per_min = abs(theta or 0) / (6.5 * 60)
    decay         = theta_per_min * minutes_elapsed
    adjusted_entry= max(0.01, entry_premium - decay)
    return round(adjusted_entry * 0.65, 2)


def compute_pnl_attribution(
    entry:   dict[str, Any],
    current: dict[str, Any],
) -> dict[str, float]:
    """
    Greeks-based PnL decomposition.
    Actual PnL = delta_pnl + gamma_pnl + vega_pnl + theta_pnl + unexplained.
    """
    try:
        spot_chg = (current.get("spot", 0) or 0) - (entry.get("spot", 0) or 0)
        iv_chg   = (current.get("iv",   0) or 0) - (entry.get("iv",   0) or 0)

        entry_t   = entry.get("snap_time",   "09:15")
        current_t = current.get("snap_time", "09:15")
        minutes   = _minutes_between(entry_t, current_t)

        delta  = entry.get("delta",  0) or 0
        gamma  = entry.get("gamma",  0) or 0
        vega   = entry.get("vega",   0) or 0
        theta  = entry.get("theta",  0) or 0

        delta_pnl  = round(delta * spot_chg, 2)
        gamma_pnl  = round(0.5 * gamma * spot_chg ** 2, 2)
        vega_pnl   = round(vega * iv_chg, 2)
        theta_pnl  = round(theta / (6.5 * 60) * minutes, 2)

        theo_pnl   = delta_pnl + gamma_pnl + vega_pnl + theta_pnl
        entry_p    = entry.get("premium", 0)  or 0
        actual_ltp = current.get("ltp",   0)  or 0
        actual_pnl = round(actual_ltp - entry_p, 2)
        unexplained= round(actual_pnl - theo_pnl, 2)

        return {
            "delta_pnl": delta_pnl, "gamma_pnl": gamma_pnl,
            "vega_pnl":  vega_pnl,  "theta_pnl":  theta_pnl,
            "theoretical_pnl": theo_pnl,
            "actual_pnl": actual_pnl,
            "unexplained": unexplained,
        }
    except Exception as e:
tml        logger.warning("compute_pnl_attribution error: {}", e)
        return {"delta_pnl": 0, "gamma_pnl": 0, "vega_pnl": 0,
                "theta_pnl": 0, "theoretical_pnl": 0, "actual_pnl": 0, "unexplained": 0}


def compute_theta_clock(theta: float, dte: int, ltp: float, target: float) -> dict:
    """
    Time remaining before theta erosion exceeds the gap to target.
    Returns hours_remaining, is_urgent.
    """
    theta_per_hour = abs(theta or 0) / 6.5
    gap_to_target  = max(0, (target or ltp) - (ltp or 0))
    if theta_per_hour <= 0:
        return {"hours_remaining": None, "is_urgent": False}
    hours = gap_to_target / theta_per_hour
    return {"hours_remaining": round(hours, 1), "is_urgent": hours < 1.5}


def build_theta_sl_series(trade: dict, snaps: list[dict]) -> list[dict]:
    """Build per-snap theta SL series for the position chart."""
    result = []
    entry  = trade["entry_premium"]
    theta  = trade.get("theta") or 0
    entry_t= trade.get("snap_time", "09:15")
    for snap in snaps:
        mins = _minutes_between(entry_t, snap["snap_time"])
        sl   = compute_theta_sl(entry, theta, mins)
        result.append({
            "snap_time":      snap["snap_time"],
            "entry_premium":  entry,
            "theta_daily":    theta,
            "sl_base":        round(entry * 0.65, 2),
            "sl_adjusted":    sl,
            "current_ltp":    snap.get("ltp"),
            "unrealised_pnl": round((snap.get("ltp") or entry) - entry, 2),
            "pnl_pct":        snap.get("pnl_pct"),
            "status":         snap.get("theta_sl_status", "IN_TRADE"),
            "delta_pnl":      snap.get("delta_pnl"),
            "gamma_pnl":      snap.get("gamma_pnl"),
            "vega_pnl":       snap.get("vega_pnl"),
            "theta_pnl":      snap.get("theta_pnl"),
            "unexplained":    snap.get("unexplained"),
        })
    return result


def _minutes_between(t1: str, t2: str) -> int:
    """Minutes between two HH:MM strings."""
    try:
        h1, m1 = map(int, t1[:5].split(":"))
        h2, m2 = map(int, t2[:5].split(":"))
        return max(0, (h2 * 60 + m2) - (h1 * 60 + m1))
    except Exception:
        return 0
