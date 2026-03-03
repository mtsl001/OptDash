"""Trades DAO — CRUD for the trades table."""
import sqlite3
from typing import Any


def create_trade(conn: sqlite3.Connection, data: dict) -> int:
    cols = ", ".join(data.keys())
    placeholders = ", ".join(["?"] * len(data))
    cur = conn.execute(
        f"INSERT INTO trades ({cols}) VALUES ({placeholders})",
        list(data.values())
    )
    conn.commit()
    return cur.lastrowid


def get_trade(conn: sqlite3.Connection, trade_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM trades WHERE id=?", [trade_id]
    ).fetchone()
    return _row_to_dict(conn, row) if row else None


def get_open_trades(conn: sqlite3.Connection, underlying: str | None = None) -> list[dict]:
    q = "SELECT * FROM trades WHERE status='ACCEPTED'"
    params = []
    if underlying:
        q += " AND underlying=?"
        params.append(underlying)
    return _fetchall(conn, q, params)


def get_pending_trades(conn: sqlite3.Connection, underlying: str | None = None) -> list[dict]:
    q = "SELECT * FROM trades WHERE status='GENERATED'"
    params = []
    if underlying:
        q += " AND underlying=?"
        params.append(underlying)
    return _fetchall(conn, q, params)


def get_latest_trade(conn: sqlite3.Connection, underlying: str) -> dict | None:
    row = conn.execute(
        """SELECT * FROM trades WHERE underlying=?
           ORDER BY created_at DESC LIMIT 1""",
        [underlying]
    ).fetchone()
    return _row_to_dict(conn, row) if row else None


def get_trade_history(
    conn: sqlite3.Connection,
    page: int = 1,
    per_page: int = 20,
    underlying: str | None = None,
    status: str | None = None,
) -> dict:
    offset = (page - 1) * per_page
    where_clauses, params = [], []
    if underlying:
        where_clauses.append("underlying=?")
        params.append(underlying)
    if status:
        where_clauses.append("status=?")
        params.append(status)
    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    total = conn.execute(
        f"SELECT COUNT(*) FROM trades {where}", params
    ).fetchone()[0]

    rows = conn.execute(
        f"SELECT * FROM trades {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [per_page, offset]
    ).fetchall()

    return {
        "trades":    [_row_to_dict(conn, r) for r in rows],
        "total":     total,
        "page":      page,
        "per_page":  per_page,
        "pages":     (total + per_page - 1) // per_page,
    }


def update_status(
    conn: sqlite3.Connection,
    trade_id: int,
    status: str,
    state_reason: str | None = None,
) -> None:
    conn.execute(
        """UPDATE trades SET status=?, updated_at=datetime('now')
           WHERE id=?""",
        [status, trade_id]
    )
    conn.commit()


def accept_trade(
    conn: sqlite3.Connection,
    trade_id: int,
    snap_time: str,
    actual_entry_price: float | None = None,
) -> None:
    conn.execute(
        """UPDATE trades
           SET status='ACCEPTED',
               actual_entry_price=COALESCE(?, entry_premium),
               snap_time=?,
               updated_at=datetime('now')
           WHERE id=?""",
        [actual_entry_price, snap_time, trade_id]
    )
    conn.commit()


def reject_trade(
    conn: sqlite3.Connection,
    trade_id: int,
    reason: str,
    note: str | None = None,
) -> None:
    conn.execute(
        """UPDATE trades
           SET status='REJECTED', rejection_reason=?, rejection_note=?,
               updated_at=datetime('now')
           WHERE id=?""",
        [reason, note, trade_id]
    )
    conn.commit()


def close_trade(
    conn: sqlite3.Connection,
    trade_id: int,
    data: dict,
) -> None:
    conn.execute(
        """UPDATE trades
           SET status='CLOSED',
               exit_premium=?,
               exit_snap_time=?,
               exit_reason=?,
               final_pnl_abs=?,
               final_pnl_pct=?,
               updated_at=datetime('now')
           WHERE id=?""",
        [
            data["exit_premium"],
            data["exit_snap_time"],
            data["exit_reason"],
            data["final_pnl_abs"],
            data["final_pnl_pct"],
            trade_id,
        ]
    )
    conn.commit()


def _row_to_dict(conn: sqlite3.Connection, row) -> dict:
    if hasattr(conn, 'row_factory') and conn.row_factory:
        return dict(row)
    desc = conn.execute("SELECT * FROM trades LIMIT 0").description
    cols = [d[0] for d in desc]
    return dict(zip(cols, row))


def _fetchall(conn: sqlite3.Connection, q: str, params: list) -> list[dict]:
    rows = conn.execute(q, params).fetchall()
    if not rows:
        return []
    desc  = conn.execute(q.replace("SELECT *", "SELECT * FROM trades LIMIT 0") if False else q + " LIMIT 0", params).description
    # Simpler: use cursor description from the actual query
    cur   = conn.execute(q, params)
    cols  = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]
