"""Full recommendation generation flow — orchestrates all AI modules."""
import json
import duckdb
import sqlite3
from loguru import logger
from zoneinfo import ZoneInfo

from optdash.config import settings
from optdash.models import Direction, TradeStatus
from optdash.analytics.environment import get_environment_score, get_market_session
from optdash.analytics.gex import get_net_gex, get_max_pain
from optdash.analytics.iv import get_ivr_ivp
# Fix-G: import retained as fallback for when direction.py returns no vex_data
# (e.g. exception path). Primary path reads vex_data from dir_res["vex_data"].
from optdash.analytics.vex_cex import get_vex_cex_current
from optdash.analytics.screener import get_strikes
from optdash.ai.direction import get_directional_bias
from optdash.ai.confidence import compute_confidence
from optdash.ai.narrative import build_narrative
from optdash.ai.pre_flight import run_pre_flight
from optdash.ai.quality import compute_quality_score
from optdash.ai.journal import trades
from optdash.ai.learning import stats

IST = ZoneInfo("Asia/Kolkata")


def generate_recommendation(
    conn:       duckdb.DuckDBPyConnection,
    jconn:      sqlite3.Connection,
    trade_date: str,
    snap_time:  str,
    underlying: str,
) -> dict | None:
    """
    Called every scheduler tick for each underlying.
    Returns the generated trade card dict, or None if no recommendation issued.
    """
    # Guard: open position exists — never recommend while in trade
    open_trades = trades.get_open_trades(jconn, underlying=underlying)
    if open_trades:
        return None

    # Guard: pending recommendation already issued
    pending = trades.get_pending_trades(jconn, underlying=underlying)
    if pending:
        return None

    # ── Step 1: Direction first — bail early on NEUTRAL to skip expensive calls ───
    dir_res = get_directional_bias(conn, trade_date, snap_time, underlying)
    if dir_res["direction"] == Direction.NEUTRAL.value:
        return None

    direction = dir_res["direction"]
    session   = get_market_session(snap_time)

    # ── Step 2: Gate — direction-aware so C9 (VEX, 2 pts) scores correctly ─────
    # Passing direction= ensures C9 awards points only when VEX aligns with the
    # actual CE/PE trade direction, not whenever VEX is non-zero.
    gate = get_environment_score(conn, trade_date, snap_time, underlying, direction=direction)

    # ── Step 3: Supporting analytics ──────────────────────────────────────────
    iv_data  = get_ivr_ivp(conn, trade_date, snap_time, underlying)
    gex_data = get_net_gex(conn, trade_date, snap_time, underlying)
    # Fix-G: get_directional_bias() already called get_vex_cex_current() internally
    # and exposes the result in dir_res["vex_data"]. Read it directly to avoid a
    # second identical DuckDB round-trip. The 'or' fallback handles the rare case
    # where direction.py hit an exception before computing vex (error return omits
    # the key), keeping correctness without coupling to direction.py internals.
    vex_data = dir_res.get("vex_data") or \
               get_vex_cex_current(conn, trade_date, snap_time, underlying)

    dealer_oc = vex_data.get("dealer_oclock", False)

    nearest_expiry = _nearest_expiry(conn, trade_date, snap_time, underlying)
    max_pain = (
        get_max_pain(conn, trade_date, snap_time, underlying, expiry_date=nearest_expiry)
        if nearest_expiry else {}
    )

    # ── Best strike selection ────────────────────────────────────────────────
    strike_list = get_strikes(
        conn, trade_date, snap_time, underlying, top_n=settings.SCREENER_TOP_N
    )
    candidates = [s for s in strike_list if s["option_type"] == direction]
    if not candidates:
        return None
    strike = candidates[0]

    # Guard: zero or negative LTP means illiquid / expired strike — skip
    entry_premium = strike.get("ltp") or 0
    if entry_premium <= 0:
        logger.warning(
            "Zero LTP for {} {} {} — skipping recommendation",
            underlying, direction, strike.get("strike_price")
        )
        return None

    # ── Learning context (session + direction specific win-rate) ────────────
    learning = stats.get_session_stats(
        jconn, underlying=underlying, direction=direction, session=session
    )

    # ── Confidence score ──────────────────────────────────────────────────
    conf_result = compute_confidence(
        gate_score=gate["score"],
        direction_result=dir_res,
        iv_data=iv_data,
        gex_data=gex_data,
        vex_data=vex_data,
        strike=strike,
        learning_stats=learning,
        session=session,
    )
    confidence = conf_result["confidence"]

    # ── Pre-flight hard rules ─────────────────────────────────────────────
    passed, failures = run_pre_flight(
        gate_score=gate["score"],
        confidence=confidence,
        strike=strike,
        gex_data={**gex_data, "max_pain_distance_pct": max_pain.get("distance_pct", 99)},
        session=session,
        existing_open_trades=len(open_trades),
        dealer_oclock=dealer_oc,
    )
    if not passed:
        logger.info(
            "Pre-flight failed for {} {}: {}", underlying, snap_time, failures
        )
        return None

    # ── SL / Target ─────────────────────────────────────────────────────
    sl     = round(entry_premium * (1 - settings.AI_SL_PCT), 2)
    target = round(entry_premium * settings.AI_TARGET_MULT, 2)

    # ── Quality grade ──────────────────────────────────────────────────
    quality = compute_quality_score(strike, gate["score"], confidence)

    # ── Narrative ─────────────────────────────────────────────────────────
    narrative = build_narrative(
        direction=direction,
        gate_score=gate["score"],
        gate_verdict=gate["verdict"],
        direction_signals=dir_res["signals"],
        iv_data=iv_data,
        gex_data=gex_data,
        vex_data=vex_data,
        session=session,
        dealer_oclock=dealer_oc,
    )

    # ── Write to journal ──────────────────────────────────────────────────────
    trade_id = trades.create_trade(jconn, {
        "trade_date":        trade_date,
        "snap_time":         snap_time,
        "underlying":        underlying,
        "option_type":       direction,
        "strike_price":      strike["strike_price"],
        "expiry_date":       strike["expiry_date"],
        "entry_premium":     entry_premium,
        "sl_price":          sl,
        "target_price":      target,
        "confidence":        confidence,
        "gate_score":        gate["score"],
        "gate_verdict":      gate["verdict"],
        "s_score":           strike["s_score"],
        "quality_grade":     quality["grade"],
        "direction_signals": json.dumps(dir_res["signals"]),
        "narrative":         narrative,
        "status":            TradeStatus.GENERATED.value,
        "session":           session.value,
        "delta":             strike.get("delta"),
        "theta":             strike.get("theta"),
        "vega":              strike.get("vega"),
        "gamma":             strike.get("gamma"),
        "iv_at_entry":       strike.get("iv"),
        "spot_at_entry":     gex_data.get("spot"),
        "dte":               strike.get("dte"),
        "conf_buckets":      json.dumps(conf_result["buckets"]),
    })

    logger.info(
        "Recommendation: {} {} {} @ {:.1f} | conf={}% gate={} session={}",
        underlying, direction, strike["strike_price"],
        entry_premium, confidence, gate["score"], session.value,
    )
    return trades.get_trade(jconn, trade_id)


def _nearest_expiry(
    conn:       duckdb.DuckDBPyConnection,
    trade_date: str,
    snap_time:  str,
    underlying: str,
) -> str | None:
    try:
        row = conn.execute("""
            SELECT MIN(expiry_date) FROM options_data
            WHERE trade_date=? AND snap_time=? AND underlying=?
              AND expiry_date >= trade_date
        """, [trade_date, snap_time, underlying]).fetchone()
        return row[0] if row else None
    except Exception:
        return None
