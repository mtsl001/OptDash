"""SQLite journal schema — CREATE TABLE statements."""

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
    actual_entry_price  REAL,               -- set on ACCEPT
    sl_price            REAL    NOT NULL,
    target_price        REAL    NOT NULL,
    exit_premium        REAL,
    exit_snap_time      TEXT,
    exit_reason         TEXT,               -- ExitReason enum
    final_pnl_abs       REAL,
    final_pnl_pct       REAL,
    confidence          INTEGER NOT NULL,
    gate_score          INTEGER NOT NULL,
    gate_verdict        TEXT    NOT NULL,
    s_score             REAL,
    quality_grade       TEXT,
    direction_signals   TEXT,               -- JSON
    narrative           TEXT,
    status              TEXT    NOT NULL DEFAULT 'GENERATED',
    rejection_reason    TEXT,
    rejection_note      TEXT,
    delta               REAL,
    theta               REAL,
    vega                REAL,
    gamma               REAL,
    iv_at_entry         REAL,
    spot_at_entry       REAL,
    conf_buckets        TEXT,               -- JSON
    created_at          TEXT    DEFAULT (datetime('now')),
    updated_at          TEXT    DEFAULT (datetime('now'))
);
"""

CREATE_POSITION_SNAPS = """
CREATE TABLE IF NOT EXISTS position_snaps (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id        INTEGER NOT NULL REFERENCES trades(id),
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
    trade_id        INTEGER NOT NULL REFERENCES trades(id),
    trade_date      TEXT    NOT NULL,
    underlying      TEXT    NOT NULL,
    option_type     TEXT    NOT NULL,
    strike_price    REAL    NOT NULL,
    expiry_date     TEXT    NOT NULL,
    entry_premium   REAL    NOT NULL,
    final_pnl_pct   REAL,
    outcome         TEXT,               -- ShadowOutcome enum
    closed_snap     TEXT,
    is_closed       INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now'))
);
"""

CREATE_SHADOW_SNAPS = """
CREATE TABLE IF NOT EXISTS shadow_snaps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    shadow_id   INTEGER NOT NULL REFERENCES shadow_trades(id),
    snap_time   TEXT    NOT NULL,
    ltp         REAL,
    pnl_pct     REAL,
    hit_sl      INTEGER DEFAULT 0,
    hit_target  INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now'))
);
"""

CREATE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_trades_status    ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_date      ON trades(trade_date);
CREATE INDEX IF NOT EXISTS idx_trades_underlying ON trades(underlying);
CREATE INDEX IF NOT EXISTS idx_snaps_trade      ON position_snaps(trade_id);
CREATE INDEX IF NOT EXISTS idx_shadow_date      ON shadow_trades(trade_date);
"""


def init_db(conn) -> None:
    """Create all tables and indexes."""
    conn.executescript(
        CREATE_TRADES +
        CREATE_POSITION_SNAPS +
        CREATE_SHADOWS +
        CREATE_SHADOW_SNAPS +
        CREATE_INDEXES
    )
    conn.commit()
