# tests/test_person_b_day1.py
# ─────────────────────────────────────────────────────────────────────────────
# Person B — Day 1 Checkpoint Tests
#
# Run with:  pytest tests/test_person_b_day1.py -v
#
# Must pass before merging into main at end of Day 1.
# ─────────────────────────────────────────────────────────────────────────────

import copy
import time
import pytest

from state.schema        import new_state, validate_state, DEFAULT_STATE
from state.kalman        import BatterySOCEstimator
from state.window        import CornerWindow
from fast_path.cusum     import CUSUMDetector, cusum_soc, cusum_speed
from fast_path.rules_engine import RulesEngine
from fast_path.confidence   import ConfidenceScorer

from tests.mock_state_vectors import (
    NORMAL, SOC_DANGER, LIFT_NOT_WORTH, GOOD_RECHARGE,
    SAFETY_CAR, STALE_DATA, TORCS_STATE, CUSUM_ALARM,
    ALL_SCENARIOS,
)


# ── schema.py ────────────────────────────────────────────────────────────────

class TestSchema:
    def test_new_state_has_all_fields(self):
        s = new_state()
        for key in DEFAULT_STATE:
            assert key in s, f"Missing field: {key}"

    def test_new_state_timestamp_is_recent(self):
        s = new_state()
        assert abs(s["timestamp"] - time.time()) < 1.0

    def test_new_state_override(self):
        s = new_state(driver="HAM", lap=5, data_source="torcs")
        assert s["driver"] == "HAM"
        assert s["lap"] == 5
        assert s["data_source"] == "torcs"

    def test_validate_catches_missing_field(self):
        s = new_state()
        del s["soc_estimated"]
        warnings = validate_state(s)
        assert any("soc_estimated" in w for w in warnings)

    def test_validate_catches_out_of_range_soc(self):
        s = new_state(soc_estimated=1.5)
        warnings = validate_state(s)
        assert any("soc_estimated" in w for w in warnings)

    def test_validate_ok_for_valid_state(self):
        s = new_state(driver="VER", soc_estimated=0.7, throttle=0.9)
        assert validate_state(s) == []


# ── kalman.py ────────────────────────────────────────────────────────────────

class TestKalman:
    def test_soc_written_to_state(self):
        estimator = BatterySOCEstimator()
        state = copy.deepcopy(NORMAL)
        soc, unc = estimator.update(state)
        assert "soc_estimated"  in state
        assert "soc_uncertainty" in state
        assert 0.0 <= soc <= 1.0
        assert unc >= 0.0

    def test_filtered_smoother_than_raw(self):
        """Kalman output should have smaller variance than raw proxy."""
        estimator = BatterySOCEstimator()
        import random, statistics
        rng = random.Random(0)
        raws, filtered = [], []

        for i in range(100):
            state = copy.deepcopy(NORMAL)
            state["throttle"] = 0.9 + rng.gauss(0, 0.05)
            state["brake"]    = False
            state["drs"]      = False
            state["data_source"] = "mock"
            state["soc_raw"] = 0.0
            soc, _ = estimator.update(state)
            raws.append(state["soc_raw"])
            filtered.append(soc)

        raw_var  = statistics.variance(raws)
        filt_var = statistics.variance(filtered)
        assert filt_var < raw_var, (
            f"Filtered variance {filt_var:.6f} should be < raw {raw_var:.6f}"
        )

    def test_torcs_uses_soc_raw_directly(self):
        estimator = BatterySOCEstimator()
        state = copy.deepcopy(TORCS_STATE)
        state["soc_raw"] = 0.61
        estimator.update(state)
        # TORCS path: proxy = soc_raw
        assert state["soc_raw"] == pytest.approx(0.61, abs=0.01)

    def test_reset(self):
        estimator = BatterySOCEstimator()
        for _ in range(20):
            estimator.update(copy.deepcopy(NORMAL))
        estimator.reset(soc=0.85)
        state = copy.deepcopy(NORMAL)
        soc, _ = estimator.update(state)
        assert 0.80 <= soc <= 0.95


# ── window.py ────────────────────────────────────────────────────────────────

class TestWindow:
    def test_push_and_mean_soc(self):
        w = CornerWindow()
        w.push(corner_id=11, soc=0.40, speed=280)
        w.push(corner_id=11, soc=0.50, speed=285)
        assert w.mean_soc(11) == pytest.approx(0.45)

    def test_soc_trend_negative(self):
        w = CornerWindow()
        w.push(4, 0.70, 220)
        w.push(4, 0.65, 215)
        w.push(4, 0.60, 210)
        assert w.soc_trend(4) < 0

    def test_soc_trend_positive(self):
        w = CornerWindow()
        w.push(10, 0.55, 200)
        w.push(10, 0.58, 202)
        w.push(10, 0.61, 205)
        assert w.soc_trend(10) > 0

    def test_empty_corner_returns_zero(self):
        w = CornerWindow()
        assert w.mean_soc(99) == 0.0
        assert w.soc_trend(99) == 0.0

    def test_maxlen_respected(self):
        w = CornerWindow(maxlen=3)
        for i in range(10):
            w.push(1, 0.5 + i * 0.01, 200)
        assert w.window_size(1) == 3


# ── cusum.py ─────────────────────────────────────────────────────────────────

