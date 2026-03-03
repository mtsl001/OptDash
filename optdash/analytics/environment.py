"""11-point Environment Gate — GO / WAIT / NO_GO verdict."""
import duckdb
from loguru import logger
from optdash.config import settings
from optdash.models import GateVerdict, MarketSession
from optdash.analytics.gex import get_net_gex, _get_gex_peak
from optdash.analytics.coc import get_coc_latest, get_atm_obi, get_futures_obi
from optdash.analytics.iv  import get_ivr_ivp
from optdash.analytics.pcr import get_pcr
from optdash.analytics.vex_cex import get_vex_cex_current, _is_dealer_oclock


def get_environment_score(
    conn: duckdb.DuckDBPyConnection,
    trade_date: str,
    snap_time:  str,
    underlying: str,
    direction:  str | None = None,
) -> dict:
    """
    11-point environment gate.
    Conditions 1-8 are standard (max 9 pts).
    Conditions 9-11 are starred — extra weight (max 2 extra pts).
    Returns score, verdict, per-condition breakdown.
    """
    try:
        gex_data = get_net_gex(conn, trade_date, snap_time, underlying)
        coc_data = get_coc_latest(conn, trade_date, snap_time, underlying)
        iv_data  = get_ivr_ivp(conn, trade_date, snap_time, underlying)
        pcr_data = get_pcr(conn, trade_date, snap_time, underlying)
        vex_data = get_vex_cex_current(conn, trade_date, snap_time, underlying)
        atm_obi  = get_atm_obi(conn, trade_date, snap_time, underlying)
        fut_obi  = get_futures_obi(conn, trade_date, snap_time, underlying)

        # Extract values (safe defaults if data missing)
        gex_pct     = gex_data.get("pct_of_peak", 100.0)
        vcoc        = coc_data.get("v_coc_15m", 0.0)
        fut_bs      = fut_obi
        pcr_div     = pcr_data.get("pcr_divergence", 0.0)
        ivp         = iv_data.get("ivp", 100.0)
        obi         = atm_obi
        vex_total   = vex_data.get("vex_total_M", 0.0)
        dealer_oc   = vex_data.get("dealer_oclock", False)
        dte         = vex_data.get("dte", 7)

        conditions: dict[str, dict] = {}

        # Condition 1: GEX declining (1 pt)
        c1_met = gex_pct <= settings.GEX_DECLINE_THRESHOLD * 100
        conditions["gex_declining"] = {
            "met": c1_met, "value": round(gex_pct, 1),
            "points": 1, "note": f"{gex_pct:.0f}% of day peak"
        }

        # Condition 2: V_CoC velocity (1 pt)
        c2_met = abs(vcoc) > abs(settings.VCOC_BULL_THRESHOLD)
        conditions["vcoc_signal"] = {
            "met": c2_met, "value": round(vcoc, 2),
            "points": 1, "note": f"V_CoC 15m = {vcoc:+.2f}"
        }

        # Condition 3: Futures OBI bearish (1 pt)
        c3_met = fut_bs < settings.FUT_OBI_BEAR_THRESHOLD
        conditions["fut_bs_ratio"] = {
            "met": c3_met, "value": round(fut_bs, 4),
            "points": 1, "note": f"Fut OBI = {fut_bs:.3f}"
        }

        # Condition 4: PCR divergence (1 pt)
        c4_met = (abs(pcr_div) > 0.15)
        conditions["pcr_divergence"] = {
            "met": c4_met, "value": round(pcr_div, 4),
            "points": 1, "note": f"Divergence = {pcr_div:+.4f}"
        }

        # Condition 5: IV cheap (IVP < 50) (1 pt)
        c5_met = (ivp or 100) < 50
        conditions["ivp_cheap"] = {
            "met": c5_met, "value": round(ivp or 100, 1),
            "points": 1, "note": f"IVP = {ivp:.0f}th pct"
        }

        # Condition 6: ATM OBI significant (1 pt)
        c6_met = abs(obi) > settings.OBI_THRESHOLD
        conditions["obi_negative"] = {
            "met": c6_met, "value": round(obi, 4),
            "points": 1, "note": f"ATM OBI = {obi:+.4f}"
        }

        # Condition 7: IV term structure not backwardation (1 pt)
        ts = iv_data.get("shape", "FLAT")
        c7_met = ts != "BACKWARDATION"
        conditions["term_structure_ok"] = {
            "met": c7_met, "value": ts,
            "points": 1, "note": f"Shape = {ts}"
        }

        # Condition 8: Session not midday chop (1 pt)
        session = get_market_session(snap_time)
        c8_met  = session != MarketSession.MIDDAY_CHOP
        conditions["session_ok"] = {
            "met": c8_met, "value": session.value,
            "points": 1, "note": f"Session = {session.value}"
        }

        # Condition 9: VEX aligned with direction ★ (2 pts)
        c9_met = False
        if direction == "CE" and vex_total > 0:
            c9_met = True
        elif direction == "PE" and vex_total < 0:
            c9_met = True
        elif direction is None and abs(vex_total) > 0:
            c9_met = True
        conditions["vex_aligned"] = {
            "met": c9_met, "value": round(vex_total, 2),
            "points": 2, "note": "VEX mechanical alignment ★ (2 pts)"
        }

        # Condition 10: Not Dealer O'Clock on DTE=1 ★ (bonus if safe)
        c10_met = not dealer_oc
        conditions["not_charm_distortion"] = {
            "met": c10_met, "value": "SAFE" if c10_met else "DEALER_OCLOCK",
            "points": 1, "note": "Dealer O'Clock guard ★"
        }

        # Tally score
        score = sum(c["points"] for c in conditions.values() if c["met"])
        score = min(score, settings.GATE_MAX_SCORE)

        verdict = (
            GateVerdict.GO.value    if score >= settings.GATE_GO_THRESHOLD   else
            GateVerdict.WAIT.value  if score >= settings.GATE_WAIT_THRESHOLD  else
            GateVerdict.NO_GO.value
        )

        return {
            "score": score,
            "max_score": settings.GATE_MAX_SCORE,
            "verdict": verdict,
            "conditions": conditions,
            "session": session.value,
        }
    except Exception as e:
        logger.warning("get_environment_score error: {}", e)
        return {"score": 0, "max_score": settings.GATE_MAX_SCORE,
                "verdict": GateVerdict.NO_GO.value, "conditions": {}}


def get_market_session(snap_time: str) -> MarketSession:
    """Return the market session bucket for a given snap_time (HH:MM)."""
    if snap_time <= settings.SESSION_OPENING_END:
        return MarketSession.OPENING
    if snap_time <= settings.SESSION_MIDDAY_START:
        return MarketSession.MIDMORNING
    if snap_time <= settings.SESSION_MIDDAY_END:
        return MarketSession.MIDDAY_CHOP
    if snap_time <= settings.SESSION_CLOSING_START:
        return MarketSession.AFTERNOON
    return MarketSession.CLOSING_CRUSH
