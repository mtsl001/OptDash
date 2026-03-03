"""Position snaps DAO."""
import sqlite3


def insert_snap(conn: sqlite3.Connection, data: dict) -> None:
    cols         = ", ".join(data.keys())
    placeholders = ", ".join(["?"] * len(data))
    conn.execute(
        f"INSERT INTO position_snaps ({cols}) VALUES ({placeholders})",
        list(data.values())
    )
    conn.commit()


def get_snaps_for_trade(conn: sqlite3.Connection, trade_id: int) -> list[dict]:
    cur  = conn.execute(
        "SELECT * FROM position_snaps WHERE trade_id=? ORDER BY snap_time ASC",
        [trade_id]
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def get_peak_ltp(conn: sqlite3.Connection, trade_id: int) -> float:
    row = conn.execute(
        "SELECT MAX(ltp) FROM position_snaps WHERE trade_id=?", [trade_id]
    ).fetchone()
    return float(row[0] or 0)


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
