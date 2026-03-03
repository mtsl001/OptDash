"""Shadow trades DAO."""
import sqlite3


def create_shadow(conn: sqlite3.Connection, data: dict) -> int:
    cols         = ", ".join(data.keys())
    placeholders = ", ".join(["?"] * len(data))
    cur = conn.execute(
        f"INSERT INTO shadow_trades ({cols}) VALUES ({placeholders})",
        list(data.values())
    )
    conn.commit()
    return cur.lastrowid


def get_active_shadows(conn: sqlite3.Connection, trade_date: str) -> list[dict]:
    cur = conn.execute(
        """SELECT * FROM shadow_trades
           WHERE trade_date=? AND is_closed=0
           ORDER BY id""",
        [trade_date]
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def insert_shadow_snap(conn: sqlite3.Connection, data: dict) -> None:
    cols         = ", ".join(data.keys())
    placeholders = ", ".join(["?"] * len(data))
    conn.execute(
        f"INSERT INTO shadow_snaps ({cols}) VALUES ({placeholders})",
        list(data.values())
    )
    conn.commit()


def close_shadow(conn: sqlite3.Connection, shadow_id: int, data: dict) -> None:
    conn.execute(
        """UPDATE shadow_trades
           SET is_closed=1, final_pnl_pct=?, outcome=?, closed_snap=?
           WHERE id=?""",
        [data["final_pnl_pct"], data["outcome"], data["closed_snap"], shadow_id]
    )
    conn.commit()


def get_shadow_history(
    conn: sqlite3.Connection,
    days: int = 30,
    underlying: str | None = None,
) -> list[dict]:
    q = """SELECT st.*, t.narrative, t.gate_score, t.confidence
           FROM shadow_trades st
           JOIN trades t ON t.id = st.trade_id
           WHERE st.trade_date >= date('now', ?)
           AND st.is_closed=1"""
    params: list = [f"-{days} days"]
    if underlying:
        q += " AND st.underlying=?"
        params.append(underlying)
    q += " ORDER BY st.trade_date DESC"
    cur  = conn.execute(q, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]
