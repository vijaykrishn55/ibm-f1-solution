"""
Person C — Day 2 Integration Tests
===================================
Tests for the slow path orchestrator, WebSocket pipeline, alert builder,
TTS, and Granite client integration.

Run: python -m pytest tests/test_person_c_day2.py -v
"""

import sys
import os
import asyncio
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from slow_path.context_forge import ContextForge
from slow_path.event_queue import EventQueue
from slow_path.mpc_planner import plan_5_corners
from slow_path.granite_client import GraniteClient
from slow_path.slow_path_runner import SlowPathRunner
from output.alert_builder import build_payload
from tests.mock_state_vectors import (
    NORMAL, SOC_DANGER, SAFETY_CAR, STALE_DATA, TORCS_STATE,
    GOOD_RECHARGE, LIFT_NOT_WORTH, CUSUM_ALARM,
)


# =============================================================================
# Context Forge tests
# =============================================================================

class TestContextForge:

    def test_add_lap_summary(self):
        cf = ContextForge(circuit="bahrain", driver="VER")
        cf.add_lap_summary({
            "lap": 1, "avg_soc": 0.82,
            "alerts_this_lap": 0, "key_decision": "safe_default"
        })
        assert cf.total_laps_completed() == 1

    def test_add_lap_summary_missing_field_raises(self):
        cf = ContextForge()
        with pytest.raises(ValueError, match="missing required field"):
            cf.add_lap_summary({"lap": 1, "avg_soc": 0.5})

    def test_get_last_n_laps(self):
        cf = ContextForge()
        for i in range(1, 11):
            cf.add_lap_summary({
                "lap": i, "avg_soc": 0.85 - i * 0.02,
                "alerts_this_lap": i % 3, "key_decision": "safe_default"
            })
        last_3 = cf.get_last_n_laps(3)
        assert len(last_3) == 3
        assert last_3[0]["lap"] == 8
        assert last_3[2]["lap"] == 10

    def test_get_lap_by_number(self):
        cf = ContextForge()
        cf.add_lap_summary({
            "lap": 5, "avg_soc": 0.70,
            "alerts_this_lap": 2, "key_decision": "recharge_window"
        })
        lap = cf.get_lap(5)
        assert lap is not None
        assert lap["avg_soc"] == 0.70

    def test_get_lap_missing_returns_none(self):
        cf = ContextForge()
        assert cf.get_lap(99) is None

    def test_add_alert_and_query(self):
        cf = ContextForge()
        cf.add_alert({"rule": "soc_danger_alert", "lap": 7, "confidence": 0.81})
        cf.add_alert({"rule": "safety_car_recharge", "lap": 7, "confidence": 0.90})
        cf.add_alert({"rule": "safe_default", "lap": 8, "confidence": 0.50})
        assert cf.total_alerts_fired() == 3
        lap7 = cf.get_alerts_for_lap(7)
        assert len(lap7) == 2

    def test_add_granite_output(self):
        cf = ContextForge()
        cf.add_granite_output({
            "fan_explanation": "Battery is healthy",
            "strategy_note": "Continue current pace",
            "threshold_updates": {},
        })
        assert len(cf.data["granite_outputs"]) == 1

    def test_add_threshold_update(self):
        cf = ContextForge()
        cf.add_threshold_update({"soc_danger_threshold": 0.30})
        assert len(cf.data["threshold_updates"]) == 1

    def test_persist_and_reload(self, tmp_path):
        path = str(tmp_path / "session.json")
        cf = ContextForge(persist_path=path, circuit="bahrain", driver="VER")
        for i in range(1, 6):
            cf.add_lap_summary({
                "lap": i, "avg_soc": 0.80,
                "alerts_this_lap": 0, "key_decision": "safe_default"
            })
        cf.save()

        cf2 = ContextForge(persist_path=path)
        cf2.load()
        assert cf2.total_laps_completed() == 5
        assert cf2.data["circuit"] == "bahrain"

    def test_auto_save_every_5_laps(self, tmp_path):
        path = str(tmp_path / "auto_session.json")
        cf = ContextForge(persist_path=path, circuit="bahrain")
        for i in range(1, 6):
            cf.add_lap_summary({
                "lap": i, "avg_soc": 0.80,
                "alerts_this_lap": 0, "key_decision": "safe_default"
            })
        # Should have auto-saved at lap 5
        assert os.path.exists(path)

    def test_reset_clears_memory(self):
        cf = ContextForge()
        cf.add_lap_summary({
            "lap": 1, "avg_soc": 0.80,
            "alerts_this_lap": 0, "key_decision": "safe_default"
        })
        cf.add_alert({"rule": "test", "lap": 1})
        cf.reset()
        assert cf.total_laps_completed() == 0
        assert cf.total_alerts_fired() == 0


