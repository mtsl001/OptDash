"""Market data endpoints — spot, GEX, CoC, environment gate."""
from fastapi import APIRouter, Depends, Query
from optdash.api.deps import get_duck
from optdash.api.validators import TradeDate, SnapTime
from optdash.analytics.gex import get_net_gex, get_gex_series, get_spot_summary
from optdash.analytics.coc import get_coc_latest, get_coc_series
from optdash.analytics.environment import get_environment_score
from optdash.config import settings

router = APIRouter()
# DEFAULT_UNDERLYING constant removed -- use settings.DEFAULT_UNDERLYING so
# any .env override is respected without redeploying router code.


@router.get("/spot")
def spot(
    trade_date: TradeDate = Query(...),
    underlying: str       = Query(settings.DEFAULT_UNDERLYING),
    duck = Depends(get_duck),
):
    """
    Returns current spot with full-day OHLC and change_pct.
    Uses get_spot_summary() (arg_max/arg_min) to ensure spot = latest snap,
    day_open = first snap, day_high/low = true intraday range.
    """
    result = get_spot_summary(duck, trade_date, underlying)
    if not result:
        return {"error": "no data"}
    return result


@router.get("/gex")
def gex(
    trade_date: TradeDate = Query(...),
    underlying: str       = Query(settings.DEFAULT_UNDERLYING),
    duck = Depends(get_duck),
):
    return get_gex_series(duck, trade_date, underlying)


@router.get("/gex/current")
def gex_current(
    trade_date: TradeDate = Query(...),
    snap_time:  SnapTime  = Query(...),
    underlying: str       = Query(settings.DEFAULT_UNDERLYING),
    duck = Depends(get_duck),
):
    return get_net_gex(duck, trade_date, snap_time, underlying)


@router.get("/coc")
def coc(
    trade_date: TradeDate = Query(...),
    underlying: str       = Query(settings.DEFAULT_UNDERLYING),
    duck = Depends(get_duck),
):
    return get_coc_series(duck, trade_date, underlying)


@router.get("/coc/current")
def coc_current(
    trade_date: TradeDate = Query(...),
    snap_time:  SnapTime  = Query(...),
    underlying: str       = Query(settings.DEFAULT_UNDERLYING),
    duck = Depends(get_duck),
):
    return get_coc_latest(duck, trade_date, snap_time, underlying)


@router.get("/environment")
def environment(
    trade_date: TradeDate  = Query(...),
    snap_time:  SnapTime   = Query(...),
    underlying: str        = Query(settings.DEFAULT_UNDERLYING),
    direction:  str | None = Query(None),
    duck = Depends(get_duck),
):
    return get_environment_score(duck, trade_date, snap_time, underlying, direction)
