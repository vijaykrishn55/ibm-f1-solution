# fast_path/rules_engine.py
# ─────────────────────────────────────────────────────────────────────────────
# RulesEngine — Person B (Task B5)
#
# Takes an enriched state vector + circuit config.
# Evaluates all rules, returns the highest-priority alert dict.
#
# FAST PATH — this must complete in < 100ms total (typically < 5ms here).
# Never call Granite, MPC, or any async service from inside this file.
#
# Usage:
#   engine = RulesEngine(circuit_config)
#   alert = engine.evaluate(state_vector)
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import json
import os
import time
from uuid import uuid4


# ── Default circuit config (used if no JSON loaded) ──────────────────────────

DEFAULT_CONFIG = {
    "circuit":               "bahrain",
    "corners":               15,
    "boost_zone_corners":    [11, 12],
    "soc_danger_threshold":  0.25,
    "corner_thresholds": {
        "1":  {"net_lift_value": -0.02, "lift_energy_gain": 0.12, "lift_aero_cost_s": 0.14},
        "4":  {"net_lift_value":  0.08, "lift_energy_gain": 0.18, "lift_aero_cost_s": 0.10},
        "10": {"net_lift_value":  0.15, "lift_energy_gain": 0.22, "lift_aero_cost_s": 0.07},
        "11": {"net_lift_value":  0.03, "lift_energy_gain": 0.09, "lift_aero_cost_s": 0.06},
        "14": {"net_lift_value":  0.11, "lift_energy_gain": 0.19, "lift_aero_cost_s": 0.08},
    },
}

SAFE_DEFAULT = {
    "rule":           "safe_default",
    "recommendation": "Maintain current mode",
    "reason":         "No triggering condition detected",
    "priority":       1,
}


