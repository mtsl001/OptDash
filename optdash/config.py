"""Central settings — all tuneable constants in one place."""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ── Paths ───────────────────────────────────────────────────────────────────
    DATA_ROOT:       Path = Path("./data")
    DUCKDB_PATH:     Path = Path("./data/optdash.duckdb")
    JOURNAL_DB_PATH: Path = Path("./data/journal.db")

    # ── API ──────────────────────────────────────────────────────────────────
    API_HOST:     str       = "0.0.0.0"
    API_PORT:     int       = 8000
    LOG_LEVEL:    str       = "INFO"
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # ── Scheduler ─────────────────────────────────────────────────────────────
    SCHEDULER_INTERVAL_SECONDS: int       = 300           # 5-min tick
    WS_INTERVAL_SECONDS:        int       = 5             # WebSocket push cadence
    UNDERLYINGS:                list[str] = ["NIFTY", "BANKNIFTY"]
    MARKET_OPEN:   str = "09:15"
    MARKET_CLOSE:  str = "15:30"
    EOD_FORCE_CLOSE_TIME: str = "15:20"
    EOD_SWEEP_TIME:       str = "15:25"

    # ── Parquet Schema ────────────────────────────────────────────────────────
    PARQUET_DATE_COL:       str  = "trade_date"
    PARQUET_SNAP_COL:       str  = "snap_time"
    PARQUET_UNDERLYING_COL: str  = "underlying"
    EXPIRY_TIERS: dict = {"TIER1": 0, "TIER2": 1, "TIER3": 2}

    # ── GEX ───────────────────────────────────────────────────────────────────
    GEX_NEAR_WEEKS:         int   = 2
    GEX_DECLINE_THRESHOLD:  float = 0.70     # 70% of peak = declining
    GEX_SCALING:            float = 1e9      # raw ÷ 1B = display in ₹B

    # ── CoC / V_CoC ──────────────────────────────────────────────────────────
    VCOC_BULL_THRESHOLD:       float = 10.0
    VCOC_BEAR_THRESHOLD:       float = -10.0
    VCOC_SPIKE_EXPIRY_SNAPS:   int   = 3
    COC_DISCOUNT_THRESHOLD:    float = -5.0

    # ── VEX / CEX ───────────────────────────────────────────────────────────
    VEX_BULL_THRESHOLD:    float = 0.0
    CEX_STRONG_BID:        float = 20.0
    CEX_BID:               float = 5.0
    CEX_PRESSURE:          float = -20.0
    DEALER_OCLOCK_DTE:     int   = 1
    DEALER_OCLOCK_START:   str   = "14:00"

    # ── PCR ───────────────────────────────────────────────────────────────────
    PCR_DIV_BULL_THRESHOLD: float = 0.25
    PCR_DIV_BEAR_THRESHOLD: float = -0.20

    # ── OBI ───────────────────────────────────────────────────────────────────
    OBI_THRESHOLD:          float = 0.10
    FUT_OBI_BEAR_THRESHOLD: float = -0.20

    # ── IV ───────────────────────────────────────────────────────────────────
    IV_LOOKBACK_DAYS:       int   = 252
    IV_CRUSH_HIGH_VEGA:     float = 50.0

    # ── Strike Screener ────────────────────────────────────────────────────────
    SCREENER_TOP_N:             int   = 20
    SCREENER_MAX_MONEYNESS_PCT: float = 5.0
    SCREENER_MIN_LIQUIDITY_CR:  float = 0.5
    SCREENER_MIN_DELTA:         float = 0.10
    SCREENER_MAX_DELTA:         float = 0.50
    SCREENER_MIN_EFF_RATIO:     float = 0.10

    # ── S_score Weights ────────────────────────────────────────────────────────
    W_DELTA:      float = 4.0
    W_EFF_RATIO:  float = 4.0
    W_LIQUIDITY:  float = 3.0
    W_IV:         float = 2.0
    W_THETA:      float = 2.0
    W_GAMMA:      float = 1.0
    W_VEGA:       float = 1.0
    STAR_4_THRESHOLD: float = 20.0
    STAR_3_THRESHOLD: float = 15.0
    STAR_2_THRESHOLD: float = 10.0

    # ── Environment Gate ────────────────────────────────────────────────────────
    GATE_GO_THRESHOLD:   int = 7
    GATE_WAIT_THRESHOLD: int = 5
    GATE_MAX_SCORE:      int = 11

    # ── Session Boundaries ───────────────────────────────────────────────────────
    SESSION_OPENING_END:   str = "10:15"
    SESSION_MIDDAY_START:  str = "11:30"
    SESSION_MIDDAY_END:    str = "13:00"
    SESSION_CLOSING_START: str = "14:30"

    # ── AI Recommender ──────────────────────────────────────────────────────────
    PREFLIGHT_MIN_GATE_SCORE:      int   = 5
    PREFLIGHT_MIN_CONFIDENCE:      int   = 50
    PREFLIGHT_MAX_THETA_RATIO:     float = 0.03
    PREFLIGHT_MAX_PAIN_PROXIMITY:  float = 0.005
    PREFLIGHT_MIN_SSCORE:          float = 8.0
    PREFLIGHT_DTE1_MIN_GATE:       int   = 7
    PREFLIGHT_DTE1_MIN_CONFIDENCE: int   = 65

    AI_SL_PCT:           float = 0.35    # SL at entry * (1 - 0.35)
    AI_TARGET_MULT:      float = 1.50    # Target at entry * 1.50
    AI_EXPIRY_MAX_SNAPS: int   = 3       # Expire recommendation after 3 unactioned snaps

    TRAILING_STOP_ACTIVATION:  float = 0.20   # Activate trail at +20% PnL
    GATE_SUSTAINED_NO_GO_SNAPS:int   = 2      # Exit after 2 consecutive NO_GO snaps

    SESSION_MIDDAY_CONFIDENCE_PENALTY: int = 10
    SESSION_CLOSING_MIN_CONFIDENCE:    int = 60


settings = Settings()
