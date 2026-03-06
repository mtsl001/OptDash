"""SQLite journal schema — CREATE TABLE + INDEX statements.

All tables use CREATE TABLE IF NOT EXISTS so init_db() is safely idempotent
and can be called on every fresh connection without side effects.

For existing databases, _run_migrations() uses ALTER TABLE to add new columns;
SQLite silently raises OperationalError on duplicate columns which we catch.
"""

CREATE_TRADES = """
CREATE TABLE IF NOT EXISTS trades (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date          TEXT    NOT NULL,
    snap_time           TEXT    NOT NULL,
    underlying          TEXT    NOT NULL,
    option_type         TEXT    NOT NULL,   -- CE | PE
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
    gate_verdict        TEXT    NOT NULL,
    s_score             REAL,
    quality_grade       TEXT,
    direction_signals   TEXT,               -- JSON blob
    narrative           TEXT,
    status              TEXT    NOT NULL DEFAULT 'GENERATED',
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
# 3. Never remove or reorder existing entries — they are idempotent no-ops
#    on databases that already have the column.
# ---------------------------------------------------------------------------
_MIGRATIONS = [
    # ── original column ────────────────────────────────────────────────────
    "ALTER TABLE trades ADD COLUMN session            TEXT",

    # ── columns added after initial schema release ─────────────────────────
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
]


def init_db(conn) -> None:
    """Create all tables and indexes, then apply any pending column migrations.
    Idempotent — safe to call on every new connection.
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

    Each migration is attempted once. Duplicate-column errors are silently
    ignored — this makes every entry idempotent on any database state.
    Other unexpected errors are also suppressed to avoid blocking startup;
    they will surface as runtime errors on first use of the missing column.
    """
    for sql in _MIGRATIONS:
        try:
            conn.execute(sql)
            conn.commit()
        except Exception:
            pass  # Column already exists (expected on re-runs) or other benign error
