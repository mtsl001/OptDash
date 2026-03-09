"""Position snaps DAO."""
import sqlite3

# ---------------------------------------------------------------------------
# Column whitelist — guards insert_snap() against f-string SQL injection via
# caller-controlled dict keys (F12).  Values remain bound as SQL parameters.
# ---------------------------------------------------------------------------
_ALLOWED_SNAP_COLS: frozenset[str] = frozenset({
    "trade_id", "snap_time", "ltp", "pnl_abs", "pnl_pct",
    "sl_adjusted", "theta_sl_status", "iv", "iv_crush",
    "gate_score", "gate_verdict", "delta_pnl", "gamma_pnl",
    "vega_pnl", "theta_pnl", "unexplained",
})


def insert_snap(
    conn:   sqlite3.Connection,
    data:   dict,
    commit: bool = True,
) -> None:
    """Insert a position snap row.

    commit=True  (default): commit immediately -- safe for standalone calls.
    commit=False: leave the INSERT in the current implicit transaction so the
    caller can batch all snaps for a tick and issue a single jconn.commit()
    afterwards -- one WAL flush per tick instead of N (F13).
    """
    # F12: validate column names before interpolating into the SQL f-string.
    unknown = set(data.keys()) - _ALLOWED_SNAP_COLS
    if unknown:
        raise ValueError(f"insert_snap: unknown column(s): {unknown}")

    cols         = ", ".join(data.keys())
    placeholders = ", ".join(["?"] * len(data))
    conn.execute(
        f"INSERT INTO position_snaps ({cols}) VALUES ({placeholders})",
        list(data.values())
    )
    if commit:
        conn.commit()


def get_snaps_for_trade(conn: sqlite3.Connection, trade_id: int) -> list[dict]:
    cur  = conn.execute(
        "SELECT * FROM position_snaps WHERE trade_id=? ORDER BY snap_time ASC",
        [trade_id]
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def get_peak_ltp(conn: sqlite3.Connection, trade_id: int) -> float | None:
    """Return the highest LTP recorded for *trade_id*, or None if no snaps exist.

    Part-6-F6: returns None rather than 0.0 when no snaps have been written
    yet.  0.0 is a semantically valid LTP (deeply-OTM option at expiry) so
    the two states must be distinguishable.  Callers handle the first-tick
    case explicitly:

        raw_peak = snaps.get_peak_ltp(jconn, trade["id"])
        peak_ltp = raw_peak if raw_peak is not None else ltp
    """
    row = conn.execute(
        "SELECT MAX(ltp) FROM position_snaps WHERE trade_id=?", [trade_id]
    ).fetchone()
    if row is None or row[0] is None:
        return None          # no snaps written yet for this trade
    return float(row[0])


def get_latest_snap(conn: sqlite3.Connection, trade_id: int) -> dict | None:
    cur = conn.execute(
        "SELECT * FROM position_snaps WHERE trade_id=? ORDER BY snap_time DESC LIMIT 1",
        [trade_id]
    )
    row  = cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))
