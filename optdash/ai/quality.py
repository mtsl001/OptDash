"""Trade quality score — advisory composite, range 0–100.

Three components:
  C1: Strike quality  (S_score / 20, capped at 1.0)            — max 35
  C2: Gate adequacy   (gate_score / GATE_MAX_SCORE)             — max 35
  C3: Confidence      (confidence / 100)                        — max 30

Grades: A ≥ 80 | B ≥ 65 | C ≥ 50 | D < 50
"""
from optdash.config import settings


def compute_quality_score(strike: dict, gate_score: int, confidence: int) -> dict:
    # C1: S_score normalised to 20 (99th-percentile typical range)
    sscore_norm = min(1.0, (strike.get("s_score") or 0) / 20)
    c1 = sscore_norm * 35

    # C2: Gate adequacy — guard against misconfigured GATE_MAX_SCORE=0
    gate_max = settings.GATE_MAX_SCORE or 10
    c2 = min(35, (gate_score / gate_max) * 35)

    # C3: Confidence adequacy
    c3 = min(30, (confidence / 100) * 30)

    quality = int(c1 + c2 + c3)
    grade = (
        "A" if quality >= 80 else
        "B" if quality >= 65 else
        "C" if quality >= 50 else
        "D"
    )
    return {"quality_score": quality, "grade": grade}
