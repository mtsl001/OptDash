"""Pre-flight checks — 8 hard blocking rules."""
from optdash.config import settings
from optdash.models import MarketSession


def run_pre_flight(
    gate_score:            int,
    confidence:            int,
    strike:                dict,
    gex_data:              dict,
    session:               MarketSession,
    existing_open_trades:  int,
    dealer_oclock:         bool,
) -> tuple[bool, list[str]]:
    """Returns (passed: bool, failures: list[str])"""
    failures = []

    # Rule 1: Gate score floor
    if gate_score < settings.PREFLIGHT_MIN_GATE_SCORE:
        failures.append(
            f"Gate {gate_score} below minimum {settings.PREFLIGHT_MIN_GATE_SCORE}"
        )

    # Rule 2: Confidence floor
    if confidence < settings.PREFLIGHT_MIN_CONFIDENCE:
        failures.append(
            f"Confidence {confidence}% below minimum {settings.PREFLIGHT_MIN_CONFIDENCE}%"
        )

    # Rule 3: Theta/premium ratio
    theta = abs(strike.get("theta") or 0)
    ltp   = strike.get("ltp") or 0
    if ltp > 0 and (theta / ltp) > settings.PREFLIGHT_MAX_THETA_RATIO:
        failures.append(
            f"Theta/premium {theta/ltp:.1%} exceeds {settings.PREFLIGHT_MAX_THETA_RATIO:.0%} — "
            f"option loses too much per day relative to price"
        )

    # Rule 4: Max Pain proximity
    # F7: use explicit None check instead of `or 1.0` falsy guard.
    # max_pain_distance_pct=0.0 means spot is exactly on max pain (most
    # dangerous stop-hunt zone). The old `or 1.0` coerced 0.0 to 1.0,
    # disabling the proximity block in the exact scenario it must fire.
    # Default 99.0 (safely far) only when the key is genuinely absent.
    raw_dist      = gex_data.get("max_pain_distance_pct")
    max_pain_dist = raw_dist if raw_dist is not None else 99.0
    if abs(max_pain_dist) < settings.PREFLIGHT_MAX_PAIN_PROXIMITY * 100:
        failures.append(
            f"Spot within {abs(max_pain_dist):.2f}% of max pain "
            f"(threshold {settings.PREFLIGHT_MAX_PAIN_PROXIMITY*100:.1f}%) — stop-hunt zone"
        )

    # Rule 5: S_score floor
    if (strike.get("s_score") or 0) < settings.PREFLIGHT_MIN_SSCORE:
        failures.append(
            f"S_score {strike.get('s_score', 0):.1f} below floor {settings.PREFLIGHT_MIN_SSCORE}"
        )

    # Rule 6: No concurrent trades (per underlying)
    # F3 (recommender): message now names the specific underlying so the
    # pre-flight log is unambiguous when multiple underlyings are tracked.
    if existing_open_trades > 0:
        underlying = strike.get("underlying", "this underlying")
        failures.append(
            f"{existing_open_trades} trade(s) already open for {underlying} — "
            f"one position per underlying allowed"
        )

    # Rule 7: DTE<=1 elevated requirements
    # F6: changed from strict `dte == 1` to `(dte or 99) <= 1`.
    # DTE=0 (expiry morning, 09:15-14:00) is even higher risk than DTE=1
    # (day before expiry) but was silently skipped by the old equality check.
    dte = strike.get("dte")
    if (dte if dte is not None else 99) <= 1:
        if gate_score < settings.PREFLIGHT_DTE1_MIN_GATE:
            failures.append(
                f"DTE≤1 requires gate >= {settings.PREFLIGHT_DTE1_MIN_GATE}, got {gate_score}"
            )
        if confidence < settings.PREFLIGHT_DTE1_MIN_CONFIDENCE:
            failures.append(
                f"DTE≤1 requires confidence >= {settings.PREFLIGHT_DTE1_MIN_CONFIDENCE}%, "
                f"got {confidence}%"
            )

    # Rule 8: Dealer O'Clock hard block on DTE<=1
    if dealer_oclock and (strike.get("dte") or 99) <= 1:
        failures.append(
            "DEALER O'CLOCK on expiry day — charm distortion blocks entry"
        )

    return (len(failures) == 0, failures)
