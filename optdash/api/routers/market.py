"""Market data endpoints — spot, GEX, CoC, environment gate."""
from fastapi import APIRouter, Depends, Query
from optdash.api.deps import get_duck
from optdash.analytics.gex import get_net_gex, get_gex_series
from optdash.analytics.coc import get_coc_latest, get_coc_series
from optdash.analytics.environment import get_environment_score

router = APIRouter()

DEFAULT_UNDERLYING = "NIFTY"


@router.get("/spot")
def spot(
    trade_date: str  = Query(...),
    underlying: str  = Query(DEFAULT_UNDERLYING),
    duck = Depends(get_duck),
):
    row = duck.execute(
        """
        SELECT snap_time, spot, day_open, day_high, day_low,
               (spot - day_open) / NULLIF(day_open, 0) * 100 AS change_pct
        FROM options_data
        WHERE trade_date=? AND underlying=?
        ORDER BY snap_time DESC LIMIT 1
        """,
        [trade_date, underlying]
    ).fetchone()
    if not row:
        return {"error": "no data"}
    return dict(zip(
        ["snap_time","spot","day_open","day_high","day_low","change_pct"], row
    ))


@router.get("/gex")
def gex(
    trade_date: str = Query(...),
    underlying: str = Query(DEFAULT_UNDERLYING),
    duck = Depends(get_duck),
):
    return get_gex_series(duck, trade_date, underlying)


@router.get("/gex/current")
def gex_current(
    trade_date: str = Query(...),
    snap_time:  str = Query(...),
    underlying: str = Query(DEFAULT_UNDERLYING),
    duck = Depends(get_duck),
):
    return get_net_gex(duck, trade_date, snap_time, underlying)


@router.get("/coc")
def coc(
    trade_date: str = Query(...),
    underlying: str = Query(DEFAULT_UNDERLYING),
    duck = Depends(get_duck),
):
    return get_coc_series(duck, trade_date, underlying)


@router.get("/coc/current")
def coc_current(
    trade_date: str = Query(...),
    snap_time:  str = Query(...),
    underlying: str = Query(DEFAULT_UNDERLYING),
    duck = Depends(get_duck),
):
    return get_coc_latest(duck, trade_date, snap_time, underlying)


@router.get("/environment")
def environment(
    trade_date: str         = Query(...),
    snap_time:  str         = Query(...),
    underlying: str         = Query(DEFAULT_UNDERLYING),
    direction:  str | None  = Query(None),
    duck = Depends(get_duck),
):
    return get_environment_score(duck, trade_date, snap_time, underlying, direction)
