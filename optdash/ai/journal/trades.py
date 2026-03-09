"""Trades DAO — CRUD for the trades table."""
import sqlite3


def create_trade(conn: sqlite3.Connection, data: dict) -> int:
    cols         = ", ".join(data.keys())
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
    return dict(row) if row else None


def get_open_trades(
    conn: sqlite3.Connection, underlying: str | None = None
) -> list[dict]:
    q, params = "SELECT * FROM trades WHERE status='ACCEPTED'", []
    if underlying:
        q += " AND underlying=?"
        params.append(underlying)
    q += " ORDER BY created_at DESC"
    return _fetchall(conn, q, params)


def get_pending_trades(
    conn: sqlite3.Connection, underlying: str | None = None
) -> list[dict]:
    q, params = "SELECT * FROM trades WHERE status='GENERATED'", []
    if underlying:
        q += " AND underlying=?"
        params.append(underlying)
    q += " ORDER BY created_at DESC"
    return _fetchall(conn, q, params)


def get_latest_trade(
    conn: sqlite3.Connection, underlying: str
) -> dict | None:
    row = conn.execute(
        """SELECT * FROM trades WHERE underlying=?
           ORDER BY created_at DESC LIMIT 1""",
        [underlying]
    ).fetchone()
    return dict(row) if row else None


def get_trade_history(
    conn:       sqlite3.Connection,
    page:       int = 1,
    per_page:   int = 20,
    underlying: str | None = None,
    status:     str | None = None,
) -> dict:
    offset = (page - 1) * per_page
    where_clauses: list[str] = []
    params:        list      = []

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

    trade_rows = _fetchall(
        conn,
        f"SELECT * FROM trades {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [per_page, offset],
    )

    return {
        "trades":   trade_rows,
        "total":    total,
        "page":     page,
        "per_page": per_page,
        "pages":    max(1, (total + per_page - 1) // per_page),
    }


def update_status(
    conn:         sqlite3.Connection,
    trade_id:     int,
    status:       str,
    state_reason: str | None = None,
) -> None:
    """Update trade status and optionally persist a state_reason.

    COALESCE(?, rejection_reason) writes the new reason when provided,
    and preserves the existing value when state_reason is None --
    so a bare status update never clears a previously recorded reason.

    Fix-H: previously state_reason was accepted but silently dropped.
    """
    conn.execute(
        """UPDATE trades
           SET status=?,
               rejection_reason=COALESCE(?, rejection_reason),
               updated_at=datetime('now')
           WHERE id=?""",
        [status, state_reason, trade_id],
    )
    conn.commit()


def accept_trade(
    conn:               sqlite3.Connection,
    trade_id:           int,
    accept_snap_time:   str,
    actual_entry_price: float | None = None,
) -> None:
    """Accept a generated trade recommendation and record the acceptance snap.

    Fix-P1-14: snap_time (the AI generation snap) is intentionally NOT
    updated here. It is set at INSERT time and must remain immutable so that:
      - build_theta_sl_series() anchors the theta-SL curve at generation time.
      - The generation-to-acceptance delta (snap_time vs accept_snap_time)
        is preserved as a learning signal for the AI feedback loop.
      - Session attribution based on snap_time is always the generation
        session, not the (potentially different) acceptance session.

    accept_snap_time is written to the dedicated column added in schema.py.
    """
    conn.execute(
        """UPDATE trades
           SET status='ACCEPTED',
               actual_entry_price=COALESCE(?, entry_premium),
               accept_snap_time=?,
               updated_at=datetime('now')
           WHERE id=?""",
        [actual_entry_price, accept_snap_time, trade_id]
    )
    conn.commit()


def reject_trade(
    conn:     sqlite3.Connection,
    trade_id: int,
    reason:   str,
    note:     str | None = None,
    commit:   bool = True,
) -> None:
    """Update trade status to REJECTED.

    commit=True  (default): commit immediately -- safe for standalone calls.
    commit=False: leave the UPDATE in the current implicit transaction so
    the caller can bundle it with shadow.create_shadow() and commit once,
    making both writes atomic (used by the /reject API endpoint).
    """
    conn.execute(
        """UPDATE trades
           SET status='REJECTED', rejection_reason=?, rejection_note=?,
               updated_at=datetime('now')
           WHERE id=?""",
        [reason, note, trade_id]
    )
    if commit:
        conn.commit()


def close_trade(
    conn:     sqlite3.Connection,
    trade_id: int,
    data:     dict,
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


# -- Helpers ------------------------------------------------------------------

def _fetchall(conn: sqlite3.Connection, q: str, params: list) -> list[dict]:
    """Execute query and return list of dicts.
    Requires row_factory=sqlite3.Row (set in deps.py and scheduler.py).
    Single-pass: one execute, one fetchall.
    """
    cur  = conn.execute(q, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]
