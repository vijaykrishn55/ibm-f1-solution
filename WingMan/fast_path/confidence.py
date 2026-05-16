# fast_path/confidence.py
# ─────────────────────────────────────────────────────────────────────────────
# ConfidenceScorer — Person B (Task B6)
#
# Takes a raw alert dict from the rules engine, computes a confidence score,
# and optionally overrides the recommendation with safe_default if confidence
# is too low to trust.
#
# Also assembles the FINAL alert dict that Person C's alert_builder consumes.
#
# Usage:
#   scorer = ConfidenceScorer()
#   final_alert = scorer.score(raw_alert, state, faiss_matches)
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations
from uuid import uuid4
import time

def score(
    self,
    alert:         dict,
    state:         dict,
    faiss_matches: list[dict] | None = None,
) -> dict:
    t_start = time.perf_counter()        # ← ADD THIS LINE
    
    priority = alert.get("priority", 1)
    # ... rest of your existing code unchanged ...
    
# ── Base scores by priority ───────────────────────────────────────────────────

PRIORITY_BASE_SCORE: dict[int, float] = {
    10: 0.85,
    9:  0.80,
    8:  0.75,
    7:  0.70,
    6:  0.65,
    1:  0.50,
}

SAFE_DEFAULT_RULE = "safe_default"
SAFE_DEFAULT_REC  = "Maintain current mode"
SAFE_DEFAULT_REASON = "Confidence below threshold — falling back to safe mode"
CONFIDENCE_FLOOR  = 0.60    # Below this → override with safe_default


class ConfidenceScorer:
    """
    Computes a confidence score for a rules engine alert.

    Adjustments (additive):
      +0.10  FAISS top-3 all agree on the same outcome
      -0.15  data_age_ms > 500 ms
      -0.10  soc_uncertainty > 0.10
      -0.20  data_age_ms > 2000 ms  (overrides the -0.15 above)

    If final_score < CONFIDENCE_FLOOR:
        → override recommendation with safe_default
    """

    def score(
        self,
        alert:         dict,
        state:         dict,
        faiss_matches: list[dict] | None = None,
    ) -> dict:
        """
        Args:
            alert:         Raw alert dict from RulesEngine.evaluate()
            state:         Current enriched state vector
            faiss_matches: Top-3 FAISS results (list of dicts with 'outcome' key)
                           Pass None or [] if FAISS is not ready yet.

        Returns:
            Final alert dict — ready for Person C's alert_builder.
        """
        priority = alert.get("priority", 1)
        base     = PRIORITY_BASE_SCORE.get(priority, 0.50)
        adj      = 0.0

        data_age       = state.get("data_age_ms",    0)
        soc_uncertainty = state.get("soc_uncertainty", 0.0)

        # ── Adjustments ─────────────────────────────────────────────────────
        # FAISS consensus bonus
        if faiss_matches and len(faiss_matches) >= 3:
            outcomes = [m.get("outcome") for m in faiss_matches[:3]]
            if len(set(outcomes)) == 1:   # all three agree
                adj += 0.10

        # Data staleness penalty
        if data_age > 2000:
            adj -= 0.20
        elif data_age > 500:
            adj -= 0.15

        # SOC uncertainty penalty
        if soc_uncertainty > 0.10:
            adj -= 0.10

        final_score = round(max(0.0, min(1.0, base + adj)), 4)

        # ── Safe fallback override ───────────────────────────────────────────
        if final_score < CONFIDENCE_FLOOR:
            rule           = SAFE_DEFAULT_RULE
            recommendation = SAFE_DEFAULT_REC
            reason         = (
                f"{SAFE_DEFAULT_REASON} "
                f"(original rule={alert.get('rule')}, "
                f"score={final_score:.3f} < {CONFIDENCE_FLOOR})"
            )
            priority = 1
        else:
            rule           = alert["rule"]
            recommendation = alert["recommendation"]
            reason         = alert["reason"]

        return {
            "alert_id":       alert.get("alert_id", str(uuid4())),
            "rule":           rule,
            "recommendation": recommendation,
            "reason":         reason,
            "priority":       priority,
            "confidence":     final_score,
            "soc_estimated":  state.get("soc_estimated", 0.0),
            "corner_id":      state.get("corner_id",     0),
            "lap":            state.get("lap",           0),
            "timestamp":      state.get("timestamp",     time.time()),
            "fan_explanation": alert.get("fan_explanation", ""),  # Granite fills later
            "source_module":  alert.get("source_module", "voltedge"),
            "_confidence_base": base,
            "_confidence_adj":  round(adj, 4),
        }


