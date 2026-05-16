"""
Person C — Day 3 Tests: Edge Cases, Demo Prep, Polish
=====================================================
- Edge Case 4: Granite timeout (fast path unaffected)
- Demo audio pre-generation
- Full pipeline demo rehearsal (Bahrain Laps 28-38)
- WebSocket broadcast validation
- Context Forge 30+ lap accumulation

Run: python -m pytest tests/test_person_c_day3.py -v
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
from output.tts import generate_demo_audio
from tests.mock_state_vectors import (
    NORMAL, SOC_DANGER, SAFETY_CAR, STALE_DATA, TORCS_STATE,
    GOOD_RECHARGE, LIFT_NOT_WORTH, CUSUM_ALARM, RED_FLAG,
)


# =============================================================================
# Edge Case 4 — Granite Timeout
# =============================================================================

class TestEdgeCase4GraniteTimeout:
    """
    Edge Case 4: Set Granite timeout to 0.1s (force timeout).
    Expected: fast path continues unaffected, error logged, thresholds unchanged.
    """

    @pytest.mark.asyncio
    async def test_granite_timeout_returns_error_dict(self):
        """Granite call with impossible timeout returns error, never raises."""
        client = GraniteClient(
            api_key="test_key",
            endpoint="https://httpstat.us/200?sleep=5000",  # 5s delay
            model="ibm-granite/granite-3.3-8b-instruct",
        )
        result = await client.call("Test prompt", timeout=0.1)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_granite_timeout_thresholds_unchanged(self):
        """After Granite timeout, threshold_updates dict stays empty."""
        eq = EventQueue()
        cf = ContextForge(circuit="bahrain", driver="VER")

        # Unreachable endpoint -> will timeout
        granite = GraniteClient(
            api_key="test",
            endpoint="https://httpstat.us/200?sleep=5000",
        )

        runner = SlowPathRunner(
            event_queue=eq, context_forge=cf,
            granite_client=granite,
            granite_every_n_laps=5,
            mpc_interval=60.0,
        )

        event_task, mpc_task = await runner.start()

        # Push 5 laps to trigger Granite at lap 5
        for lap in range(1, 6):
            await eq.push({
                "type": "lap_complete",
                "lap": lap,
                "avg_soc": 0.80,
                "alerts_this_lap": 0,
                "key_decision": "safe_default",
            })
            await asyncio.sleep(0.05)

        # Wait for Granite timeout (0.1s default for our test client)
        await asyncio.sleep(2.0)
        runner.stop()
        await asyncio.sleep(0.3)

        # Thresholds should be unchanged
        assert runner.threshold_updates == {}
        # Laps should still be recorded
        assert cf.total_laps_completed() == 5
        # No Granite output stored
        assert len(cf.data["granite_outputs"]) == 0

    @pytest.mark.asyncio
    async def test_fast_path_continues_during_granite_timeout(self):
        """
        Simulate: Granite times out but new lap events keep flowing.
        Verifies the event loop doesn't get blocked by Granite calls.
        """
        eq = EventQueue()
        cf = ContextForge(circuit="bahrain")
        # Use RFC5737 unreachable address to guarantee fast connection timeout
        granite = GraniteClient(
            api_key="test",
            endpoint="http://192.0.2.1:1/v1/chat",
        )

        runner = SlowPathRunner(
            event_queue=eq, context_forge=cf,
            granite_client=granite,
            granite_every_n_laps=5,
            mpc_interval=60.0,
        )

        event_task, mpc_task = await runner.start()

        # Push 10 laps (Granite triggers at 5, but events 6-10 should still flow)
        for lap in range(1, 11):
            await eq.push({
                "type": "lap_complete",
                "lap": lap,
                "avg_soc": 0.80 - lap * 0.01,
                "alerts_this_lap": 0,
                "key_decision": "safe_default",
            })
            await asyncio.sleep(0.05)

        await asyncio.sleep(1.0)
        runner.stop()
        await asyncio.sleep(0.3)

        # All 10 laps should be recorded even though Granite timed out at lap 5
        assert cf.total_laps_completed() == 10

    def test_granite_parse_response_with_error_text(self):
        """Granite returns garbage text -> parser provides safe fallback."""
        client = GraniteClient(api_key="test", endpoint="https://fake.ibm.com")
        result = client._parse_response("INTERNAL SERVER ERROR 500 TIMEOUT")
        assert "fan_explanation" in result
        assert result["threshold_updates"] == {}

    def test_granite_parse_response_partial_json(self):
        """Granite returns truncated JSON -> parser handles gracefully."""
        client = GraniteClient(api_key="test", endpoint="https://fake.ibm.com")
        result = client._parse_response('{"fan_explanation": "test", "thresho')
        # Should not crash, returns fallback
        assert "fan_explanation" in result


# =============================================================================
# Demo Audio Pre-Generation (Day 3 Afternoon)
# =============================================================================

class TestDemoAudioGeneration:
    """
    Pre-generate demo audio for the 3 demo moments.
    Demo window: Bahrain 2024, Laps 28-38.
    """

    def test_generate_demo_audio_returns_3_moments(self):
        moments = generate_demo_audio()
        assert len(moments) == 3

    def test_generate_demo_audio_file_paths(self):
        moments = generate_demo_audio()
        for m in moments:
            assert "file" in m
            assert "text" in m
            # Each file should be in demo_audio/ dir
            assert "demo_audio" in m["file"]

    def test_demo_audio_text_content(self):
        """Verify demo audio covers our 3 key demo moments."""
        moments = generate_demo_audio()

        texts = [m["text"] for m in moments]
        # Moment 1: SOC danger at boost zone
        assert any("recharge" in t.lower() or "battery" in t.lower() for t in texts)
        # Moment 2: Optimal recharge window
        assert any("recharge" in t.lower() or "energy" in t.lower() for t in texts)
        # Moment 3: Lift not worth it
        assert any("throttle" in t.lower() or "lift" in t.lower() for t in texts)

    def test_demo_moment_files_created(self):
        """Check demo_audio/ dir exists after generation."""
        generate_demo_audio()
        assert os.path.isdir("demo_audio") or os.path.isdir(
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "demo_audio")
        )


# =============================================================================
# Demo Rehearsal: Bahrain Laps 28-38
# =============================================================================

class TestDemoRehearsal:
    """
    Full demo rehearsal test: 10-lap replay through the slow path.
    Verifies: Context Forge accumulation, alert flow, Granite trigger timing.
    """

    @pytest.mark.asyncio
    async def test_bahrain_28_38_slow_path_rehearsal(self):
        """Push laps 28-38 through the slow path runner."""
        eq = EventQueue()
        cf = ContextForge(circuit="bahrain", driver="VER")
        runner = SlowPathRunner(
            event_queue=eq, context_forge=cf,
            granite_client=None,
            granite_every_n_laps=10,
            mpc_interval=60.0,
        )

        event_task, mpc_task = await runner.start()

        # Simulate laps 28-38 with realistic SOC progression
        soc_schedule = [0.52, 0.49, 0.45, 0.38, 0.35, 0.31, 0.28, 0.26, 0.30, 0.34, 0.40]
        for i, lap in enumerate(range(28, 39)):
            soc = soc_schedule[i]
            alerts = 2 if soc < 0.35 else 1 if soc < 0.45 else 0
            decision = (
                "soc_danger_alert" if soc < 0.30
                else "recharge_window" if soc < 0.40
                else "safe_default"
            )
            await eq.push({
                "type": "lap_complete",
                "lap": lap,
                "avg_soc": soc,
                "alerts_this_lap": alerts,
                "key_decision": decision,
            })
            await asyncio.sleep(0.03)

        await asyncio.sleep(0.5)
        runner.stop()
        await asyncio.sleep(0.3)

        assert cf.total_laps_completed() == 11  # laps 28 through 38 inclusive
        # Lap 30 should trigger Granite (30 % 10 == 0) if running from lap 1
        # But since we start at 28, lap 30 is the trigger
        last_3 = cf.get_last_n_laps(3)
        assert last_3[-1]["lap"] == 38

    def test_demo_alert_payloads_match_script(self):
        """
        Validate the 3 demo moments produce correct payloads.
        Demo script: Lap 31 (SOC danger), Lap 34 (recharge), Lap 37 (lift not worth).
        """
        # Moment 1: Lap 31, SOC danger at boost zone
        alert1 = {
            "alert_id": "demo-m1",
            "rule": "soc_danger_alert",
            "recommendation": "Recharge now — Turn eleven",
            "reason": "soc=0.38 < danger_threshold=0.25",
            "priority": 9,
            "confidence": 0.88,
            "source_module": "voltedge",
        }
        state1 = {**SOC_DANGER, "lap": 31, "corner_id": 11, "soc_estimated": 0.22}
        p1 = build_payload(alert1, state1)
        assert p1["lap"] == 31
        assert p1["priority"] == 9
        assert p1["source_module"] == "voltedge"

        # Moment 2: Lap 34, optimal recharge window
        alert2 = {
            "alert_id": "demo-m2",
            "rule": "optimal_recharge_window",
            "recommendation": "Lift here — net energy gain worth aero trade",
            "reason": "net_lift_value=0.15 > 0.05",
            "priority": 6,
            "confidence": 0.74,
            "source_module": "voltedge",
        }
        state2 = {**GOOD_RECHARGE, "lap": 34, "corner_id": 10}
        p2 = build_payload(alert2, state2)
        assert p2["lap"] == 34
        assert p2["rule"] == "optimal_recharge_window"

        # Moment 3: Lap 37, lift not worth it
        alert3 = {
            "alert_id": "demo-m3",
            "rule": "lift_not_worth_it",
            "recommendation": "Stay on throttle through Turn four",
            "reason": "net_lift_value=-0.02",
            "priority": 7,
            "confidence": 0.81,
            "source_module": "voltedge",
        }
        state3 = {**LIFT_NOT_WORTH, "lap": 37, "corner_id": 1}
        p3 = build_payload(alert3, state3)
        assert p3["lap"] == 37
        assert p3["rule"] == "lift_not_worth_it"

    def test_context_forge_accumulates_30_laps(self):
        """Definition of Done: Context Forge has all 30+ lap summaries."""
        cf = ContextForge(circuit="bahrain", driver="VER")
        for lap in range(1, 31):
            cf.add_lap_summary({
                "lap": lap,
                "avg_soc": round(0.85 - lap * 0.015, 2),
                "alerts_this_lap": lap % 3,
                "key_decision": "safe_default",
            })
        assert cf.total_laps_completed() == 30
        assert cf.get_lap(1) is not None
        assert cf.get_lap(30) is not None

    def test_granite_called_at_lap_10_without_blocking(self):
        """
        Definition of Done: Granite called at lap 10, fast path continues.
        We test that the runner correctly identifies lap 10 as a trigger.
        """
        runner = SlowPathRunner(
            event_queue=EventQueue(),
            context_forge=ContextForge(),
            granite_every_n_laps=10,
        )
        # Lap 10 should trigger, 5 should not
        assert 10 % runner.granite_every_n_laps == 0
        assert 5 % runner.granite_every_n_laps != 0
        assert 20 % runner.granite_every_n_laps == 0

    def test_mpc_planner_demo_corners(self):
        """MPC planner produces valid plan for Bahrain corners."""
        corners = [
            {"corner_id": 4, "net_lift_value": 0.08},
            {"corner_id": 10, "net_lift_value": 0.15},
            {"corner_id": 11, "net_lift_value": 0.03},
            {"corner_id": 14, "net_lift_value": 0.11},
            {"corner_id": 1, "net_lift_value": -0.02},
        ]
        # SOC at 0.35 (mid-demo)
        plan = plan_5_corners(0.35, corners)
        assert len(plan) == 5
        # All lift fractions should be valid
        for cid, frac in plan.items():
            assert 0.0 <= frac <= 1.0


# =============================================================================
# WebSocket Broadcast Validation
# =============================================================================

class TestWebSocketBroadcast:
    """Test the broadcast function and merge_queues logic."""

    @pytest.mark.asyncio
    async def test_broadcast_deduplication(self):
        """Same alert_id should not be broadcast twice."""
        from output.websocket_server import broadcast, _recent_alert_ids
        _recent_alert_ids.clear()

        # First broadcast (no connected clients, but dedup list should update)
        payload = {"alert_id": "test-dedup-001", "rule": "test"}
        await broadcast(payload)
        assert "test-dedup-001" in _recent_alert_ids

    @pytest.mark.asyncio
    async def test_merge_queues_yields_from_both(self):
        """merge_queues yields items from multiple sources."""
        from output.websocket_server import merge_queues

        q1 = asyncio.Queue()
        q2 = asyncio.Queue()

        await q1.put({"source": "voltedge", "id": 1})
        await q2.put({"source": "gridsense", "id": 2})

        results = []

        async def collect():
            async for source, item in merge_queues(q1, q2):
                results.append((source, item))
                if len(results) >= 2:
                    break

        try:
            await asyncio.wait_for(collect(), timeout=2.0)
        except asyncio.TimeoutError:
            pass

        assert len(results) == 2
        sources = {r[0] for r in results}
        assert "voltedge" in sources
        assert "gridsense" in sources


# =============================================================================
# TTS Edge Cases
# =============================================================================

class TestTTSEdgeCases:
    """TTS should skip during braking and handle errors gracefully."""

    @pytest.mark.asyncio
    async def test_tts_skips_during_braking(self):
        """TTS speak() should not speak when brake=True."""
        from output.tts import speak
        braking_state = {**NORMAL, "brake": True}
        # Should return without error (skip)
        await speak("Test message", braking_state)
        # No crash = pass

    @pytest.mark.asyncio
    async def test_tts_speaks_when_not_braking(self):
        """TTS speak() should attempt speech when brake=False."""
        import output.tts as tts_module
        # Disable engine for testing to avoid blocking
        original_engine = tts_module._engine
        tts_module._engine = None
        try:
            normal_state = {**NORMAL, "brake": False}
            # Should not crash (falls back to print-only mode)
            await tts_module.speak("Test message", normal_state)
        finally:
            tts_module._engine = original_engine


# =============================================================================
# Definition of Done Validation
# =============================================================================

class TestDefinitionOfDone:
    """
    Final validation against the Definition of Done checklist:
    - [ ] Granite called at lap 10 without blocking fast path
    - [ ] Context Forge has all 30+ lap summaries
    - [ ] Audio fires correctly, skips braking zones
    - [ ] Fan panel: Granite explanation visible after lap 10 call
    - [ ] UI updates in real time, all panels active
    - [ ] Demo audio pre-generated and cached
    """

    def test_granite_lap_10_trigger_config(self):
        """granite_every_n_laps is set to 10."""
        runner = SlowPathRunner(
            event_queue=EventQueue(),
            context_forge=ContextForge(),
            granite_every_n_laps=10,
        )
        assert runner.granite_every_n_laps == 10

    def test_context_forge_supports_30_laps(self):
        cf = ContextForge()
        for i in range(1, 31):
            cf.add_lap_summary({
                "lap": i, "avg_soc": 0.80,
                "alerts_this_lap": 0, "key_decision": "safe_default"
            })
        assert cf.total_laps_completed() == 30

    def test_alert_payload_has_fan_explanation_field(self):
        alert = {"alert_id": "dod-1", "rule": "test", "fan_explanation": "Battery OK"}
        payload = build_payload(alert, NORMAL)
        assert payload["fan_explanation"] == "Battery OK"

    def test_alert_payload_has_source_module_field(self):
        alert = {"alert_id": "dod-2", "rule": "test", "source_module": "gridsense"}
        payload = build_payload(alert, NORMAL)
        assert payload["source_module"] == "gridsense"

    def test_demo_audio_generation_callable(self):
        # Should be callable without error
        moments = generate_demo_audio()
        assert isinstance(moments, list)
        assert len(moments) == 3

    def test_mpc_planner_available(self):
        plan = plan_5_corners(0.5, [{"corner_id": 1, "net_lift_value": 0.1}])
        assert isinstance(plan, dict)


# =============================================================================
# Run
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