class RulesEngine:
    """
    Deterministic, synchronous rules engine.

    All rules are evaluated in priority order.
    Returns the highest-priority rule that fires (or safe_default).
    """

    def __init__(self, config: dict | None = None, config_path: str | None = None):
        if config_path and os.path.exists(config_path):
            with open(config_path) as f:
                self.config = json.load(f)
        elif config:
            self.config = config
        else:
            self.config = DEFAULT_CONFIG

        # Slow-path writes here; rules engine reads on next tick
        self.mpc_recommendations: dict[int, float] = {}  # {corner_id: lift_fraction}

    # ── Rule evaluation ──────────────────────────────────────────────────────

    def _rule_stale_data(self, s: dict) -> dict | None:
        """
        Priority 10 — stale data overrides everything.
        If data_age_ms > 2000 we cannot trust ANY reading.
        """
        if s.get("data_age_ms", 0) > 2000:
            return {
                "rule":           "stale_data_fallback",
                "recommendation": "Data too old — safe mode active",
                "reason":         f"data_age_ms={s['data_age_ms']} > 2000ms",
                "priority":       10,
            }
        return None

    def _rule_safety_car_recharge(self, s: dict) -> dict | None:
        """
        Priority 10 — free recovery window under safety/virtual safety car.
        Highest value recharge window of the entire race.
        """
        if (
            s.get("session_flag") in ("sc", "vsc")
            and s.get("soc_estimated", 1.0) < 0.9
        ):
            return {
                "rule":           "safety_car_recharge",
                "recommendation": "Recharge aggressively — free recovery window",
                "reason":         (
                    f"session_flag={s['session_flag']}, "
                    f"soc={s['soc_estimated']:.3f} — lift to max recovery"
                ),
                "priority":       10,
            }
        return None

    def _rule_soc_danger_alert(self, s: dict) -> dict | None:
        """
        Priority 9 — SOC critically low approaching a boost zone.
        """
        threshold = self.config.get("soc_danger_threshold", 0.25)
        boost_corners = self.config.get("boost_zone_corners", [])
        corner_id = s.get("corner_id", 0)

        if (
            s.get("soc_estimated", 1.0) < threshold
            and corner_id in boost_corners
            and s.get("session_flag") == "green"
        ):
            corners_to_boost = min(boost_corners) - corner_id
            return {
                "rule":           "soc_danger_alert",
                "recommendation": (
                    f"Recharge immediately — boost zone in {max(0, corners_to_boost)} corners"
                ),
                "reason":         (
                    f"soc={s['soc_estimated']:.3f} < danger_threshold={threshold}, "
                    f"corner_id={corner_id} is in boost zone"
                ),
                "priority":       9,
            }
        return None

    def _rule_cusum_soc_alarm(self, s: dict) -> dict | None:
        """
        Priority 8 — CUSUM has detected abnormal SOC depletion trend.
        """
        if s.get("cusum_soc_alarm", False):
            return {
                "rule":           "cusum_soc_alarm",
                "recommendation": "Energy drain elevated — monitor closely",
                "reason":         (
                    f"CUSUM SOC alarm fired, "
                    f"energy_delta={s.get('energy_delta', 0):.4f}"
                ),
                "priority":       8,
            }
        return None

    def _rule_lift_not_worth_it(self, s: dict) -> dict | None:
        """
        Priority 7 — driver is lifting (low throttle) but this corner has
        negative net_lift_value, meaning the aero cost exceeds energy gain.
        """
        corner_id = s.get("corner_id", 0)
        corner_cfg = self.config.get("corner_thresholds", {}).get(str(corner_id))

        if (
            s.get("throttle", 1.0) < 0.25
            and corner_cfg is not None
            and corner_cfg.get("net_lift_value", 0) < 0
        ):
            return {
                "rule":           "lift_not_worth_it",
                "recommendation": "Remove lift — energy cost exceeds aero gain",
                "reason":         (
                    f"throttle={s['throttle']:.2f} < 0.25, "
                    f"net_lift_value={corner_cfg['net_lift_value']:.3f} at corner {corner_id}"
                ),
                "priority":       7,
            }
        return None

    def _rule_optimal_recharge_window(self, s: dict) -> dict | None:
        """
        Priority 6 — this corner has high net_lift_value AND SOC is below
        the recharge target AND car is on throttle (energy to harvest).
        """
        corner_id = s.get("corner_id", 0)
        corner_cfg = self.config.get("corner_thresholds", {}).get(str(corner_id))

        if (
            corner_cfg is not None
            and corner_cfg.get("net_lift_value", 0) > 0.05
            and s.get("soc_estimated", 1.0) < 0.6
            and s.get("session_flag") == "green"
            and s.get("throttle", 0.0) > 0.7
        ):
            lift_val = corner_cfg["net_lift_value"]
            return {
                "rule":           "optimal_recharge_window",
                "recommendation": "Lift here — net energy gain worth aero trade",
                "reason":         (
                    f"net_lift_value={lift_val:.3f} > 0.05, "
                    f"soc={s['soc_estimated']:.3f} < 0.6, "
                    f"corner {corner_id}"
                ),
                "priority":       6,
            }
        return None

    # ── Ordered rule list (highest to lowest priority) ───────────────────────

    _RULES = [
    _rule_safety_car_recharge,
    _rule_soc_danger_alert,
    _rule_cusum_soc_alarm,
    _rule_stale_data,
    _rule_lift_not_worth_it,
    _rule_optimal_recharge_window,
]

    # ── Public API ───────────────────────────────────────────────────────────

    def evaluate(self, state: dict) -> dict:
        """
        Evaluate all rules against the state vector.

        Returns the highest-priority alert dict.
        Always returns something — at minimum safe_default.
        Adds alert_id, soc_estimated, corner_id, lap, timestamp, fan_explanation.
        """
        t_start = time.perf_counter()

        fired = []
        for rule_fn in self._RULES:
            result = rule_fn(self, state)
            if result is not None:
                fired.append(result)

        # Pick highest priority (or safe_default)
        if fired:
            alert = max(fired, key=lambda r: r["priority"])
        else:
            alert = dict(SAFE_DEFAULT)

        # Enrich with metadata
        alert["alert_id"]      = str(uuid4())
        alert["soc_estimated"] = state.get("soc_estimated", 0.0)
        alert["corner_id"]     = state.get("corner_id", 0)
        alert["lap"]           = state.get("lap", 0)
        alert["timestamp"]     = state.get("timestamp", time.time())
        alert["fan_explanation"] = ""   # Granite slow path fills this
        alert["source_module"] = "voltedge"

        latency_ms = (time.perf_counter() - t_start) * 1000
        alert["_rules_latency_ms"] = round(latency_ms, 3)

        return alert

    def update_mpc_recommendations(self, recs: dict[int, float]):
        """Called by slow path to update MPC-derived lift fractions."""
        self.mpc_recommendations.update(recs)


# ── Quick standalone test ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

    from tests.mock_state_vectors import (
        NORMAL, SOC_DANGER, LIFT_NOT_WORTH, GOOD_RECHARGE,
        SAFETY_CAR, STALE_DATA, TORCS_STATE,
    )

    engine = RulesEngine()

    test_cases = [
        ("NORMAL",        NORMAL,       "safe_default"),
        ("SOC_DANGER",    SOC_DANGER,   "soc_danger_alert"),
        ("LIFT_NOT_WORTH",LIFT_NOT_WORTH,"lift_not_worth_it"),
        ("GOOD_RECHARGE", GOOD_RECHARGE, "optimal_recharge_window"),
        ("SAFETY_CAR",    SAFETY_CAR,   "safety_car_recharge"),
        ("STALE_DATA",    STALE_DATA,   "stale_data_fallback"),
        ("TORCS_STATE",   TORCS_STATE,  "safe_default"),
    ]

    print("RulesEngine — rule firing test")
    print("─" * 60)
    all_pass = True
    for name, state, expected_rule in test_cases:
        alert = engine.evaluate(state)
        fired = alert["rule"]
        ok = "✓" if fired == expected_rule else "✗"
        if fired != expected_rule:
            all_pass = False
        print(f"  {ok} {name:<18} fired={fired:<28} expected={expected_rule}")
        if fired != expected_rule:
            print(f"       reason: {alert['reason']}")

    print("\n" + ("✓ All rules passed" if all_pass else "✗ Some rules failed"))
