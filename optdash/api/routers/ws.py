"""WebSocket — streams live snap updates to the frontend every 5 seconds."""
import asyncio
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from loguru import logger

from optdash.api.deps import get_duck, get_journal
from optdash.analytics.gex import get_net_gex
from optdash.analytics.coc import get_coc_latest
from optdash.analytics.environment import get_environment_score
from optdash.analytics.pcr import get_pcr
from optdash.analytics.vex_cex import get_vex_cex_current
from optdash.analytics.alerts import get_alerts
from optdash.ai.journal import trades
from optdash.config import settings

router = APIRouter()


@router.websocket("/live")
async def live_feed(
    ws:         WebSocket,
    trade_date: str = Query(...),
    snap_time:  str = Query("LIVE"),
    underlying: str = Query("NIFTY"),
):
    await ws.accept()
    duck    = ws.app.state.duck
    journal = ws.app.state.journal

    logger.info("WS connect: {} {} {}", underlying, trade_date, snap_time)
    try:
        while True:
            # Determine effective snap_time
            if snap_time == "LIVE":
                eff_snap = _latest_snap(duck, trade_date, underlying)
            else:
                eff_snap = snap_time

            if not eff_snap:
                await ws.send_json({"error": "no data"})
                await asyncio.sleep(settings.WS_INTERVAL_SECONDS)
                continue

            payload = _build_payload(duck, journal, trade_date, eff_snap, underlying)
            await ws.send_text(json.dumps(payload, default=str))
            await asyncio.sleep(settings.WS_INTERVAL_SECONDS)
    except WebSocketDisconnect:
        logger.info("WS disconnect: {} {}", underlying, snap_time)
    except Exception as e:
        logger.warning("WS error: {}", e)
        await ws.close()


def _latest_snap(
    duck, trade_date: str, underlying: str
) -> str | None:
    try:
        row = duck.execute(
            """SELECT MAX(snap_time) FROM options_data
               WHERE trade_date=? AND underlying=?""",
            [trade_date, underlying]
        ).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def _build_payload(duck, journal, trade_date, snap_time, underlying) -> dict:
    try:
        env     = get_environment_score(duck, trade_date, snap_time, underlying)
        gex     = get_net_gex(duck, trade_date, snap_time, underlying)
        coc     = get_coc_latest(duck, trade_date, snap_time, underlying)
        pcr     = get_pcr(duck, trade_date, snap_time, underlying)
        vex     = get_vex_cex_current(duck, trade_date, snap_time, underlying)
        alerts  = get_alerts(duck, trade_date, snap_time, underlying)
        pending = trades.get_pending_trades(journal, underlying=underlying)
        open_t  = trades.get_open_trades(journal, underlying=underlying)

        return {
            "snap_time":    snap_time,
            "underlying":   underlying,
            "environment":  env,
            "gex":          gex,
            "coc":          coc,
            "pcr":          pcr,
            "vex":          vex,
            "alerts":       alerts[:5],
            "pending_trade":pending[-1] if pending else None,
            "open_trade":   open_t[0]   if open_t  else None,
        }
    except Exception as e:
        logger.warning("WS _build_payload error: {}", e)
        return {"error": str(e), "snap_time": snap_time}
