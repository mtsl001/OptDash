"""Learning report — comprehensive performance analysis."""
import sqlite3
from optdash.ai.learning.stats import get_session_stats, get_threshold_performance
from optdash.ai.journal.shadow import get_shadow_history


def build_learning_report(conn: sqlite3.Connection, days: int = 30) -> dict:
    """
    Full learning report covering:
    - Overall performance
    - By underlying
    - By direction (CE/PE)
    - By session
    - Threshold analysis (confidence, gate_score, s_score)
    - Shadow trade comparison
    - Rejection reason analysis
    """
    overall = get_session_stats(conn)

    # By underlying — P4-F13: scoped to the same `days` window as shadow stats
    # so live vs shadow win-rates are directly comparable (previously all-time).
    underlyings_rows = conn.execute(
        """SELECT underlying,
                  COUNT(*),
                  SUM(CASE WHEN final_pnl_pct > 0 THEN 1 ELSE 0 END),
                  AVG(final_pnl_pct)
           FROM trades
           WHERE status='CLOSED' AND final_pnl_pct IS NOT NULL
             AND trade_date >= date('now', ?)
           GROUP BY underlying""",
        [f"-{days} days"]
    ).fetchall()
    by_underlying = [
        {
            "underlying": r[0],
            "total":      r[1],
            # Guard: SUM(CASE...) returns NULL when zero rows match CASE — not 0
            "win_rate":   round((r[2] or 0) / r[1] * 100, 1) if r[1] else None,
            "avg_pnl":    round(r[3] or 0, 2),
        }
        for r in underlyings_rows
    ]

    # By direction — P4-F13
    direction_rows = conn.execute(
        """SELECT option_type,
                  COUNT(*),
                  SUM(CASE WHEN final_pnl_pct > 0 THEN 1 ELSE 0 END),
                  AVG(final_pnl_pct)
           FROM trades
           WHERE status='CLOSED' AND final_pnl_pct IS NOT NULL
             AND trade_date >= date('now', ?)
           GROUP BY option_type""",
        [f"-{days} days"]
    ).fetchall()
    by_direction = [
        {
            "direction": r[0],
            "total":     r[1],
            "win_rate":  round((r[2] or 0) / r[1] * 100, 1) if r[1] else None,
            "avg_pnl":   round(r[3] or 0, 2),
        }
        for r in direction_rows
    ]

    # By session — P4-F13
    session_rows = conn.execute(
        """SELECT session,
                  COUNT(*),
                  SUM(CASE WHEN final_pnl_pct > 0 THEN 1 ELSE 0 END),
                  AVG(final_pnl_pct)
           FROM trades
           WHERE status='CLOSED' AND final_pnl_pct IS NOT NULL
             AND session IS NOT NULL
             AND trade_date >= date('now', ?)
           GROUP BY session""",
        [f"-{days} days"]
    ).fetchall()
    by_session = [
        {
            "session":  r[0],
            "total":    r[1],
            "win_rate": round((r[2] or 0) / r[1] * 100, 1) if r[1] else None,
            "avg_pnl":  round(r[3] or 0, 2),
        }
        for r in session_rows
    ]

    # By exit reason — intentionally all-time: exit reason distribution is a
    # structural metric that benefits from the full trade history, not a
    # rolling window. A 30-day window on a low-frequency strategy would
    # produce misleadingly sparse per-reason counts.
    exit_rows = conn.execute(
        """SELECT exit_reason, COUNT(*), AVG(final_pnl_pct)
           FROM trades WHERE status='CLOSED'
           GROUP BY exit_reason"""
    ).fetchall()
    by_exit = [
        {"exit_reason": r[0], "count": r[1], "avg_pnl": round(r[2] or 0, 2)}
        for r in exit_rows
    ]

    # Threshold analysis
    confidence_buckets = get_threshold_performance(conn, "confidence")
    gate_buckets       = get_threshold_performance(
        conn, "gate_score", [(0, 5), (5, 7), (7, 8), (8, 9), (9, 12)]
    )
    # P4-F12: realigned to the full 0–150 STAR scale.
    # Old top bucket (16, 100) excluded all STAR-4 trades (s_score 100–150),
    # so the best setups were invisible in threshold analysis.
    # New buckets align with STAR2=60, STAR3=80, STAR4=100 thresholds;
    # 151 is the exclusive upper bound (max possible s_score is 150).
    sscore_buckets = get_threshold_performance(
        conn, "s_score", [(0, 60), (60, 80), (80, 100), (100, 151)]
    )

    # Rejection reason breakdown
    reject_rows = conn.execute(
        """SELECT rejection_reason, COUNT(*)
           FROM trades WHERE status='REJECTED'
           GROUP BY rejection_reason
           ORDER BY COUNT(*) DESC"""
    ).fetchall()
    rejection_analysis = [{"reason": r[0], "count": r[1]} for r in reject_rows]

    # Shadow comparison
    shadows        = get_shadow_history(conn, days=days)
    shadow_total   = len(shadows)
    shadow_avg_pnl = (
        round(sum(s.get("final_pnl_pct") or 0 for s in shadows) / shadow_total, 2)
        if shadow_total else 0
    )
    shadow_outcomes: dict[str, int] = {}
    for s in shadows:
        o = s.get("outcome", "UNKNOWN")
        shadow_outcomes[o] = shadow_outcomes.get(o, 0) + 1

    # P4-F14a: replaced shadow_wins (pnl > 0 — inflated by noise trades of
    # +0.1%) with outcome-based accuracy metrics.
    # rejection_accuracy = GOOD_SKIP / (GOOD_SKIP + CLEAN_MISS)
    #   Only decided outcomes count; RISKY_MISS / BREAK_EVEN are excluded
    #   from the denominator so ambiguous outcomes do not dilute the signal.
    # clean_miss_rate = CLEAN_MISS / shadow_total
    #   What fraction of all shadows were costly rejections (gate was wrong)?
    shadow_good_skips    = shadow_outcomes.get("GOOD_SKIP", 0)
    shadow_clean_misses  = shadow_outcomes.get("CLEAN_MISS", 0)
    shadow_total_decided = shadow_good_skips + shadow_clean_misses

    return {
        "overall":            overall,
        "by_underlying":      by_underlying,
        "by_direction":       by_direction,
        "by_session":         by_session,
        "by_exit_reason":     by_exit,
        "confidence_buckets": confidence_buckets,
        "gate_buckets":       gate_buckets,
        "sscore_buckets":     sscore_buckets,
        "rejection_analysis": rejection_analysis,
        "shadow_summary": {
            "total":    shadow_total,
            "avg_pnl":  shadow_avg_pnl,
            "outcomes": shadow_outcomes,
            # Outcome-based accuracy — replaces raw pnl>0 shadow_wins count.
            "rejection_accuracy": round(
                shadow_good_skips / shadow_total_decided * 100, 1
            ) if shadow_total_decided else None,
            "clean_miss_rate": round(
                shadow_clean_misses / shadow_total * 100, 1
            ) if shadow_total else None,
        },
    }
