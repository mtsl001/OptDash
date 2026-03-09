"""Learning stats — aggregate win-rate by session, direction, underlying."""
import sqlite3
from optdash.models import MarketSession

# Whitelist for get_threshold_performance to prevent SQL injection
_ALLOWED_THRESHOLD_FIELDS = {"confidence", "gate_score", "s_score"}


def get_session_stats(
    conn:       sqlite3.Connection,
    underlying: str | None           = None,
    direction:  str | None           = None,
    session:    MarketSession | None = None,
    min_trades: int = 10,
) -> dict:
    """
    Returns win_rate, avg_pnl, total_trades for the given filter bucket.
    Falls back to overall stats if the bucket has fewer than min_trades closed trades.

    P4-F15: return dict now includes `is_fallback: bool` so callers (e.g.
    confidence.py) can detect when global stats were substituted and apply
    cold-start discounting rather than granting B4 credits based on thin
    or non-existent bucket history.
    """
    base_where = ["status='CLOSED'", "final_pnl_pct IS NOT NULL"]
    params: list = []

    if underlying:
        base_where.append("underlying=?")
        params.append(underlying)
    if direction:
        base_where.append("option_type=?")
        params.append(direction)
    if session:
        base_where.append("session=?")
        params.append(session.value)

    where = " AND ".join(base_where)
    row = conn.execute(
        f"""SELECT
                COUNT(*)                                              AS total,
                SUM(CASE WHEN final_pnl_pct > 0 THEN 1 ELSE 0 END)  AS wins,
                AVG(final_pnl_pct)                                    AS avg_pnl,
                AVG(confidence)                                       AS avg_conf,
                AVG(gate_score)                                       AS avg_gate
            FROM trades WHERE {where}""",
        params
    ).fetchone()

    total = row[0] or 0
    # P4-F15: capture fallback state before potentially overwriting `total`.
    # was_fallback=True means the specific bucket (underlying+direction+session)
    # had fewer than min_trades closed trades, so global stats were used.
    was_fallback = total < min_trades
    if was_fallback:
        # Fallback: global stats — not enough history in this specific bucket
        row = conn.execute(
            """SELECT COUNT(*),
                      SUM(CASE WHEN final_pnl_pct > 0 THEN 1 ELSE 0 END),
                      AVG(final_pnl_pct), AVG(confidence), AVG(gate_score)
               FROM trades
               WHERE status='CLOSED' AND final_pnl_pct IS NOT NULL"""
        ).fetchone()
        total = row[0] or 0

    wins     = row[1] or 0
    avg_pnl  = round(float(row[2] or 0), 2)
    avg_conf = round(float(row[3] or 0), 1)
    avg_gate = round(float(row[4] or 0), 1)
    win_rate = round((wins / total * 100) if total else 50.0, 1)

    return {
        "win_rate":       win_rate,
        "avg_pnl":        avg_pnl,
        "total_trades":   total,
        "avg_confidence": avg_conf,
        "avg_gate":       avg_gate,
        "is_fallback":    was_fallback,  # P4-F15: True → global stats substituted
    }


def get_threshold_performance(
    conn:            sqlite3.Connection,
    threshold_field: str,
    buckets:         list[tuple[float, float]] | None = None,
) -> list[dict]:
    """Win rate by threshold bucket — for the learning report.

    threshold_field must be one of: 'confidence', 'gate_score', 's_score'.
    Raises ValueError on any other value to prevent SQL injection.
    """
    if threshold_field not in _ALLOWED_THRESHOLD_FIELDS:
        raise ValueError(
            f"Invalid threshold_field {threshold_field!r}. "
            f"Allowed: {sorted(_ALLOWED_THRESHOLD_FIELDS)}"
        )

    if buckets is None:
        buckets = [(0, 50), (50, 65), (65, 75), (75, 85), (85, 101)]

    results = []
    for lo, hi in buckets:
        row = conn.execute(
            f"""SELECT
                    COUNT(*),
                    SUM(CASE WHEN final_pnl_pct > 0 THEN 1 ELSE 0 END),
                    AVG(final_pnl_pct)
                FROM trades
                WHERE status='CLOSED' AND final_pnl_pct IS NOT NULL
                  AND {threshold_field} >= ? AND {threshold_field} < ?""",
            [lo, hi]
        ).fetchone()
        total = row[0] or 0
        wins  = row[1] or 0
        results.append({
            "bucket":   f"{lo}-{hi}",
            "total":    total,
            "wins":     wins,
            "win_rate": round(wins / total * 100, 1) if total else None,
            "avg_pnl":  round(float(row[2] or 0), 2),
        })
    return results
