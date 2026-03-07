"""AI endpoints - recommendation, accept/reject, position, journal, learning."""
import json
import sqlite3
from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel

from optdash.api.deps import get_journal
from optdash.ai.journal import trades, shadow, snaps
from optdash.ai.learning.report import build_learning_report
from optdash.analytics.pnl import build_theta_sl_series
from optdash.models import TradeStatus, ExitReason

router = APIRouter()

DEFAULT_UNDERLYING = "NIFTY"


# -- Request schemas ----------------------------------------------------------

class AcceptRequest(BaseModel):
    trade_id:           int
    snap_time:          str
    actual_entry_price: float | None = None


class RejectRequest(BaseModel):
    trade_id: int
    reason:   str
    note:     str | None = None


class CloseRequest(BaseModel):
    trade_id:   int
    exit_price: float
    snap_time:  str


# -- Response helpers ---------------------------------------------------------

def _hydrate_trade(trade: dict | None) -> dict | None:
    """Deserialize JSON-string fields in a raw journal trade dict.

    Fix-I: recommender.py stores conf_buckets and direction_signals via
    json.dumps() so they land in SQLite as text columns. Without this
    step, API consumers receive raw string literals instead of structured
    objects, requiring client-side double-parsing.

    Fields deserialized:
      conf_buckets      (str -> dict)  fallback: {}
      direction_signals (str -> list)  fallback: []

    All other fields are passed through unchanged. None input returns None.
    """
    if trade is None:
        return None
    result = dict(trade)
    _JSON_FIELDS: dict[str, object] = {
        "conf_buckets":      {},
        "direction_signals": [],
    }
    for field, default in _JSON_FIELDS.items():
        raw = result.get(field)
        if isinstance(raw, str):
            try:
                result[field] = json.loads(raw)
            except (ValueError, TypeError):
                result[field] = default
    return result


# -- Endpoints ----------------------------------------------------------------

@router.get("/recommendation/latest")
def latest_recommendation(
    underlying: str = Query(DEFAULT_UNDERLYING),
    jconn: sqlite3.Connection = Depends(get_journal),
):
    pending = trades.get_pending_trades(jconn, underlying=underlying)
    if not pending:
        return {"status": "NO_RECOMMENDATION"}
    return _hydrate_trade(pending[0])   # index 0 = most-recent (ORDER BY created_at DESC)


@router.get("/position/live")
def live_position(
    underlying: str = Query(DEFAULT_UNDERLYING),
    jconn: sqlite3.Connection = Depends(get_journal),
):
    open_trades = trades.get_open_trades(jconn, underlying=underlying)
    if not open_trades:
        return {"status": "NO_POSITION"}
    return _hydrate_trade(open_trades[0])


@router.get("/position/snaps/{trade_id}")
def position_snaps(
    trade_id: int,
    jconn: sqlite3.Connection = Depends(get_journal),
):
    """Full snap history: PnL, Greeks attribution, IV crush, theta SL series."""
    trade = trades.get_trade(jconn, trade_id)
    if not trade:
        raise HTTPException(404, "Trade not found")
    snap_list = snaps.get_snaps_for_trade(jconn, trade_id)
    return {
        "trade":           _hydrate_trade(trade),
        "snaps":           snap_list,
        "theta_sl_series": build_theta_sl_series(trade, snap_list),
    }


@router.post("/accept")
def accept(
    req:   AcceptRequest,
    jconn: sqlite3.Connection = Depends(get_journal),
):
    trade = trades.get_trade(jconn, req.trade_id)
    if not trade:
        raise HTTPException(404, "Trade not found")
    if trade["status"] != TradeStatus.GENERATED.value:
        raise HTTPException(
            400, f"Cannot accept trade in status '{trade['status']}'"
        )

    # Fix-J (F-03): Removed shadow.create_shadow() from the accept path.
    # Shadows are ONLY for rejected/expired trades (what-if tracking).
    # Creating a shadow on accept was corrupting the learning engine:
    # when the shadow hit its mechanical target it was classified as
    # CLEAN_MISS (costly rejection), inverting the learning signal for
    # trades that were actually taken and won.
    trades.accept_trade(jconn, req.trade_id, req.snap_time, req.actual_entry_price)
    return {"status": "accepted", "trade_id": req.trade_id}


@router.post("/reject")
def reject(
    req:   RejectRequest,
    jconn: sqlite3.Connection = Depends(get_journal),
):
    trade = trades.get_trade(jconn, req.trade_id)
    if not trade:
        raise HTTPException(404, "Trade not found")

    # Only GENERATED recommendations can be rejected.
    # Live positions (ACCEPTED) must be closed via /close-trade.
    if trade["status"] != TradeStatus.GENERATED.value:
        raise HTTPException(
            400,
            f"Can only reject GENERATED recommendations "
            f"(current status='{trade['status']}'). "
            "Use /close-trade to exit a live position."
        )

    # Shadow: track what would have happened had we taken this recommendation.
    # This is the core of the learning engine - CLEAN_MISS vs GOOD_SKIP.
    shadow.create_shadow(jconn, {
        "trade_id":      req.trade_id,
        "trade_date":    trade["trade_date"],
        "underlying":    trade["underlying"],
        "option_type":   trade["option_type"],
        "strike_price":  trade["strike_price"],
        "expiry_date":   trade["expiry_date"],
        "entry_premium": trade["entry_premium"],
    })

    trades.reject_trade(jconn, req.trade_id, req.reason, req.note)
    return {"status": "rejected", "trade_id": req.trade_id}


@router.post("/close-trade")
def close_position(
    req:   CloseRequest,
    jconn: sqlite3.Connection = Depends(get_journal),
):
    """Manually close a live (ACCEPTED) position."""
    trade = trades.get_trade(jconn, req.trade_id)
    if not trade:
        raise HTTPException(404, "Trade not found")
    if trade["status"] != TradeStatus.ACCEPTED.value:
        raise HTTPException(400, f"Trade not open (status='{trade['status']}')")

    entry   = trade["actual_entry_price"] or trade["entry_premium"]
    pnl_abs = round(req.exit_price - entry, 2)
    pnl_pct = round(pnl_abs / entry * 100, 2)

    trades.close_trade(jconn, req.trade_id, {
        "exit_premium":   req.exit_price,
        "exit_snap_time": req.snap_time,
        "exit_reason":    ExitReason.MANUAL_EXIT.value,
        "final_pnl_abs":  pnl_abs,
        "final_pnl_pct":  pnl_pct,
    })
    return {"status": "closed", "trade_id": req.trade_id, "pnl_pct": pnl_pct}


@router.get("/journal/history")
def trade_history(
    page:       int        = Query(1,  ge=1),
    per_page:   int        = Query(20, ge=5, le=100),
    underlying: str | None = Query(None),
    status:     str | None = Query(None),
    jconn: sqlite3.Connection = Depends(get_journal),
):
    result = trades.get_trade_history(
        jconn, page=page, per_page=per_page,
        underlying=underlying, status=status
    )
    # Hydrate each trade row in the paginated result.
    # get_trade_history returns a dict with a "trades" list key.
    if isinstance(result, dict) and "trades" in result:
        result["trades"] = [_hydrate_trade(t) for t in result["trades"]]
    elif isinstance(result, list):
        result = [_hydrate_trade(t) for t in result]
    return result


@router.get("/learning/report")
def learning_report(
    days: int = Query(30, ge=7, le=365),
    jconn: sqlite3.Connection = Depends(get_journal),
):
    return build_learning_report(jconn, days=days)
