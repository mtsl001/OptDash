"""Directional bias engine — weighted signal voting.

Signal weights:
  V_CoC velocity spike  -> weight 3  (strongest: order-flow momentum)
  Futures OBI           -> weight 2  (institutional positioning)
  VEX alignment         -> weight 2  (dealer mechanical flow)
  ATM OBI               -> weight 1  (options order imbalance)
  PCR divergence        -> weight 1  (retail sentiment contra-indicator)

Max CE/PE weight = 9 (all signals same direction).
Ties (ce_weight == pe_weight) yield NEUTRAL — no edge when signals cancel.
"""
import duckdb
from loguru import logger
from optdash.config import settings
from optdash.models import Direction
from optdash.analytics.coc import get_coc_latest, get_atm_obi, get_futures_obi
from optdash.analytics.vex_cex import get_vex_cex_current
from optdash.analytics.pcr import get_pcr


def get_directional_bias(
    conn:       duckdb.DuckDBPyConnection,
    trade_date: str,
    snap_time:  str,
    underlying: str,
) -> dict:
    try:
        coc  = get_coc_latest(conn, trade_date, snap_time, underlying)
        vex  = get_vex_cex_current(conn, trade_date, snap_time, underlying)
        obi  = get_atm_obi(conn, trade_date, snap_time, underlying)
        pcr  = get_pcr(conn, trade_date, snap_time, underlying)
        fobi = get_futures_obi(conn, trade_date, snap_time, underlying)

        signals: list[dict] = []

        # ── Signal 1: V_CoC velocity (weight 3) ─────────────────────────────
        vcoc        = coc.get("v_coc_15m") or 0
        vcoc_active = _is_vcoc_spike_active(conn, trade_date, snap_time, underlying)
        if vcoc > settings.VCOC_BULL_THRESHOLD or (vcoc_active and vcoc > 0):
            signals.append({"signal": "VCOC_BULL", "weight": 3,
                             "direction": Direction.CE.value, "value": vcoc})
        elif vcoc < settings.VCOC_BEAR_THRESHOLD or (vcoc_active and vcoc < 0):
            signals.append({"signal": "VCOC_BEAR", "weight": 3,
                             "direction": Direction.PE.value, "value": vcoc})

        # ── Signal 2: Futures OBI (weight 2) ────────────────────────────
        # FUT_OBI_BEAR_THRESHOLD is a per-underlying dict — resolve before comparison.
        fut_obi_thr = settings.FUT_OBI_BEAR_THRESHOLD.get(underlying, -0.20)
        if fobi < fut_obi_thr:
            signals.append({"signal": "FUT_OBI_BEAR", "weight": 2,
                             "direction": Direction.PE.value, "value": fobi})
        elif fobi > abs(fut_obi_thr):
            signals.append({"signal": "FUT_OBI_BULL", "weight": 2,
                             "direction": Direction.CE.value, "value": fobi})

        # ── Signal 3: VEX alignment (weight 2) ──────────────────────────
        # Fix-E: use per-underlying threshold (same dict as _classify_vex)
        # instead of bare > 0 / < 0. This prevents weight-2 ghost votes when
        # VEX noise oscillates around zero on low-liquidity underlyings.
        vex_total = vex.get("vex_total_M", 0) or 0
        vex_thr   = settings.VEX_THRESHOLDS.get(underlying, settings.VEX_BULL_THRESHOLD)
        if vex_total > vex_thr:
            signals.append({"signal": "VEX_BULL", "weight": 2,
                             "direction": Direction.CE.value, "value": vex_total})
        elif vex_total < -vex_thr:
            signals.append({"signal": "VEX_BEAR", "weight": 2,
                             "direction": Direction.PE.value, "value": vex_total})

        # ── Signal 4: ATM OBI (weight 1) ──────────────────────────────
        if obi > settings.OBI_THRESHOLD:
            signals.append({"signal": "OBI_BULL", "weight": 1,
                             "direction": Direction.CE.value, "value": obi})
        elif obi < -settings.OBI_THRESHOLD:
            signals.append({"signal": "OBI_BEAR", "weight": 1,
                             "direction": Direction.PE.value, "value": obi})

        # ── Signal 5: PCR divergence (weight 1) ─────────────────────────
        div = pcr.get("pcr_divergence", 0)
        if div > settings.PCR_DIV_BULL_THRESHOLD:
            signals.append({"signal": "PCR_RETAIL_PUTS", "weight": 1,
                             "direction": Direction.CE.value, "value": div})
        elif div < settings.PCR_DIV_BEAR_THRESHOLD:
            signals.append({"signal": "PCR_RETAIL_CALLS", "weight": 1,
                             "direction": Direction.PE.value, "value": div})

        ce_weight = sum(s["weight"] for s in signals if s["direction"] == Direction.CE.value)
        pe_weight = sum(s["weight"] for s in signals if s["direction"] == Direction.PE.value)

        # No signals at all
        if ce_weight == 0 and pe_weight == 0:
            return {"direction": Direction.NEUTRAL.value, "ce_weight": 0,
                    "pe_weight": 0, "margin": 0, "signals": []}

        # Tie — contradictory signals cancel, no tradeable edge
        if ce_weight == pe_weight:
            return {
                "direction": Direction.NEUTRAL.value,
                "ce_weight": ce_weight,
                "pe_weight": pe_weight,
                "margin":    0,
                "signals":   signals,
            }

        direction = Direction.CE.value if ce_weight > pe_weight else Direction.PE.value
        return {
            "direction": direction,
            "ce_weight": ce_weight,
            "pe_weight": pe_weight,
            "margin":    abs(ce_weight - pe_weight),
            "signals":   signals,
        }

    except Exception as e:
        logger.warning("get_directional_bias error: {}", e)
        return {"direction": Direction.NEUTRAL.value, "ce_weight": 0,
                "pe_weight": 0, "margin": 0, "signals": []}


def _is_vcoc_spike_active(
    conn:       duckdb.DuckDBPyConnection,
    trade_date: str,
    snap_time:  str,
    underlying: str,
) -> bool:
    """
    True if a V_CoC spike occurred within the last VCOC_SPIKE_EXPIRY_SNAPS snaps.
    Fetches the N most-recent distinct snap_times, then checks V_CoC for each.
    This correctly scopes memory to a rolling window rather than all-day history.
    """
    try:
        snap_rows = conn.execute("""
            SELECT DISTINCT snap_time FROM options_data
            WHERE trade_date=? AND underlying=? AND snap_time<=?
            ORDER BY snap_time DESC
            LIMIT ?
        """, [
            trade_date, underlying, snap_time,
            settings.VCOC_SPIKE_EXPIRY_SNAPS,
        ]).fetchall()

        if not snap_rows:
            return False

        threshold = abs(settings.VCOC_BULL_THRESHOLD)
        for (t,) in snap_rows:
            coc = get_coc_latest(conn, trade_date, t, underlying)
            if abs(coc.get("v_coc_15m", 0) or 0) > threshold:
                return True
        return False

    except Exception:
        return False
