"""SQLite journal schema — CREATE TABLE + INDEX statements.

All tables use CREATE TABLE IF NOT EXISTS so init_db() is safely idempotent
and can be called on every fresh connection without side effects.

For existing databases, _run_migrations() uses ALTER TABLE to add new columns;
SQLite raises OperationalError("duplicate column name") on re-runs, which is
caught and silenced. All other errors propagate so startup fails loudly.
"""
import sqlite3

CREATE_TRADES = """
CREATE TABLE IF NOT EXISTS trades (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date          TEXT    NOT NULL,
    -- snap_time = AI generation snap; immutable after INSERT.
    -- Do NOT overwrite on ACCEPT -- use accept_snap_time for that.
    snap_time           TEXT    NOT NULL,
    underlying          TEXT    NOT NULL,
    -- Fix-P1-12: CHECK constraint rejects any value outside CE/PE.
    -- Prevents Direction.NEUTRAL or a typo from being stored silently.
    option_type         TEXT    NOT NULL CHECK(option_type IN ('CE','PE')),
    strike_price        REAL    NOT NULL,
    expiry_date         TEXT    NOT NULL,
    dte                 INTEGER,
    entry_premium       REAL    NOT NULL,
    actual_entry_price  REAL,               -- set on ACCEPT (slippage-adjusted)
    sl_price            REAL    NOT NULL,
    target_price        REAL    NOT NULL,
    exit_premium        REAL,
    exit_snap_time      TEXT,
    exit_reason         TEXT,               -- ExitReason enum value
    final_pnl_abs       REAL,
    final_pnl_pct       REAL,
    confidence          INTEGER NOT NULL,
    gate_score          INTEGER NOT NULL,
    -- Fix-P1-12: CHECK constraints on gate_verdict and status.
    gate_verdict        TEXT    NOT NULL
                        CHECK(gate_verdict IN ('GO','WAIT','NO_GO')),
    s_score             REAL,
    quality_grade       TEXT,
    direction_signals   TEXT,               -- JSON blob
    narrative           TEXT,
    -- Fix-P1-12: status CHECK ensures only valid TradeStatus values are stored.
    status              TEXT    NOT NULL DEFAULT 'GENERATED'
                        CHECK(status IN ('GENERATED','ACCEPTED','REJECTED','EXPIRED','CLOSED')),
    rejection_reason    TEXT,
    rejection_note      TEXT,
    session             TEXT,               -- MarketSession enum value
    delta               REAL,
    theta               REAL,
    vega                REAL,
    gamma               REAL,
    iv_at_entry         REAL,
    spot_at_entry       REAL,
    conf_buckets        TEXT,               -- JSON blob
    -- Fix-P1-14: acceptance snap stored separately so snap_time (generation
    -- time) is never overwritten. The generation-to-acceptance delta is a
    -- key learning signal preserved by this separation.
    accept_snap_time    TEXT,               -- set on ACCEPT; snap_time stays immutable
    created_at          TEXT    DEFAULT (datetime('now')),
    updated_at          TEXT    DEFAULT (datetime('now'))
);
"""

CREATE_POSITION_SNAPS = """
CREATE TABLE IF NOT EXISTS position_snaps (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id        INTEGER NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
    snap_time       TEXT    NOT NULL,
    ltp             REAL,
    pnl_abs         REAL,
    pnl_pct         REAL,
    sl_adjusted     REAL,
    theta_sl_status TEXT,
    iv              REAL,
    iv_crush        TEXT,
    gate_score      INTEGER,
    gate_verdict    TEXT,
    delta_pnl       REAL,
    gamma_pnl       REAL,
    vega_pnl        REAL,
    theta_pnl       REAL,
    unexplained     REAL,
    created_at      TEXT DEFAULT (datetime('now'))
);
"""

CREATE_SHADOWS = """
CREATE TABLE IF NOT EXISTS shadow_trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id        INTEGER NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
    trade_date      TEXT    NOT NULL,
    underlying      TEXT    NOT NULL,
    option_type     TEXT    NOT NULL,
    strike_price    REAL    NOT NULL,
    expiry_date     TEXT    NOT NULL,
    entry_premium   REAL    NOT NULL,
    final_pnl_pct   REAL,
    outcome         TEXT,               -- ShadowOutcome enum value
    closed_snap     TEXT,
    is_closed       INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now'))
);
"""

CREATE_SHADOW_SNAPS = """
CREATE TABLE IF NOT EXISTS shadow_snaps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    shadow_id   INTEGER NOT NULL REFERENCES shadow_trades(id) ON DELETE CASCADE,
    snap_time   TEXT    NOT NULL,
    ltp         REAL,
    pnl_pct     REAL,
    hit_sl      INTEGER DEFAULT 0,
    hit_target  INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now'))
);
"""

