"""AI endpoints — recommendation, accept/reject, position, journal, learning."""
import sqlite3
from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel

from optdash.api.deps import get_duck, get_journal
from optdash.ai.journal import trades, shadow
from optdash.ai.learning.report import build_learning_report
from optdash.models import TradeStatus

router = APIRouter()

DEFAULT_UNDERLYING = "NIFTY"


# ── Request schemas ───────────────────────────────────────────────────────────

class AcceptRequest(BaseModel):
    trade_id:            int
    snap_time:           str
    actual_entry_price:  float | None = None


class RejectRequest(BaseModel):
    trade_id: int
    reason:   str
    note:     str | None = None


class CloseRequest(BaseModel):
    trade_id:    int
    exit_price:  float
    snap_time:   str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/recommendation/latest")
def latest_recommendation(
    underlying: str = Query(DEFAULT_UNDERLYING),
    jconn: sqlite3.Connection = Depends(get_journal),
):
    pending = trades.get_pending_trades(jconn, underlying=underlying)
    if not pending:
        return {"status": "NO_RECOMMENDATION"}
    return pending[-1]   # most recent


@router.get("/position/live")
def live_position(
    underlying: str = Query(DEFAULT_UNDERLYING),
    jconn: sqlite3.Connection = Depends(get_journal),
):
    open_trades = trades.get_open_trades(jconn, underlying=underlying)
    if not open_trades:
        return {"status": "NO_POSITION"}
    return open_trades[0]


@router.post("/accept")
def accept(
    req:   AcceptRequest,
    jconn: sqlite3.Connection = Depends(get_journal),
):
    trade = trades.get_trade(jconn, req.trade_id)
    if not trade:
        raise HTTPException(404, "Trade not found")
    if trade["status"] != TradeStatus.GENERATED.value:
        raise HTTPException(400, f"Cannot accept trade in status {trade['status']}")

    # Create shadow for comparison
    shadow.create_shadow(jconn, {
        "trade_id":      req.trade_id,
        "trade_date":    trade["trade_date"],
        "underlying":    trade["underlying"],
        "option_type":   trade["option_type"],
        "strike_price":  trade["strike_price"],
        "expiry_date":   trade["expiry_date"],
        "entry_premium": trade["entry_premium"],
    })

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
    if trade["status"] not in (TradeStatus.GENERATED.value, TradeStatus.ACCEPTED.value):
        raise HTTPException(400, f"Cannot reject trade in status {trade['status']}")

    # Shadow: track what would have happened
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
def close_trade(
    req:   CloseRequest,
    jconn: sqlite3.Connection = Depends(get_journal),
):
    trade = trades.get_trade(jconn, req.trade_id)
    if not trade:
        raise HTTPException(404, "Trade not found")
    if trade["status"] != TradeStatus.ACCEPTED.value:
        raise HTTPException(400, f"Trade not open (status={trade['status']})")

    entry    = trade["actual_entry_price"] or trade["entry_premium"]
    pnl_abs  = round(req.exit_price - entry, 2)
    pnl_pct  = round(pnl_abs / entry * 100, 2)

    trades.close_trade(jconn, req.trade_id, {
        "exit_premium":   req.exit_price,
        "exit_snap_time": req.snap_time,
        "exit_reason":    "MANUAL_CLOSE",
        "final_pnl_abs":  pnl_abs,
        "final_pnl_pct":  pnl_pct,
    })
    return {"status": "closed", "trade_id": req.trade_id, "pnl_pct": pnl_pct}


@router.get("/journal/history")
def trade_history(
    page:       int         = Query(1, ge=1),
    per_page:   int         = Query(20, ge=5, le=100),
    underlying: str | None  = Query(None),
    status:     str | None  = Query(None),
    jconn: sqlite3.Connection = Depends(get_journal),
):
    return trades.get_trade_history(
        jconn, page=page, per_page=per_page,
        underlying=underlying, status=status
    )


@router.get("/learning/report")
def learning_report(
    days: int = Query(30, ge=7, le=365),
    jconn: sqlite3.Connection = Depends(get_journal),
):
    return build_learning_report(jconn, days=days)
