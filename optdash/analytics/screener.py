"""Strike screener — S_score ranking with star ratings."""
import duckdb
from loguru import logger
from optdash.config import settings


def get_strikes(
    conn: duckdb.DuckDBPyConnection,
    trade_date: str,
    snap_time:  str,
    underlying: str,
    top_n: int = 20,
) -> list[dict]:
    """
    Return top_n strikes ranked by S_score.
    Filters: moneyness <=5%, delta 0.10-0.50, liquidity_cr >=0.5.
    """
    try:
        rows = conn.execute("""
            WITH spot_cte AS (
                SELECT AVG(spot) AS spot FROM options_data
                WHERE trade_date=? AND snap_time=? AND underlying=?
            ),
            ranked AS (
                SELECT
                    o.expiry_date, o.expiry_tier, o.dte, o.option_type, o.strike_price,
                    o.ltp, o.iv, o.delta, o.theta, o.gamma, o.vega, o.rho,
                    (o.strike_price - s.spot) / s.spot * 100  AS moneyness_pct,
                    o.oi * o.ltp / 1e7                        AS liquidity_cr,
                    ABS(o.theta) / NULLIF(o.ltp, 0)           AS eff_ratio,
                    (
                        ? * ABS(o.delta)
                      + ? * (1.0 - LEAST(1.0, ABS(o.theta) / NULLIF(o.ltp, 0) / 0.05))
                      + ? * LEAST(1.0, o.oi * o.ltp / 1e7 / 5.0)
                      + ? * (1.0 - LEAST(1.0, o.iv / 100.0))
                      + ? * LEAST(1.0, ABS(o.gamma) * 100)
                      + ? * LEAST(1.0, ABS(o.vega) / 50.0)
                      + ? * ABS(o.delta)
                    ) * 10                                     AS s_score
                FROM options_data o, spot_cte s
                WHERE o.trade_date=? AND o.snap_time=? AND o.underlying=?
                  AND ABS((o.strike_price - s.spot) / s.spot * 100) <= ?
                  AND ABS(o.delta) BETWEEN ? AND ?
                  AND o.oi * o.ltp / 1e7 >= ?
                  AND o.ltp > 0
            )
            SELECT *, CASE
                WHEN s_score >= ? THEN 4
                WHEN s_score >= ? THEN 3
                WHEN s_score >= ? THEN 2
                ELSE 1 END AS stars
            FROM ranked
            ORDER BY s_score DESC
            LIMIT ?
        """, [
            trade_date, snap_time, underlying,
            settings.W_DELTA, settings.W_THETA, settings.W_LIQUIDITY,
            settings.W_IV,    settings.W_GAMMA,  settings.W_VEGA, settings.W_EFF_RATIO,
            trade_date, snap_time, underlying,
            settings.SCREENER_MAX_MONEYNESS_PCT,
            settings.SCREENER_MIN_DELTA, settings.SCREENER_MAX_DELTA,
            settings.SCREENER_MIN_LIQUIDITY_CR,
            settings.STAR_4_THRESHOLD, settings.STAR_3_THRESHOLD, settings.STAR_2_THRESHOLD,
            top_n,
        ]).fetchall()

        cols = [
            "expiry_date", "expiry_tier", "dte", "option_type", "strike_price",
            "ltp", "iv", "delta", "theta", "gamma", "vega", "rho",
            "moneyness_pct", "liquidity_cr", "eff_ratio", "s_score", "stars",
        ]
        return [
            {k: (round(v, 4) if isinstance(v, float) else v) for k, v in zip(cols, r)}
            for r in rows
        ]
    except Exception as e:
        logger.warning("get_strikes error: {}", e)
        return []
