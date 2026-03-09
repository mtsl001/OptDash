"""Confidence scoring — four independent buckets, capped at 100."""
from optdash.config import settings
from optdash.models import MarketSession, GEXRegime


def compute_confidence(
    gate_score:       int,
    direction_result: dict,
    iv_data:          dict,
    gex_data:         dict,
    vex_data:         dict,
    strike:           dict,
    learning_stats:   dict,
    session:          MarketSession,
) -> dict:
    """
    Bucket 1: Signal Alignment     (max 40 pts)
    Bucket 2: Gate Score           (max 25 pts)
    Bucket 3: Structural Quality   (max 25 pts)
    Bucket 4: Historical Perf      (max 10 pts)
    """
    margin       = direction_result.get("margin", 0)
    signal_count = len(direction_result.get("signals", []))
    direction    = direction_result.get("direction", "")

    # Bucket 1: signal strength
    b1 = min(40, margin * 8 + signal_count * 2)

    # Bucket 2: gate adequacy
    # F5: multiplier was 30 with cap 25, so gate=10 and gate=11 both yielded
    # b2=25 (the cap). The best gate score got no extra credit over near-best.
    # Fix: set multiplier == cap (25) so the mapping is linear and each gate
    # step produces a unique b2 value up to the cap at perfect score.
    gate_max = settings.GATE_MAX_SCORE or 10
    b2 = min(25, int((gate_score / gate_max) * 25))

    # Bucket 3: structural quality
    # Guard ivp with explicit None check — ivp=0 is valid (historically cheapest IV)
    # and must NOT be coerced to 100 via falsy `or` operator.
    ivp_val = iv_data.get("ivp")
    b3 = 0
    if (ivp_val if ivp_val is not None else 100) < 50: b3 += 6
    if iv_data.get("shape") == "CONTANGO":              b3 += 4
    # S_score threshold aligned with 0–150 scale (STAR_3 floor = 80).
    # Previously 10 (bottom 7% of scale) — now 80 (top 47%) so structural
    # quality points only fire for genuinely above-average strikes.
    if (strike.get("s_score") or 0) > 80:              b3 += 7
    # NEGATIVE_TREND and POSITIVE_DECLINING are both directionally favourable
    # environments for options buyers — gamma wall is absent or weakening.
    gex_regime = gex_data.get("regime", "")
    if gex_regime in (GEXRegime.NEGATIVE_TREND.value, GEXRegime.POSITIVE_DECLINING.value):
        b3 += 5
    vex_sig = vex_data.get("vex_signal", "")
    if vex_sig == "VEX_BULLISH" and direction == "CE":  b3 += 3
    if vex_sig == "VEX_BEARISH" and direction == "PE":  b3 += 3
    b3 = min(25, b3)

    # Bucket 4: historical performance
    # F14b: default win_rate=50.0 gave B4=int(0.5*12)=6 even with zero closed
    # trades, granting historical credit before any track record exists.
    # Gate on total_trades: return 0 until at least 5 real closed trades.
    # F15: if stats fell back to global data (thin specific bucket), cap B4
    # at 5 to avoid over-crediting a broad average on an untested bucket.
    total_trades = learning_stats.get("total_trades", 0)
    if total_trades >= 5:
        win_rate = learning_stats.get("win_rate", 50.0) / 100
        b4 = min(10, int(win_rate * 12))
        # Cap when global fallback is thin (< 20 trades in the specific bucket)
        if learning_stats.get("is_fallback") and total_trades < 20:
            b4 = min(b4, 5)
    else:
        b4 = 0  # no track record — no historical credit

    raw = b1 + b2 + b3 + b4

    # Session adjustments
    if session == MarketSession.MIDDAY_CHOP:
        raw -= settings.SESSION_MIDDAY_CONFIDENCE_PENALTY
    if session == MarketSession.CLOSING_CRUSH:
        # Fix-D: SESSION_CLOSING_CONFIDENCE_CAP is an UPPER BOUND on confidence
        # during the closing session. It prevents overconfident late entries
        # when market micro-structure degrades after 14:30.
        # It does NOT act as a minimum requirement -- use PREFLIGHT_MIN_CONFIDENCE
        # to enforce a floor.
        raw = min(raw, settings.SESSION_CLOSING_CONFIDENCE_CAP)

    confidence = max(0, min(100, raw))

    return {
        "confidence": confidence,
        "buckets": {
            "signal_alignment": b1,
            "gate_score":       b2,
            "structural":       b3,
            "historical":       b4,
        },
        "session_adjusted": raw != (b1 + b2 + b3 + b4),
    }