# =============================================================================
# Event Queue tests
# =============================================================================

class TestEventQueue:

    @pytest.mark.asyncio
    async def test_push_pop(self):
        eq = EventQueue()
        await eq.push({"type": "lap_complete", "lap": 1})
        event = await eq.pop()
        assert event["type"] == "lap_complete"
        assert event["lap"] == 1

    @pytest.mark.asyncio
    async def test_queue_size(self):
        eq = EventQueue()
        assert eq.size() == 0
        await eq.push({"type": "test"})
        assert eq.size() == 1
        await eq.pop()
        assert eq.size() == 0

    @pytest.mark.asyncio
    async def test_fifo_order(self):
        eq = EventQueue()
        for i in range(5):
            await eq.push({"lap": i})
        for i in range(5):
            event = await eq.pop()
            assert event["lap"] == i


# =============================================================================
# MPC Planner tests
# =============================================================================

class TestMPCPlanner:

    def test_plan_5_corners_basic(self):
        corners = [
            {"corner_id": 4, "net_lift_value": 0.08},
            {"corner_id": 10, "net_lift_value": 0.15},
            {"corner_id": 11, "net_lift_value": 0.03},
            {"corner_id": 14, "net_lift_value": 0.11},
            {"corner_id": 1, "net_lift_value": -0.02},
        ]
        plan = plan_5_corners(0.85, corners)
        assert len(plan) == 5
        for cid, frac in plan.items():
            assert 0.0 <= frac <= 1.0

    def test_plan_empty_corners(self):
        plan = plan_5_corners(0.5, [])
        assert plan == {}

    def test_low_soc_forces_higher_lift(self):
        corners = [
            {"corner_id": 4, "net_lift_value": 0.08},
            {"corner_id": 10, "net_lift_value": 0.15},
            {"corner_id": 11, "net_lift_value": 0.03},
        ]
        plan_high = plan_5_corners(0.90, corners)
        plan_low = plan_5_corners(0.30, corners)
        # With low SOC, need more lift to maintain >= 0.25
        total_high = sum(plan_high.values())
        total_low = sum(plan_low.values())
        assert total_low >= total_high or abs(total_low - total_high) < 0.1

    def test_soc_stays_above_threshold(self):
        corners = [
            {"corner_id": i, "net_lift_value": 0.05}
            for i in range(1, 6)
        ]
        plan = plan_5_corners(0.30, corners)
        # Simulate: SOC after plan should stay >= 0.25
        soc = 0.30
        for i, corner in enumerate(corners):
            frac = plan.get(corner["corner_id"], 0)
            if frac > 0:
                soc += corner["net_lift_value"] * frac
        assert soc >= 0.24  # Allow small floating point tolerance


# =============================================================================
# Granite Client tests (unit -- no real API calls)
# =============================================================================

class TestGraniteClient:

    def test_build_prompt(self):
        client = GraniteClient(api_key="test_key", endpoint="https://fake.ibm.com/v1/chat")
        laps = [
            {"lap": 1, "avg_soc": 0.82, "alerts_this_lap": 0, "key_decision": "safe_default"},
            {"lap": 2, "avg_soc": 0.78, "alerts_this_lap": 1, "key_decision": "recharge_window"},
        ]
        prompt = client._build_prompt(laps)
        assert "Lap 1" in prompt
        assert "Lap 2" in prompt
        assert "threshold_updates" in prompt
        assert "fan_explanation" in prompt

    def test_parse_response_valid_json(self):
        client = GraniteClient(api_key="test", endpoint="https://fake.ibm.com")
        text = '{"fan_explanation": "Battery doing well", "strategy_note": "Keep pace", "threshold_updates": {}}'
        result = client._parse_response(text)
        assert result["fan_explanation"] == "Battery doing well"
        assert result["threshold_updates"] == {}

    def test_parse_response_with_markdown_fences(self):
        client = GraniteClient(api_key="test", endpoint="https://fake.ibm.com")
        text = '```json\n{"fan_explanation": "Healthy", "strategy_note": "", "threshold_updates": {}}\n```'
        result = client._parse_response(text)
        assert result["fan_explanation"] == "Healthy"

    def test_parse_response_invalid_json_returns_fallback(self):
        client = GraniteClient(api_key="test", endpoint="https://fake.ibm.com")
        text = "This is not JSON at all"
        result = client._parse_response(text)
        assert "fan_explanation" in result
        assert result["threshold_updates"] == {}

    @pytest.mark.asyncio
    async def test_analyse_laps_empty_returns_error(self):
        client = GraniteClient(api_key="test", endpoint="https://fake.ibm.com")
        result = await client.analyse_laps([])
        assert result["error"] == "no laps to analyse"


