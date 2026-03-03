"""Trade quality score — advisory composite, range 0-100."""
from optdash.config import settings


def compute_quality_score(strike: dict, gate_score: int, confidence: int) -> dict:
    """
    Component 1: Strike quality  (S_score normalized) — max 35
    Component 2: Gate adequacy                        — max 35
    Component 3: Confidence adequacy                  — max 30
    """
    sscore_norm = min(1.0, (strike.get("s_score") or 0) / 20)
    c1 = sscore_norm * 35
    c2 = min(35, (gate_score / settings.GATE_MAX_SCORE) * 35)
    c3 = min(30, (confidence / 100) * 30)

    quality = int(c1 + c2 + c3)
    grade = (
        "A" if quality >= 80 else
        "B" if quality >= 65 else
        "C" if quality >= 50 else
        "D"
    )
    return {"quality_score": quality, "grade": grade}
