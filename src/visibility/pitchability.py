"""Pitchability scoring: who to pitch first.

Adapted from geo-slab's scripts/score_prospects.py (github.com/Nipstar/geo-slab).
Combines the visibility gap (an invisible business is the best pitch), the
business's review signal, and how contactable it is, into a 0-100 score and a
tier. Used to order the daily queue and batch drafting so the best leads move
first.

    pitchability = 0.40 * geo_gap
                 + 0.25 * opportunity      (review-signal proxy, 0-100)
                 + 0.20 * business_signal
                 + 0.15 * contactability

    geo_gap         = min(100 - composite_visibility, 80)
    business_signal = review_bucket * rating_factor
    contactability  = phone(50) + website(50)
"""
from __future__ import annotations

from .. import db

WEIGHTS = {
    "geo_gap": 0.40,
    "opportunity": 0.25,
    "business_signal": 0.20,
    "contactability": 0.15,
}
REVIEW_BUCKETS = [(200, 100), (50, 70), (10, 40), (0, 20)]
REVIEW_MISSING_DEFAULT = 30
GEO_GAP_CAP = 80


def _review_bucket(review_count) -> int:
    if review_count in (None, ""):
        return REVIEW_MISSING_DEFAULT
    try:
        n = int(float(review_count))
    except (ValueError, TypeError):
        return REVIEW_MISSING_DEFAULT
    for threshold, score in REVIEW_BUCKETS:
        if n >= threshold:
            return score
    return REVIEW_MISSING_DEFAULT


def _rating_factor(rating) -> float:
    try:
        r = float(rating)
    except (ValueError, TypeError):
        return 0.8
    if r <= 0:
        return 0.8
    return max(0.0, min(1.0, r / 5.0))


def score_row(company, composite_visibility: float | None) -> dict:
    """Compute pitchability for one company + its latest visibility score."""
    if composite_visibility is None:
        # No check yet: score on business signal + contactability only, no gap.
        geo_gap = 0.0
    else:
        geo_gap = max(0.0, min(100.0 - composite_visibility, GEO_GAP_CAP))

    bucket = _review_bucket(company["places_reviews"])
    business_signal = round(bucket * _rating_factor(company["places_rating"]), 1)
    # Review count as a rough opportunity/demand proxy, capped at 100.
    try:
        reviews = int(float(company["places_reviews"] or 0))
    except (ValueError, TypeError):
        reviews = 0
    opportunity = float(min(reviews, 100))

    has_phone = bool((company["phone"] or "").strip())
    has_website = bool((company["website"] or "").strip())
    contactability = (50 if has_phone else 0) + (50 if has_website else 0)

    pitch = round(
        WEIGHTS["geo_gap"] * geo_gap
        + WEIGHTS["opportunity"] * opportunity
        + WEIGHTS["business_signal"] * business_signal
        + WEIGHTS["contactability"] * contactability,
        1,
    )
    if pitch >= 70:
        tier = "premium"
    elif pitch >= 50:
        tier = "standard"
    else:
        tier = "skip"
    return {"pitchability_score": pitch, "pitchability_tier": tier,
            "geo_gap": geo_gap, "business_signal": business_signal,
            "contactability": contactability}


def score_company(conn, company) -> dict:
    check = db.latest_check(conn, company["id"])
    composite = check["composite_score"] if check else None
    result = score_row(company, composite)
    db.update_company(
        conn, company["id"],
        pitchability_score=result["pitchability_score"],
        pitchability_tier=result["pitchability_tier"],
    )
    return result


def rescore_all(limit: int | None = None) -> dict:
    """Recompute pitchability for all live companies. Returns a tier tally."""
    conn = db.get_connection()
    tally = {"premium": 0, "standard": 0, "skip": 0}
    try:
        q = "SELECT * FROM companies WHERE status NOT IN ('closed_lost','client')"
        if limit:
            q += f" LIMIT {int(limit)}"
        for company in conn.execute(q).fetchall():
            res = score_company(conn, company)
            tally[res["pitchability_tier"]] += 1
    finally:
        conn.close()
    return tally