class TestCUSUM:
    def test_alarm_fires_on_excess_drain(self):
        d = CUSUMDetector(expected_value=-0.003, threshold=0.015)
        alarms = [d.update(-0.008) for _ in range(10)]
        assert any(alarms), "Alarm should fire during excess drain"

    def test_no_alarm_on_normal_drain(self):
        d = CUSUMDetector(expected_value=-0.003, threshold=0.015)
        alarms = [d.update(-0.003) for _ in range(20)]
        assert not any(alarms), "No alarm on normal drain"

    def test_reset_clears_cumsum(self):
        d = CUSUMDetector(expected_value=-0.003, threshold=0.015)
        for _ in range(4):
            d.update(-0.008)   # build up cumsum
        d.reset()
        assert d.cumsum == 0.0

    def test_alarm_count_increments(self):
        d = CUSUMDetector(expected_value=-0.003, threshold=0.015)
        for _ in range(20):
            d.update(-0.010)
        assert d.alarm_count >= 2


# ── rules_engine.py ───────────────────────────────────────────────────────────

class TestRulesEngine:
    @pytest.fixture
    def engine(self):
        return RulesEngine()

    def test_safe_default_on_normal(self, engine):
        alert = engine.evaluate(NORMAL)
        assert alert["rule"] == "safe_default"

    def test_soc_danger_alert(self, engine):
        alert = engine.evaluate(SOC_DANGER)
        assert alert["rule"] == "soc_danger_alert"
        assert alert["priority"] == 9

    def test_safety_car_recharge_priority_10(self, engine):
        alert = engine.evaluate(SAFETY_CAR)
        assert alert["rule"] == "safety_car_recharge"
        assert alert["priority"] == 10

    def test_stale_data_fallback(self, engine):
        alert = engine.evaluate(STALE_DATA)
        assert alert["rule"] == "stale_data_fallback"

    def test_lift_not_worth_it(self, engine):
        alert = engine.evaluate(LIFT_NOT_WORTH)
        assert alert["rule"] == "lift_not_worth_it"

    def test_optimal_recharge_window(self, engine):
        alert = engine.evaluate(GOOD_RECHARGE)
        assert alert["rule"] == "optimal_recharge_window"

    def test_cusum_alarm_fires(self, engine):
        alert = engine.evaluate(CUSUM_ALARM)
        assert alert["rule"] == "cusum_soc_alarm"
        assert alert["priority"] == 8

    def test_alert_always_has_required_fields(self, engine):
        for name, state in ALL_SCENARIOS:
            alert = engine.evaluate(state)
            for field in ("alert_id", "rule", "recommendation",
                          "reason", "priority", "confidence_base" if "confidence_base" in alert else "rule"):
                assert "alert_id"       in alert, f"{name}: missing alert_id"
                assert "rule"           in alert, f"{name}: missing rule"
                assert "recommendation" in alert, f"{name}: missing recommendation"
                assert "priority"       in alert, f"{name}: missing priority"

    def test_latency_under_100ms(self, engine):
        import time
        for _, state in ALL_SCENARIOS:
            t0 = time.perf_counter()
            engine.evaluate(state)
            elapsed = (time.perf_counter() - t0) * 1000
            assert elapsed < 100, f"Rule evaluation took {elapsed:.1f}ms > 100ms SLO"


# ── confidence.py ─────────────────────────────────────────────────────────────

class TestConfidence:
    @pytest.fixture
    def scorer(self):
        return ConfidenceScorer()

    @pytest.fixture
    def base_alert(self):
        from fast_path.rules_engine import RulesEngine
        return RulesEngine().evaluate(SOC_DANGER)

    def test_high_confidence_fresh_data_faiss_consensus(self, scorer, base_alert):
        faiss = [{"outcome": "warning_fired"}, {"outcome": "warning_fired"},
                 {"outcome": "warning_fired"}]
        result = scorer.score(base_alert, SOC_DANGER, faiss)
        assert result["confidence"] >= 0.80

    def test_penalty_stale_data(self, scorer, base_alert):
        fresh   = scorer.score(base_alert, {**SOC_DANGER, "data_age_ms": 80}, None)
        stale   = scorer.score(base_alert, {**SOC_DANGER, "data_age_ms": 800}, None)
        assert stale["confidence"] < fresh["confidence"]

    def test_safe_fallback_on_very_stale(self, scorer, base_alert):
        very_stale = {**SOC_DANGER, "data_age_ms": 2500}
        result = scorer.score(base_alert, very_stale, None)
        assert result["rule"] == "safe_default"

    def test_output_has_all_required_fields(self, scorer, base_alert):
        result = scorer.score(base_alert, SOC_DANGER, None)
        required = ["alert_id", "rule", "recommendation", "reason",
                    "priority", "confidence", "soc_estimated",
                    "corner_id", "lap", "timestamp", "fan_explanation"]
        for f in required:
            assert f in result, f"Missing field: {f}"

    def test_confidence_between_0_and_1(self, scorer):
        engine = RulesEngine()
        for _, state in ALL_SCENARIOS:
            alert  = engine.evaluate(state)
            result = scorer.score(alert, state, None)
            assert 0.0 <= result["confidence"] <= 1.0, (
                f"Confidence out of range for {state.get('data_source')}: {result['confidence']}"
            )
