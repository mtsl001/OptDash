"""Learning report — comprehensive performance analysis."""
import sqlite3
from optdash.ai.learning.stats import get_session_stats, get_threshold_performance
from optdash.ai.journal.shadow import get_shadow_history


def build_learning_report(conn: sqlite3.Connection, days: int = 30) -> dict:
    """
    Full learning report covering:
    - Overall performance
    - By underlying / direction / session / exit reason  (all scoped to `days`)
    - Threshold analysis (confidence, gate_score, s_score)
    - Shadow trade comparison with rejection accuracy
    - Rejection reason analysis
    """
    overall    = get_session_stats(conn)
    date_param = [f"-{days} days"]

    # By underlying — P4-F13: scoped to `days` window to match shadow comparison
    underlyings_rows = conn.execute(
        """SELECT underlying,
                  COUNT(*),
                  SUM(CASE WHEN final_pnl_pct > 0 THEN 1 ELSE 0 END),
                  AVG(final_pnl_pct)
           FROM trades
           WHERE status='CLOSED' AND final_pnl_pct IS NOT NULL
             AND trade_date >= date('now', ?)
           GROUP BY underlying""",
        date_param
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
        date_param
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
        date_param
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

    # By exit reason — P4-F13
    exit_rows = conn.execute(
        """SELECT exit_reason, COUNT(*), AVG(final_pnl_pct)
           FROM trades
           WHERE status='CLOSED'
             AND trade_date >= date('now', ?)
           GROUP BY exit_reason""",
        date_param
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
    # P4-F12: old top bucket (16, 100) missed all STAR-4 strikes (s_score 100–150).
    # Expanded to (16, 200) so the full s_score range is covered.
    sscore_buckets = get_threshold_performance(
        conn, "s_score", [(0, 8), (8, 12), (12, 16), (16, 200)]
    )

    # Rejection reason breakdown — P4-F13: scoped to `days` window
    reject_rows = conn.execute(
        """SELECT rejection_reason, COUNT(*)
           FROM trades
           WHERE status='REJECTED'
             AND trade_date >= date('now', ?)
           GROUP BY rejection_reason
           ORDER BY COUNT(*) DESC""",
        date_param
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

    # P4-F14a: replace noisy shadow_wins (shadows with pnl > 0) with
    # rejection_accuracy = GOOD_SKIP% (correctly identified losers).
    # GOOD_SKIP: shadow would have lost ≥20% — pre-flight was right to block.
    # CLEAN_MISS: shadow would have won ≥30% — costly false rejection.
    good_skips   = shadow_outcomes.get("GOOD_SKIP", 0)
    clean_misses = shadow_outcomes.get("CLEAN_MISS", 0)
    rejection_accuracy = (
        round(good_skips / shadow_total * 100, 1) if shadow_total else None
    )

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
            "total":                shadow_total,
            "rejection_accuracy":   rejection_accuracy,   # % GOOD_SKIP — correctly blocked
            "clean_misses":         clean_misses,          # costly false rejections
            "good_skips":           good_skips,
            "avg_hypothetical_pnl": shadow_avg_pnl,
            "outcomes":             shadow_outcomes,
        },
    }