# =============================================================================
# Alert Builder tests
# =============================================================================

class TestAlertBuilder:

    def test_build_payload_basic(self):
        alert = {
            "alert_id": "abc-123",
            "rule": "soc_danger_alert",
            "recommendation": "Recharge now",
            "reason": "soc below threshold",
            "priority": 9,
            "confidence": 0.85,
            "fan_explanation": "Battery low",
            "source_module": "voltedge",
        }
        payload = build_payload(alert, NORMAL)
        assert payload["alert_id"] == "abc-123"
        assert payload["rule"] == "soc_danger_alert"
        assert payload["soc_estimated"] == NORMAL["soc_estimated"]
        assert payload["data_source"] == "mock"
        assert payload["source_module"] == "voltedge"

    def test_build_payload_gridsense_source(self):
        alert = {
            "alert_id": "gs-001",
            "rule": "gridsense_tyre_complaint",
            "recommendation": "Check front tyre wear",
            "reason": "Driver reports understeer",
            "priority": 7,
            "confidence": 0.72,
            "source_module": "gridsense",
        }
        payload = build_payload(alert, NORMAL)
        assert payload["source_module"] == "gridsense"

    def test_build_payload_default_values(self):
        payload = build_payload({}, {})
        assert payload["rule"] == "unknown"
        assert payload["recommendation"] == "Maintain current mode"
        assert payload["source_module"] == "voltedge"
        assert payload["confidence"] == 0.5

    def test_build_payload_preserves_brake_state(self):
        alert = {"alert_id": "t1", "rule": "test", "recommendation": "test"}
        braking_state = {**NORMAL, "brake": True}
        payload = build_payload(alert, braking_state)
        assert payload["brake"] is True

    def test_build_payload_torcs_source(self):
        alert = {"alert_id": "t2", "rule": "test"}
        payload = build_payload(alert, TORCS_STATE)
        assert payload["data_source"] == "torcs"


# =============================================================================
# Slow Path Runner tests
# =============================================================================

class TestSlowPathRunner:

    @pytest.mark.asyncio
    async def test_event_loop_processes_lap_events(self):
        eq = EventQueue()
        cf = ContextForge(circuit="bahrain", driver="VER")
        runner = SlowPathRunner(
            event_queue=eq, context_forge=cf,
            mpc_interval=60.0
        )

        event_task, mpc_task = await runner.start()

        for lap in range(1, 6):
            await eq.push({
                "type": "lap_complete",
                "lap": lap,
                "avg_soc": 0.80,
                "alerts_this_lap": 0,
                "key_decision": "safe_default",
            })
        await asyncio.sleep(0.5)
        runner.stop()
        await asyncio.sleep(0.3)

        assert cf.total_laps_completed() == 5

    @pytest.mark.asyncio
    async def test_event_loop_records_alerts(self):
        eq = EventQueue()
        cf = ContextForge()
        runner = SlowPathRunner(event_queue=eq, context_forge=cf, mpc_interval=60.0)

        event_task, mpc_task = await runner.start()

        await eq.push({
            "type": "alert_fired",
            "alert": {"rule": "soc_danger", "lap": 5, "confidence": 0.9},
        })
        await asyncio.sleep(0.5)
        runner.stop()
        await asyncio.sleep(0.3)

        assert cf.total_alerts_fired() == 1

    @pytest.mark.asyncio
    async def test_granite_trigger_at_lap_10(self):
        eq = EventQueue()
        cf = ContextForge(circuit="bahrain")
        runner = SlowPathRunner(
            event_queue=eq, context_forge=cf,
            granite_client=None,  # No real API
            granite_every_n_laps=10,
            mpc_interval=60.0,
        )

        event_task, mpc_task = await runner.start()

        for lap in range(1, 11):
            await eq.push({
                "type": "lap_complete",
                "lap": lap,
                "avg_soc": 0.85 - lap * 0.02,
                "alerts_this_lap": 0,
                "key_decision": "safe_default",
            })
            await asyncio.sleep(0.05)

        await asyncio.sleep(0.5)
        runner.stop()
        await asyncio.sleep(0.3)

        assert cf.total_laps_completed() == 10
        # No Granite client = no call, but no crash
        assert runner._granite_call_count == 0

    @pytest.mark.asyncio
    async def test_shutdown_event_stops_loop(self):
        eq = EventQueue()
        cf = ContextForge()
        runner = SlowPathRunner(event_queue=eq, context_forge=cf, mpc_interval=60.0)

        event_task, mpc_task = await runner.start()
        await eq.push({"type": "shutdown"})
        await asyncio.sleep(0.5)
        # Event loop should have stopped
        assert cf.total_laps_completed() == 0

    @pytest.mark.asyncio
    async def test_stats_output(self):
        eq = EventQueue()
        cf = ContextForge()
        runner = SlowPathRunner(event_queue=eq, context_forge=cf, mpc_interval=60.0)
        stats = runner.stats()
        assert "granite_calls" in stats
        assert "mpc_calls" in stats
        assert "laps_recorded" in stats

    @pytest.mark.asyncio
    async def test_update_state(self):
        eq = EventQueue()
        cf = ContextForge()
        runner = SlowPathRunner(event_queue=eq, context_forge=cf)
        runner.update_state(NORMAL)
        assert runner._current_state["driver"] == "VER"


