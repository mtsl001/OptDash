"""Alert generation — transition-based signals from last 60 min of data."""
import duckdb
from loguru import logger
from optdash.config import settings
from optdash.models import AlertType, AlertSeverity
from optdash.analytics.gex import get_gex_series
from optdash.analytics.coc import get_coc_series
from optdash.analytics.pcr import get_pcr_series
from optdash.analytics.microstructure import get_volume_velocity


def get_alerts(
    conn: duckdb.DuckDBPyConnection,
    trade_date: str,
    snap_time:  str,
    underlying: str,
    lookback_snaps: int = 12,
) -> list[dict]:
    """Return list of alerts from the last 60 min (ordered newest first)."""
    alerts = []

    try:
        gex_series = get_gex_series(conn, trade_date, underlying)
        coc_series = get_coc_series(conn, trade_date, underlying)
        pcr_series = get_pcr_series(conn, trade_date, underlying)
        vol_series = get_volume_velocity(conn, trade_date, underlying)

        def recent(series):
            filtered = [s for s in series if s["snap_time"] <= snap_time]
            return filtered[-lookback_snaps:]

        gex_w = recent(gex_series)
        coc_w = recent(coc_series)
        pcr_w = recent(pcr_series)
        vol_w = recent(vol_series)

        # Alert: GEX decline below 70%
        if len(gex_w) >= 2:
            if gex_w[-1]["pct_of_peak"] < 70 and gex_w[-2]["pct_of_peak"] >= 70:
                alerts.append(_make_alert(
                    time=gex_w[-1]["snap_time"],
                    type_=AlertType.GEX_DECLINE,
                    severity=AlertSeverity.HIGH,
                    direction=None,
                    headline="GEX crossed below 70% of peak",
                    message=f"Net GEX declined to {gex_w[-1]['pct_of_peak']:.0f}% — gamma pin weakening, directional move easier.",
                ))

        # Alert: V_CoC velocity spike
        if coc_w:
            latest_coc = coc_w[-1]
            vcoc = latest_coc.get("v_coc_15m", 0)
            if latest_coc["signal"] in ("VELOCITY_BULL", "VELOCITY_BEAR"):
                dir_ = "CE" if vcoc > 0 else "PE"
                alerts.append(_make_alert(
                    time=latest_coc["snap_time"],
                    type_=AlertType.COC_VELOCITY,
                    severity=AlertSeverity.HIGH,
                    direction=dir_,
                    headline=f"V_CoC velocity spike: {vcoc:+.1f}",
                    message=f"Cost-of-carry velocity {vcoc:+.1f} indicates "
                            f"{'institutional long accumulation' if vcoc > 0 else 'institutional unwinding'}.",
                ))

        # Alert: PCR divergence threshold cross
        if len(pcr_w) >= 2:
            cur  = pcr_w[-1]["pcr_divergence"]
            prev = pcr_w[-2]["pcr_divergence"]
            if abs(cur) > 0.20 and abs(prev) <= 0.20:
                dir_   = "CE" if cur > 0 else "PE"
                sev    = AlertSeverity.HIGH if abs(cur) > 0.30 else AlertSeverity.MEDIUM
                label  = "Retail panic puts" if cur > 0 else "Retail panic calls"
                alerts.append(_make_alert(
                    time=pcr_w[-1]["snap_time"],
                    type_=AlertType.PCR_DIVERGENCE,
                    severity=sev,
                    direction=dir_,
                    headline=f"PCR divergence: {label} ({cur:+.3f})",
                    message=f"PCR Vol-OI spread crossed {cur:+.3f} — retail positioning diverging, fade signal.",
                ))

        # Alert: Volume spike
        if vol_w:
            latest_vol = vol_w[-1]
            ratio = latest_vol["volume_ratio"]
            if latest_vol["signal"] == "SPIKE":
                sev = AlertSeverity.HIGH if ratio >= 3.0 else AlertSeverity.MEDIUM
                alerts.append(_make_alert(
                    time=latest_vol["snap_time"],
                    type_=AlertType.VOLUME_SPIKE,
                    severity=sev,
                    direction=None,
                    headline=f"Volume spike: {ratio:.1f}x baseline",
                    message=f"Current volume {ratio:.1f}x above rolling median — unusual activity detected.",
                ))

    except Exception as e:
        logger.warning("get_alerts error: {}", e)

    seen = set()
    unique = []
    for a in sorted(alerts, key=lambda x: x["time"], reverse=True):
        k = (a["type"], a["time"])
        if k not in seen:
            seen.add(k)
            unique.append(a)
    return unique


def _make_alert(time, type_: AlertType, severity: AlertSeverity,
                direction, headline, message) -> dict:
    return {
        "time":      time,
        "type":      type_.value,
        "severity":  severity.value,
        "direction": direction,
        "headline":  headline,
        "message":   message,
    }
