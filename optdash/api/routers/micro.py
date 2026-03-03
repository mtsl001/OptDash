"""Microstructure endpoints — PCR, alerts, volume velocity, VEX/CEX."""
from fastapi import APIRouter, Depends, Query
from optdash.api.deps import get_duck
from optdash.analytics.pcr import get_pcr_series
from optdash.analytics.alerts import get_alerts
from optdash.analytics.microstructure import get_volume_velocity
from optdash.analytics.vex_cex import get_vex_cex_series

router = APIRouter()

DEFAULT_UNDERLYING = "NIFTY"


@router.get("/pcr")
def pcr(
    trade_date: str = Query(...),
    underlying: str = Query(DEFAULT_UNDERLYING),
    duck = Depends(get_duck),
):
    return get_pcr_series(duck, trade_date, underlying)


@router.get("/alerts")
def alerts(
    trade_date: str = Query(...),
    snap_time:  str = Query(...),
    underlying: str = Query(DEFAULT_UNDERLYING),
    duck = Depends(get_duck),
):
    return get_alerts(duck, trade_date, snap_time, underlying)


@router.get("/volume-velocity")
def volume_velocity(
    trade_date: str = Query(...),
    underlying: str = Query(DEFAULT_UNDERLYING),
    duck = Depends(get_duck),
):
    return get_volume_velocity(duck, trade_date, underlying)


@router.get("/vex-cex")
def vex_cex(
    trade_date: str = Query(...),
    snap_time:  str = Query(...),
    underlying: str = Query(DEFAULT_UNDERLYING),
    duck = Depends(get_duck),
):
    return get_vex_cex_series(duck, trade_date, snap_time, underlying)
