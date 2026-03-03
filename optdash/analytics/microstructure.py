"""Microstructure analytics — volume velocity."""
import duckdb
from loguru import logger


def get_volume_velocity(conn: duckdb.DuckDBPyConnection, trade_date: str,
                        underlying: str) -> list[dict]:
    """Full-day volume ratio vs rolling 10-snap median baseline."""
    try:
        rows = conn.execute("""
            SELECT snap_time, SUM(volume) AS vol_total
            FROM options_data
            WHERE trade_date=? AND underlying=?
            GROUP BY snap_time ORDER BY snap_time
        """, [trade_date, underlying]).fetchall()
        if not rows:
            return []
        result = []
        vols = [r[1] or 0 for r in rows]
        for i, r in enumerate(rows):
            window = vols[max(0, i - 10):i] if i > 0 else [vols[0]]
            baseline = sorted(window)[len(window) // 2] if window else vols[i]
            ratio = (vols[i] / baseline) if baseline else 1.0
            result.append({
                "snap_time":    r[0],
                "vol_total":    int(vols[i]),
                "baseline_vol": int(baseline),
                "volume_ratio": round(ratio, 2),
                "signal":       "SPIKE" if ratio >= 2.0 else "NORMAL",
            })
        return result
    except Exception as e:
        logger.warning("get_volume_velocity error: {}", e)
        return []