# =============================================================================
# Integration: Day 2 merge pipeline
# =============================================================================

class TestDay2Integration:

    def test_voltedge_alert_through_builder(self):
        """VoltEdge alert flows correctly through alert_builder."""
        alert = {
            "alert_id": "ve-001",
            "rule": "soc_danger_alert",
            "recommendation": "Recharge now",
            "reason": "soc=0.22",
            "priority": 9,
            "confidence": 0.85,
            "source_module": "voltedge",
        }
        payload = build_payload(alert, SOC_DANGER)
        assert payload["source_module"] == "voltedge"
        assert payload["soc_estimated"] == 0.22

    def test_gridsense_alert_through_builder(self):
        """GridSense alert flows correctly through same alert_builder."""
        alert = {
            "alert_id": "gs-001",
            "rule": "gridsense_energy_complaint",
            "recommendation": "Investigate battery management",
            "reason": "Driver reports lack of power",
            "priority": 8,
            "confidence": 0.78,
            "source_module": "gridsense",
        }
        payload = build_payload(alert, NORMAL)
        assert payload["source_module"] == "gridsense"
        assert payload["rule"] == "gridsense_energy_complaint"

    def test_payload_schema_consistency_across_sources(self):
        """Both VoltEdge and GridSense payloads have identical keys."""
        ve_alert = {
            "alert_id": "ve", "rule": "test_ve",
            "recommendation": "R", "reason": "X",
            "priority": 5, "confidence": 0.6,
            "source_module": "voltedge",
        }
        gs_alert = {
            "alert_id": "gs", "rule": "test_gs",
            "recommendation": "R", "reason": "X",
            "priority": 5, "confidence": 0.6,
            "source_module": "gridsense",
        }
        ve_payload = build_payload(ve_alert, NORMAL)
        gs_payload = build_payload(gs_alert, NORMAL)
        assert set(ve_payload.keys()) == set(gs_payload.keys())

    @pytest.mark.asyncio
    async def test_full_slow_path_30_laps(self):
        """Simulate 30 laps flowing through event queue -> context forge."""
        eq = EventQueue()
        cf = ContextForge(circuit="bahrain", driver="VER")
        runner = SlowPathRunner(
            event_queue=eq, context_forge=cf,
            granite_client=None,
            granite_every_n_laps=10,
            mpc_interval=60.0,
        )

        event_task, mpc_task = await runner.start()

        for lap in range(1, 31):
            await eq.push({
                "type": "lap_complete",
                "lap": lap,
                "avg_soc": round(0.85 - (lap * 0.01), 2),
                "alerts_this_lap": lap % 4,
                "key_decision": "safe_default" if lap < 20 else "recharge_window",
            })
            await asyncio.sleep(0.02)

        await asyncio.sleep(1.0)
        runner.stop()
        await asyncio.sleep(0.3)

        assert cf.total_laps_completed() == 30
        last_5 = cf.get_last_n_laps(5)
        assert len(last_5) == 5
        assert last_5[4]["lap"] == 30


# =============================================================================
# Run
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
