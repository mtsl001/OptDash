"""Position snaps DAO."""
import sqlite3

# ---------------------------------------------------------------------------
# Allowed column set -- validated before f-string SQL construction (F12)
# ---------------------------------------------------------------------------
_ALLOWED_SNAP_COLS: frozenset[str] = frozenset({
    "trade_id", "snap_time", "ltp", "pnl_abs", "pnl_pct",
    "sl_adjusted", "trail_sl", "gate_status", "iv_current",
    "delta_current", "theta_current", "spot_current",
})


def insert_snap(
    conn:   sqlite3.Connection,
    data:   dict,
    commit: bool = True,
) -> None:
    """Insert a position snap row.

    Raises ValueError if *data* contains any key not in _ALLOWED_SNAP_COLS
    (prevents f-string SQL injection via unvalidated dict keys).

    commit=True  (default): commit immediately -- safe for standalone calls.
    commit=False: leave the INSERT uncommitted so the scheduler tick loop
    can batch all N snaps in one transaction and commit once at the end,
    reducing WAL syncs from N to 1 per tick (F13 fix). If the loop
    crashes mid-way, no partial tick data is committed.
    """
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
    """Return the peak LTP recorded across all snaps for a trade.

    Returns None (not 0.0) when no snaps exist yet, so callers can
    distinguish 'no snaps yet' from 'peak LTP was genuinely 0.0' (a
    deeply OTM worthless option). The tracker uses this to seed
    peak_ltp with the current tick's ltp on the very first snap rather
    than anchoring the trailing stop at 0.0 * 0.90 = 0.0 (P6-F6 fix).
    """
    row = conn.execute(
        "SELECT MAX(ltp) FROM position_snaps WHERE trade_id=?", [trade_id]
    ).fetchone()
    if row is None or row[0] is None:
        return None   # no snaps yet -- caller handles the first-tick case
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
