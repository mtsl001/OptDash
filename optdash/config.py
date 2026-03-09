"""Central settings - all tuneable constants in one place."""
import re
from pathlib import Path
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # -- Paths
    DATA_ROOT:       Path = Path("./data")
    DUCKDB_PATH:     Path = Path("./data/optdash.duckdb")
    JOURNAL_DB_PATH: Path = Path("./data/journal.db")

    # -- API
    API_HOST:     str       = "0.0.0.0"
    API_PORT:     int       = 8000
    LOG_LEVEL:    str       = "INFO"
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # Fix-P1-5: UNDERLYINGS is declared before DEFAULT_UNDERLYING so the
    # _check_default_underlying validator can reference it via info.data.
    # All supported underlyings — single source of truth.
    # Add / remove here; every loop in the system reads this list.
    UNDERLYINGS: list[str] = [
        "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "SENSEX"
    ]

    # Default underlying for endpoints that take an optional underlying param.
    # Override via DEFAULT_UNDERLYING= in .env. Must be a member of UNDERLYINGS.
    DEFAULT_UNDERLYING: str = "NIFTY"

    @field_validator("DEFAULT_UNDERLYING")
    @classmethod
    def _check_default_underlying(cls, v: str, info) -> str:
        underlyings = (info.data or {}).get("UNDERLYINGS", [])
        if underlyings and v not in underlyings:
            raise ValueError(
                f"DEFAULT_UNDERLYING={v!r} is not in UNDERLYINGS={underlyings}"
            )
        return v

    # -- Scheduler
    SCHEDULER_INTERVAL_SECONDS: int = 300  # 5-min tick
    WS_INTERVAL_SECONDS:        int = 5    # WebSocket push cadence

    MARKET_OPEN:          str = "09:15"
    MARKET_CLOSE:         str = "15:30"
    EOD_FORCE_CLOSE_TIME: str = "15:20"
    EOD_SWEEP_TIME:       str = "15:25"

    # Fix-P1-7: all time-string fields validated to strict HH:MM (zero-padded).
    # Without this, "9:5" passes pydantic but breaks strptime and snap_time
    # string comparisons throughout the codebase.
    @field_validator(
        "MARKET_OPEN", "MARKET_CLOSE", "EOD_FORCE_CLOSE_TIME", "EOD_SWEEP_TIME",
        "SESSION_OPENING_END", "SESSION_MIDDAY_START", "SESSION_MIDDAY_END",
        "SESSION_CLOSING_START", "DEALER_OCLOCK_START",
        mode="before",
    )
    @classmethod
    def _check_hhmm(cls, v: str) -> str:
        if not re.fullmatch(r"\d{2}:\d{2}", str(v)):
            raise ValueError(f"Expected HH:MM format (e.g. '09:15'), got {v!r}")
        return v

    # NSE market holidays — scheduler skips all ticks on these dates so no
    # DuckDB scans or empty-data log noise accumulate on non-trading days.
    # Format: YYYY-MM-DD strings. Default is empty (no holidays configured).
    # Set in .env as a JSON array (pydantic-settings parses it automatically):
    #   MARKET_HOLIDAYS=["2026-03-14","2026-04-14","2026-04-18"]
    # The scheduler reads this via settings.MARKET_HOLIDAYS; a missing / empty
    # list simply disables the holiday skip without any error.
    MARKET_HOLIDAYS: list[str] = []

    # -- Per-Underlying Market Metadata
    # Fix-P1-6: inner-type annotations added to all per-underlying dicts so
    # pydantic validates element types when .env overrides supply JSON.
    # A single _check_underlying_coverage validator (below) confirms that every
    # known underlying has an entry in each dict — a missing key would cause a
    # silent None return and a TypeError (e.g. None * lot_size) mid-trade.

    # Lot sizes (NSE/BSE exchange-defined - last verified Mar 2026; review on contract rollover).
    LOT_SIZES: dict[str, int] = {
        "NIFTY": 75, "BANKNIFTY": 15, "FINNIFTY": 40,
        "MIDCPNIFTY": 120, "NIFTYNXT50": 10, "SENSEX": 10,
    }
    # Strike price intervals in points between adjacent strikes.
    STRIKE_INTERVALS: dict[str, int] = {
        "NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50,
        "MIDCPNIFTY": 25, "NIFTYNXT50": 50, "SENSEX": 100,
    }
    # Weekly expiry weekday per underlying (Python weekday: 0=Mon ... 4=Fri).
    # BANKNIFTY  -> Wednesday(2) [NSE moved from Thursday, effective Sep 2023],
    # FINNIFTY   -> Tuesday(1),
    # MIDCPNIFTY -> Monday(0),
    # NIFTYNXT50 / SENSEX -> Friday(4),
    # NIFTY      -> Thursday(3).
    EXPIRY_WEEKDAY: dict[str, int] = {
        "NIFTY": 3, "BANKNIFTY": 2,
        "FINNIFTY": 1,
        "MIDCPNIFTY": 0,
        "NIFTYNXT50": 4, "SENSEX": 4,
    }

    # -- Parquet Schema
    PARQUET_DATE_COL:       str  = "trade_date"
    PARQUET_SNAP_COL:       str  = "snap_time"
    PARQUET_UNDERLYING_COL: str  = "underlying"
    EXPIRY_TIERS: dict = {"TIER1": 0, "TIER2": 1, "TIER3": 2}
    # Raw Parquet files (pre-enrichment) are purged after this many calendar days.
    # Processed Parquets are kept permanently as the audit trail.
    RAW_PARQUET_RETENTION_DAYS: int = 3
    # Rolling lookback window for the DuckDB options_data view.
    # Only the last N calendar days of data/processed/ are included in the
    # view, bounding scan cost regardless of how long the service runs.
    # 5 = full Mon-Fri week; increase for longer historical analytics.
    DUCK_VIEW_LOOKBACK_DAYS: int = 5

    # -- GEX
    GEX_NEAR_WEEKS:        int   = 2
    GEX_DECLINE_THRESHOLD: float = 0.70  # 70% of peak -> declining
    GEX_SCALING:           float = 1e9   # raw / 1B -> display in Rs B

    # -- CoC / V_CoC
    VCOC_BULL_THRESHOLD:     float = 10.0
    VCOC_BEAR_THRESHOLD:     float = -10.0
    VCOC_SPIKE_EXPIRY_SNAPS: int   = 3
    COC_DISCOUNT_THRESHOLD:  float = -5.0

    # -- VEX / CEX
    # VEX_BULL_THRESHOLD: global fallback for unknown underlyings only.
    # For all known underlyings, VEX_THRESHOLDS takes precedence.
    VEX_BULL_THRESHOLD:  float = 0.0
    # Per-underlying VEX magnitude thresholds (Rs M).
    # Scaled to index liquidity: liquid large-caps (NIFTY/BANKNIFTY) need a
    # higher bar to filter noise; illiquid underlyings need a lower bar.
    # Fix-B: these were absent -- classifiers used VEX_BULL_THRESHOLD=0.0
    # for all underlyings, causing spurious signals on low-volume indices.
    VEX_THRESHOLDS: dict[str, float] = {
        "NIFTY": 0.50, "BANKNIFTY": 0.50, "FINNIFTY": 0.25,
        "MIDCPNIFTY": 0.15, "NIFTYNXT50": 0.15, "SENSEX": 0.25,
    }
    CEX_STRONG_BID:      float = 20.0
    CEX_BID:             float = 5.0
    CEX_PRESSURE:        float = -20.0
    DEALER_OCLOCK_DTE:   int   = 1
    DEALER_OCLOCK_START: str   = "14:00"
    # Per-underlying CEX magnitude thresholds (Rs M) - scaled to index liquidity.
    # CEX_CHARM_THRESHOLD -> STRONG_CHARM_BID level (replaces global CEX_STRONG_BID).
    # CEX_VANNA_THRESHOLD -> CHARM_BID mid-level   (replaces global CEX_BID).
    CEX_CHARM_THRESHOLD: dict[str, float] = {
        "NIFTY": 20.0, "BANKNIFTY": 20.0, "FINNIFTY": 10.0,
        "MIDCPNIFTY": 5.0, "NIFTYNXT50": 5.0, "SENSEX": 10.0,
    }
    CEX_VANNA_THRESHOLD: dict[str, float] = {
        "NIFTY": 12.0, "BANKNIFTY": 12.0, "FINNIFTY": 6.0,
        "MIDCPNIFTY": 3.0, "NIFTYNXT50": 3.0, "SENSEX": 6.0,
    }

    # -- PCR
    PCR_DIV_BULL_THRESHOLD: float = 0.25
    PCR_DIV_BEAR_THRESHOLD: float = -0.20

    # -- OBI
    OBI_THRESHOLD: float = 0.10
    # Futures OBI threshold for Gate Condition 3 (sellers dominant).
    # Per-underlying dict: liquid indices (NIFTY/BANKNIFTY) need a tighter filter;
    # illiquid ones (MIDCPNIFTY/NIFTYNXT50) have naturally wider OBI swings.
    FUT_OBI_BEAR_THRESHOLD: dict[str, float] = {
        "NIFTY": -0.20, "BANKNIFTY": -0.20,
        "FINNIFTY": -0.25,
        "MIDCPNIFTY": -0.35,
        "NIFTYNXT50": -0.30,
        "SENSEX": -0.30,
    }

    # -- IV
    IV_LOOKBACK_DAYS: int = 252
    # IV crush HIGH severity Vega threshold -- per underlying.
    # Unit: option price points per 1% IV change (confirmed from screener
    # normalisation: vega / ltp / 0.50). NOT raw BSM decimal Vega.
    # Calibrated against typical ATM Vega for near-expiry options:
    #   NIFTY      ~8-15  pts (ATM, 3-7 DTE)   -> threshold 15
    #   BANKNIFTY  ~30-70 pts (ATM, 5-10 DTE)  -> threshold 30
    #   FINNIFTY   ~6-12  pts                   -> threshold 10
    #   MIDCPNIFTY ~3-8   pts                   -> threshold  8
    #   NIFTYNXT50 ~3-8   pts                   -> threshold  8
    #   SENSEX     ~20-50 pts                   -> threshold 20
    IV_CRUSH_HIGH_VEGA: dict[str, float] = {
        "NIFTY": 15.0, "BANKNIFTY": 30.0, "FINNIFTY": 10.0,
        "MIDCPNIFTY": 8.0, "NIFTYNXT50": 8.0, "SENSEX": 20.0,
    }

    # Fix-P1-6 (continued): validator ensuring all per-underlying dicts have
    # complete key coverage for every underlying in UNDERLYINGS.
    # Runs for each named dict field after UNDERLYINGS has been processed
    # (field declaration order guarantees UNDERLYINGS is in info.data).
    @field_validator(
        "LOT_SIZES", "STRIKE_INTERVALS", "EXPIRY_WEEKDAY",
        "VEX_THRESHOLDS", "CEX_CHARM_THRESHOLD", "CEX_VANNA_THRESHOLD",
        "FUT_OBI_BEAR_THRESHOLD", "IV_CRUSH_HIGH_VEGA",
    )
    @classmethod
    def _check_underlying_coverage(cls, v: dict, info) -> dict:
        underlyings = (info.data or {}).get("UNDERLYINGS", [])
        missing = set(underlyings) - set(v.keys())
        if missing:
            raise ValueError(
                f"{info.field_name} is missing keys for underlyings: {missing}"
            )
        return v

    # -- Strike Screener
    SCREENER_TOP_N:             int   = 20
    SCREENER_MAX_MONEYNESS_PCT: float = 5.0
    SCREENER_MIN_LIQUIDITY_CR:  float = 0.5
    SCREENER_MIN_DELTA:         float = 0.10
    SCREENER_MAX_DELTA:         float = 0.50
    SCREENER_MIN_EFF_RATIO:     float = 0.10

    # -- S_score Weights
    W_DELTA:      float = 4.0
    W_EFF_RATIO:  float = 4.0
    W_LIQUIDITY:  float = 3.0
    W_IV:         float = 2.0
    W_THETA:      float = 2.0
    W_GAMMA:      float = 1.0
    W_VEGA:       float = 1.0
    # Star thresholds calibrated against the 0-~150 S_score scale.
    # Actual max = (W_DELTA x 0.50 + W_EFF_RATIO + W_LIQUIDITY + W_IV
    #               + W_THETA + W_GAMMA + W_VEGA) x 10 = 150.
    # Typical well-screened options score 70-120.
    STAR_4_THRESHOLD: float = 100.0  # >=67% of max - excellent
    STAR_3_THRESHOLD: float =  80.0  # >=53% of max - good
    STAR_2_THRESHOLD: float =  60.0  # >=40% of max - acceptable

    # -- Environment Gate
    GATE_GO_THRESHOLD:   int = 7
    GATE_WAIT_THRESHOLD: int = 5
    GATE_MAX_SCORE:      int = 11

    # -- Session Boundaries
    SESSION_OPENING_END:   str = "10:15"
    SESSION_MIDDAY_START:  str = "11:30"
    SESSION_MIDDAY_END:    str = "13:00"
    SESSION_CLOSING_START: str = "14:30"

    # -- AI Recommender
    PREFLIGHT_MIN_GATE_SCORE:      int   = 5
    PREFLIGHT_MIN_CONFIDENCE:      int   = 50
    PREFLIGHT_MAX_THETA_RATIO:     float = 0.03
    PREFLIGHT_MAX_PAIN_PROXIMITY:  float = 0.005
    PREFLIGHT_MIN_SSCORE:          float = 60.0   # blocks strikes below 40% of 0-150 scale
    PREFLIGHT_DTE1_MIN_GATE:       int   = 7
    PREFLIGHT_DTE1_MIN_CONFIDENCE: int   = 65

    AI_SL_PCT:           float = 0.35    # SL at entry * (1 - 0.35)
    AI_TARGET_MULT:      float = 1.50    # Target at entry * 1.50
    AI_EXPIRY_MAX_SNAPS: int   = 3       # Expire recommendation after 3 unactioned snaps

    # Fix-P1-8: bounds validators for fractional/multiplier config fields.
    # AI_SL_PCT=35 (user forgets fractional) produces sl = entry*(1-35) = negative.
    # AI_TARGET_MULT<=1.0 would set target at or below entry price.
    @field_validator("AI_SL_PCT")
    @classmethod
    def _check_sl_pct(cls, v: float) -> float:
        if not (0.0 < v < 1.0):
            raise ValueError(f"AI_SL_PCT must be in (0, 1), got {v}")
        return v

    @field_validator("AI_TARGET_MULT")
    @classmethod
    def _check_target_mult(cls, v: float) -> float:
        if v <= 1.0:
            raise ValueError(f"AI_TARGET_MULT must be > 1.0, got {v}")
        return v

    TRAILING_STOP_ACTIVATION:   float = 0.20   # Activate trail at +20% PnL
    GATE_SUSTAINED_NO_GO_SNAPS: int   = 2      # Exit after 2 consecutive NO_GO snaps

    SESSION_MIDDAY_CONFIDENCE_PENALTY: int = 10
    # Fix-D: renamed from SESSION_CLOSING_MIN_CONFIDENCE.
    # This is a CAP (upper bound), not a floor. During CLOSING_CRUSH session,
    # confidence is capped at this value to prevent overconfident late entries.
    # Setting it to a high number (e.g. 80) does NOT require high confidence
    # -- it merely stops the cap from biting. To require high confidence,
    # use PREFLIGHT_MIN_CONFIDENCE instead.
    SESSION_CLOSING_CONFIDENCE_CAP: int = 60


settings = Settings()
