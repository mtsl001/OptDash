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
    - Threshold analysis (confidence, gate_score, s_score)
    - Shadow trade comparison (what would have happened if we accepted)
    - Rejection reason analysis
    """
    overall = get_session_stats(conn)

    # By underlying
    underlyings_rows = conn.execute(
        """SELECT underlying, COUNT(*),
               SUM(CASE WHEN final_pnl_pct>0 THEN 1 ELSE 0 END),
               AVG(final_pnl_pct)
           FROM trades WHERE status='CLOSED' AND final_pnl_pct IS NOT NULL
           GROUP BY underlying"""
    ).fetchall()
    by_underlying = [
        {"underlying": r[0],
         "total": r[1],
         "win_rate": round(r[2] / r[1] * 100, 1) if r[1] else None,
         "avg_pnl": round(r[3], 2)}
        for r in underlyings_rows
    ]

    # By direction
    direction_rows = conn.execute(
        """SELECT option_type, COUNT(*),
               SUM(CASE WHEN final_pnl_pct>0 THEN 1 ELSE 0 END),
               AVG(final_pnl_pct)
           FROM trades WHERE status='CLOSED' AND final_pnl_pct IS NOT NULL
           GROUP BY option_type"""
    ).fetchall()
    by_direction = [
        {"direction": r[0],
         "total":     r[1],
         "win_rate":  round(r[2] / r[1] * 100, 1) if r[1] else None,
         "avg_pnl":   round(r[3], 2)}
        for r in direction_rows
    ]

    # By exit reason
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
    gate_buckets       = get_threshold_performance(conn, "gate_score",
                         [(0,5),(5,7),(7,8),(8,9),(9,12)])
    sscore_buckets     = get_threshold_performance(conn, "s_score",
                         [(0,8),(8,12),(12,16),(16,100)])

    # Rejection reason breakdown
    reject_rows = conn.execute(
        """SELECT rejection_reason, COUNT(*)
           FROM trades WHERE status='REJECTED'
           GROUP BY rejection_reason ORDER BY COUNT(*) DESC"""
    ).fetchall()
    rejection_analysis = [{"reason": r[0], "count": r[1]} for r in reject_rows]

    # Shadow comparison
    shadows        = get_shadow_history(conn, days=days)
    shadow_wins    = sum(1 for s in shadows if (s.get("final_pnl_pct") or 0) > 0)
    shadow_total   = len(shadows)
    shadow_avg_pnl = round(
        sum(s.get("final_pnl_pct") or 0 for s in shadows) / shadow_total, 2
    ) if shadow_total else 0
    shadow_outcomes = {}
    for s in shadows:
        o = s.get("outcome", "UNKNOWN")
        shadow_outcomes[o] = shadow_outcomes.get(o, 0) + 1

    return {
        "overall":              overall,
        "by_underlying":        by_underlying,
        "by_direction":         by_direction,
        "by_exit_reason":       by_exit,
        "confidence_buckets":   confidence_buckets,
        "gate_buckets":         gate_buckets,
        "sscore_buckets":       sscore_buckets,
        "rejection_analysis":   rejection_analysis,
        "shadow_summary": {
            "total":     shadow_total,
            "wins":      shadow_wins,
            "win_rate":  round(shadow_wins / shadow_total * 100, 1) if shadow_total else None,
            "avg_pnl":   shadow_avg_pnl,
            "outcomes":  shadow_outcomes,
        },
    }
