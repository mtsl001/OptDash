"""Shadow trades DAO."""
import sqlite3

# ---------------------------------------------------------------------------
# Allowed column sets -- validated before any f-string SQL construction (F12)
# ---------------------------------------------------------------------------
_ALLOWED_SHADOW_COLS: frozenset[str] = frozenset({
    "trade_id", "trade_date", "underlying", "option_type", "strike_price",
    "expiry_date", "entry_premium", "sl_price", "target_price",
    "entry_snap_time", "is_closed", "final_pnl_pct", "outcome", "closed_snap",
})

_ALLOWED_SHADOW_SNAP_COLS: frozenset[str] = frozenset({
    "shadow_id", "snap_time", "ltp", "pnl_pct", "hit_sl", "hit_tgt",
})


def create_shadow(conn: sqlite3.Connection, data: dict, commit: bool = True) -> int:
    """Insert a new shadow trade record.

    Raises ValueError if *data* contains any key not in _ALLOWED_SHADOW_COLS
    (prevents f-string SQL injection via unvalidated dict keys).

    commit=True  (default): commit immediately -- safe for standalone calls.
    commit=False: leave the INSERT in the current implicit transaction so the
    caller can bundle it with other writes (e.g. reject_trade) and commit once.
    """
    unknown = set(data.keys()) - _ALLOWED_SHADOW_COLS
    if unknown:
        raise ValueError(f"create_shadow: unknown column(s): {unknown}")
    cols         = ", ".join(data.keys())
    placeholders = ", ".join(["?"] * len(data))
    cur = conn.execute(
        f"INSERT INTO shadow_trades ({cols}) VALUES ({placeholders})",
        list(data.values())
    )
    if commit:
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


def insert_shadow_snap(
    conn:   sqlite3.Connection,
    data:   dict,
    commit: bool = True,
) -> None:
    """Insert a shadow position snap.

    Raises ValueError if *data* contains any key not in _ALLOWED_SHADOW_SNAP_COLS.

    commit=True  (default): commit immediately -- safe for standalone calls.
    commit=False: leave the INSERT uncommitted so the caller can bundle it
    with close_shadow() into one atomic transaction (F16 fix). Since
    close_shadow() always commits, using commit=False here + calling
    close_shadow() next makes both writes atomic: a crash between them
    leaves neither committed rather than leaving an orphan snap row.
    """
    unknown = set(data.keys()) - _ALLOWED_SHADOW_SNAP_COLS
    if unknown:
        raise ValueError(f"insert_shadow_snap: unknown column(s): {unknown}")
    cols         = ", ".join(data.keys())
    placeholders = ", ".join(["?"] * len(data))
    conn.execute(
        f"INSERT INTO shadow_snaps ({cols}) VALUES ({placeholders})",
        list(data.values())
    )
    if commit:
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
    """Return closed shadow trades joined to their parent trade metadata.

    Uses LEFT JOIN (not INNER JOIN) so orphaned shadows -- where the parent
    trade row was manually deleted or CASCADE failed before FK enforcement
    was enabled -- are still included rather than silently excluded.
    Consumers use .get() with fallbacks for all joined fields so None
    values from the left join are handled gracefully.
    """
    q = """SELECT st.*, t.narrative, t.gate_score, t.confidence
           FROM shadow_trades st
           LEFT JOIN trades t ON t.id = st.trade_id
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
