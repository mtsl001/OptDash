"""Screener endpoints — strike screener and IV term structure."""
from fastapi import APIRouter, Depends, Query
from optdash.api.deps import get_duck
from optdash.analytics.screener import get_strikes
from optdash.analytics.iv import get_term_structure

router = APIRouter()

DEFAULT_UNDERLYING = "NIFTY"


@router.get("/strikes")
def strikes(
    trade_date: str = Query(...),
    snap_time:  str = Query(...),
    underlying: str = Query(DEFAULT_UNDERLYING),
    top_n:      int = Query(20, ge=5, le=50),
    # Fix-J: direction filter (CE or PE). When omitted, both types are returned.
    # Pattern validation rejects invalid values with 422 before reaching analytics.
    direction:  str | None = Query(None, pattern="^(CE|PE)$"),
    duck = Depends(get_duck),
):
    return get_strikes(duck, trade_date, snap_time, underlying, top_n, direction)


@router.get("/term-structure")
def term_structure(
    trade_date: str = Query(...),
    snap_time:  str = Query(...),
    underlying: str = Query(DEFAULT_UNDERLYING),
    duck = Depends(get_duck),
):
    return get_term_structure(duck, trade_date, snap_time, underlying)
