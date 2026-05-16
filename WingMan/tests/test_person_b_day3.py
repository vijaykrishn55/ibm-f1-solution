# tests/test_person_b_day3.py
# Person B — Day 3 edge case tests
# Run: python -m pytest tests/test_person_b_day3.py -v

import time
import json
import pytest
from unittest.mock import patch

from state.schema import new_state
from state.kalman import BatterySOCEstimator
from state.window import CornerWindow
from fast_path.cusum import CUSUMDetector
from fast_path.rules_engine import RulesEngine
from fast_path.confidence import ConfidenceScorer
from fast_path.faiss_index import FAISSIndex

# ──────────────────────────────────────────────
# Shared fixtures
# ──────────────────────────────────────────────

CIRCUIT_CONFIG = {
    "circuit": "bahrain",
    "corners": 15,
    "boost_zone_corners": [11, 12],
    "soc_danger_threshold": 0.25,
    "corner_thresholds": {
        "1":  {"net_lift_value": -0.02, "lift_energy_gain": 0.12, "lift_aero_cost_s": 0.14},
        "4":  {"net_lift_value":  0.08, "lift_energy_gain": 0.18, "lift_aero_cost_s": 0.10},
        "10": {"net_lift_value":  0.15, "lift_energy_gain": 0.22, "lift_aero_cost_s": 0.07},
        "11": {"net_lift_value":  0.03, "lift_energy_gain": 0.09, "lift_aero_cost_s": 0.06},
        "14": {"net_lift_value":  0.11, "lift_energy_gain": 0.19, "lift_aero_cost_s": 0.08},
    },
}

BASE = new_state(
    driver="VER",
    lap=31,
    corner_id=11,
    lap_fraction=0.55,
    speed=285.0,
    throttle=0.92,
    brake=False,
    drs=True,
    aero_state="straight_mode",
    soc_raw=0.72,
    soc_estimated=0.72,
    soc_uncertainty=0.03,
    energy_delta=-0.003,
    gap_ahead=1.4,
    session_flag="green",
    data_age_ms=80,
    data_source="mock",
)


# ──────────────────────────────────────────────
# Edge Case 1 — SOC near zero at boost zone
# ──────────────────────────────────────────────

class TestSOCNearZero:

    def test_soc_danger_fires_at_near_zero(self):
        sv = {**BASE, "soc_estimated": 0.04, "corner_id": 11, "session_flag": "green"}
        engine = RulesEngine(CIRCUIT_CONFIG)
        alert = engine.evaluate(sv)
        assert alert["rule"] == "soc_danger_alert", (
            f"Expected soc_danger_alert, got {alert['rule']}"
        )

    def test_soc_danger_priority_is_9(self):
        sv = {**BASE, "soc_estimated": 0.04, "corner_id": 11, "session_flag": "green"}
        engine = RulesEngine(CIRCUIT_CONFIG)
        alert = engine.evaluate(sv)
        assert alert["priority"] == 9

    def test_soc_danger_recommendation_mentions_recharge(self):
        sv = {**BASE, "soc_estimated": 0.04, "corner_id": 11, "session_flag": "green"}
        engine = RulesEngine(CIRCUIT_CONFIG)
        alert = engine.evaluate(sv)
        assert "recharge" in alert["recommendation"].lower()

    def test_soc_danger_does_not_fire_outside_boost_zone(self):
        # SOC is danger-level but corner_id 3 is NOT a boost zone
        sv = {**BASE, "soc_estimated": 0.04, "corner_id": 3, "session_flag": "green"}
        engine = RulesEngine(CIRCUIT_CONFIG)
        alert = engine.evaluate(sv)
        assert alert["rule"] != "soc_danger_alert"

    def test_soc_danger_does_not_fire_under_safety_car(self):
        # Safety car should override soc_danger (safety_car_recharge is priority 10)
        sv = {**BASE, "soc_estimated": 0.04, "corner_id": 11, "session_flag": "sc"}
        engine = RulesEngine(CIRCUIT_CONFIG)
        alert = engine.evaluate(sv)
        assert alert["rule"] == "safety_car_recharge"


# ──────────────────────────────────────────────
# Edge Case 2 — Safety car sustained (5 vectors)
# ──────────────────────────────────────────────

class TestSafetyCarSustained:

    def test_safety_car_fires_every_tick(self):
        engine = RulesEngine(CIRCUIT_CONFIG)
        sc_vectors = [
            {**BASE, "session_flag": "sc", "soc_estimated": 0.65}
            for _ in range(5)
        ]
        for sv in sc_vectors:
            alert = engine.evaluate(sv)
            assert alert["rule"] == "safety_car_recharge", (
                f"Expected safety_car_recharge every tick, got {alert['rule']}"
            )

    def test_safety_car_priority_is_10(self):
        engine = RulesEngine(CIRCUIT_CONFIG)
        sv = {**BASE, "session_flag": "sc", "soc_estimated": 0.65}
        alert = engine.evaluate(sv)
        assert alert["priority"] == 10

    def test_no_lower_rule_bleeds_through_during_sc(self):
        engine = RulesEngine(CIRCUIT_CONFIG)
        # soc_danger would normally fire (soc=0.04, boost zone) but SC should win
        sv = {**BASE, "session_flag": "sc", "soc_estimated": 0.04, "corner_id": 11}
        alert = engine.evaluate(sv)
        assert alert["rule"] == "safety_car_recharge"
        assert alert["priority"] == 10

    def test_safety_car_also_fires_under_vsc(self):
        engine = RulesEngine(CIRCUIT_CONFIG)
        sv = {**BASE, "session_flag": "vsc", "soc_estimated": 0.50}
        alert = engine.evaluate(sv)
        assert alert["rule"] == "safety_car_recharge"

    def test_safety_car_does_not_fire_when_soc_is_full(self):
        # Rule: fires only when soc < 0.9
        engine = RulesEngine(CIRCUIT_CONFIG)
        sv = {**BASE, "session_flag": "sc", "soc_estimated": 0.95}
        alert = engine.evaluate(sv)
        assert alert["rule"] != "safety_car_recharge"


# ──────────────────────────────────────────────
# Edge Case 3 — Stale da