CREATE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_trades_status          ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_date            ON trades(trade_date);
CREATE INDEX IF NOT EXISTS idx_trades_underlying      ON trades(underlying);
CREATE INDEX IF NOT EXISTS idx_trades_status_ul       ON trades(status, underlying);
CREATE INDEX IF NOT EXISTS idx_trades_session         ON trades(session);
CREATE INDEX IF NOT EXISTS idx_snaps_trade            ON position_snaps(trade_id);
CREATE INDEX IF NOT EXISTS idx_shadow_trade_id        ON shadow_trades(trade_id);
CREATE INDEX IF NOT EXISTS idx_shadow_date            ON shadow_trades(trade_date);
CREATE INDEX IF NOT EXISTS idx_shadow_snaps_shadow_id ON shadow_snaps(shadow_id);
"""

# ---------------------------------------------------------------------------
# Additive-only migrations for existing databases.
#
# WHY THIS LIST EXISTS
# --------------------
# CREATE TABLE IF NOT EXISTS creates ALL columns for fresh installs.
# Existing databases (created before a column was added) need ALTER TABLE.
# SQLite raises OperationalError("duplicate column name") on re-runs;
# _run_migrations() catches that silently so every entry is idempotent.
#
# RULES FOR ADDING NEW COLUMNS
# ----------------------------
# 1. Add the column to CREATE_TRADES above (for fresh installs).
# 2. Add a corresponding ALTER TABLE line here (for existing databases).
# 3. Never remove or reorder existing entries -- they are idempotent no-ops
#    on databases that already have the column.
# ---------------------------------------------------------------------------
_MIGRATIONS = [
    # -- original column -------------------------------------------------------
    "ALTER TABLE trades ADD COLUMN session            TEXT",

    # -- columns added after initial schema release ----------------------------
    # Fix-A: these columns were present in CREATE_TRADES DDL but missing
    # from _MIGRATIONS, causing OperationalError on upgrade installs.
    "ALTER TABLE trades ADD COLUMN actual_entry_price REAL",
    "ALTER TABLE trades ADD COLUMN s_score            REAL",
    "ALTER TABLE trades ADD COLUMN quality_grade      TEXT",
    "ALTER TABLE trades ADD COLUMN direction_signals  TEXT",
    "ALTER TABLE trades ADD COLUMN narrative          TEXT",
    "ALTER TABLE trades ADD COLUMN rejection_reason   TEXT",
    "ALTER TABLE trades ADD COLUMN rejection_note     TEXT",
    "ALTER TABLE trades ADD COLUMN delta              REAL",
    "ALTER TABLE trades ADD COLUMN theta              REAL",
    "ALTER TABLE trades ADD COLUMN vega               REAL",
    "ALTER TABLE trades ADD COLUMN gamma              REAL",
    "ALTER TABLE trades ADD COLUMN iv_at_entry        REAL",
    "ALTER TABLE trades ADD COLUMN spot_at_entry      REAL",
    "ALTER TABLE trades ADD COLUMN conf_buckets       TEXT",

    # Fix-P1-14: accept_snap_time stores the acceptance snap; snap_time now
    # remains the immutable AI generation time for the lifetime of the row.
    "ALTER TABLE trades ADD COLUMN accept_snap_time   TEXT",
]


def init_db(conn) -> None:
    """Create all tables and indexes, then apply any pending column migrations.
    Idempotent -- safe to call on every new connection.
    """
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        CREATE_TRADES
        + CREATE_POSITION_SNAPS
        + CREATE_SHADOWS
        + CREATE_SHADOW_SNAPS
        + CREATE_INDEXES
    )
    conn.commit()
    _run_migrations(conn)


def _run_migrations(conn) -> None:
    """Apply ALTER TABLE migrations for existing databases.

    Each migration is attempted once. Only OperationalError("duplicate column
    name") is silenced -- this is the expected benign result when the column
    already exists on a re-run, making every entry idempotent.

    Fix-P1-13: all other OperationalError subtypes (table locked, disk full,
    wrong column type, etc.) previously fell through the bare `except Exception:
    pass` and were silently swallowed. The column then simply did not exist;
    the first INSERT/SELECT against it raised a cryptic runtime
    OperationalError with no trace back to the failed migration.
    Now they raise RuntimeError at startup so the failure is loud and
    immediately traceable to the offending migration statement.
    """
    for sql in _MIGRATIONS:
        try:
            conn.execute(sql)
            conn.commit()
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                pass  # idempotent -- column already exists on a re-run
            else:
                raise RuntimeError(
                    f"Migration failed (non-duplicate error): {sql!r}\n{e}"
                ) from e