# ── Quick standalone test ─────────────────────────────────────────────────────

if __name__ == "__main__":
    scorer = ConfidenceScorer()

    print("ConfidenceScorer — scoring test")
    print("─" * 60)

    base_state = {
        "soc_estimated":   0.22,
        "soc_uncertainty": 0.03,
        "data_age_ms":     80,
        "corner_id":       11,
        "lap":             15,
        "timestamp":       time.time(),
    }

    base_alert = {
        "alert_id":       str(uuid4()),
        "rule":           "soc_danger_alert",
        "recommendation": "Recharge immediately",
        "reason":         "SOC below threshold in boost zone",
        "priority":       9,
        "fan_explanation": "",
        "source_module":  "voltedge",
    }

    # Test 1: High confidence — good FAISS consensus
    faiss_ok = [
        {"outcome": "warning_fired"}, {"outcome": "warning_fired"}, {"outcome": "warning_fired"}
    ]
    result = scorer.score(base_alert, base_state, faiss_ok)
    print(f"\nTest 1 — fresh data, FAISS consensus:")
    print(f"  score={result['confidence']}  rule={result['rule']}")
    print(f"  base={result['_confidence_base']}  adj={result['_confidence_adj']}")
    assert result["confidence"] >= 0.85, "Expected high confidence"

    # Test 2: Stale data (>500ms) — penalty applied
    stale_state = {**base_state, "data_age_ms": 800}
    result2 = scorer.score(base_alert, stale_state, faiss_ok)
    print(f"\nTest 2 — stale data (800ms):")
    print(f"  score={result2['confidence']}  adj={result2['_confidence_adj']}")
    assert result2["confidence"] < result["confidence"], "Expected lower score for stale data"

    # Test 3: Very stale data (>2000ms) — safe fallback override
    very_stale = {**base_state, "data_age_ms": 2500}
    result3 = scorer.score(base_alert, very_stale, None)
    print(f"\nTest 3 — very stale data (2500ms) → safe fallback:")
    print(f"  score={result3['confidence']}  rule={result3['rule']}")
    assert result3["rule"] == SAFE_DEFAULT_RULE, "Expected safe_default override"

    # Test 4: High SOC uncertainty
    uncertain = {**base_state, "soc_uncertainty": 0.15}
    result4 = scorer.score(base_alert, uncertain, None)
    print(f"\nTest 4 — high SOC uncertainty (0.15):")
    print(f"  score={result4['confidence']}  adj={result4['_confidence_adj']}")

    print("\n✓  All confidence tests passed")
def score(
    self,
    alert:         dict,
    state:         dict,
    faiss_matches: list[dict] | None = None,
) -> dict:
    t_start = time.perf_counter()        # ← ADD THIS (line 1)

    # ... all your existing code stays exactly the same ...

    return {
        "alert_id":       alert.get("alert_id", str(uuid4())),
        "rule":           rule,
        "recommendation": recommendation,
        "reason":         reason,
        "priority":       priority,
        "confidence":     final_score,
        "soc_estimated":  state.get("soc_estimated", 0.0),
        "corner_id":      state.get("corner_id",     0),
        "lap":            state.get("lap",           0),
        "timestamp":      state.get("timestamp",     time.time()),
        "fan_explanation": alert.get("fan_explanation", ""),
        "source_module":  alert.get("source_module", "voltedge"),
        "_confidence_base": base,
        "_confidence_adj":  round(adj, 4),
        "processing_ms":  round((time.perf_counter() - t_start) * 1000, 2),  # ← ADD THIS (line 2)
    }