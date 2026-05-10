"""Confidence scoring helpers (placeholder)"""

def score(recommendation, priority=1, faiss_agreement=False, data_age_ms=0, kalman_uncertainty=0.0, cusum_reset=False):
    score = 0.2 + (priority / 10.0) * 0.6
    if faiss_agreement:
        score += 0.15
    if data_age_ms > 500:
        score -= 0.2
    if cusum_reset:
        score -= 0.1
    if kalman_uncertainty > 0.1:
        score -= 0.1
    return max(0.0, min(1.0, score))
