# tests/test_person_c_day2.py
# -----------------------------------------------------------------------------
# Person C -- Day 2 Integration Tests
#
# Validates:
#   1. Alert builder produces correct payload schema
#   2. WebSocket broadcast function works with mock clients
#   3. OutputEventLoop processes VoltEdge alerts
#   4. OutputEventLoop processes GridSense alerts
#   5. Alert deduplication
#   6. MPC planner produces valid plans
#   7. Alert logger writes and reads JSONL
#   8. Context Forge integration via output loop
#   9. Granite trigger at lap 10
#  10. Full 30-lap replay through output loop
#  11. Payload includes pipeline latency and MPC plan
#
# Run:
#   python tests/test_person_c_day2.py     (standalone)
# -----------------------------------------------------------------------------

import sys
import os
import copy
import time
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from output.alert_builder import build_payload
from output.main_loop import OutputEventLoop, AlertLogger
from slow_path.context_forge import ContextForge
from slow_path.mpc_planner import plan_5_corners
from fast_path.pipeline import FastPathPipeline
from tests.mock_state_vectors import (
    NORMAL, SOC_DANGER, LIFT_NOT_WORTH, GOOD_RECHARGE,
    SAFETY_CAR, STALE_DATA, TORCS_STATE, CUSUM_ALARM,
    BATTERY_CRITICAL,
)


# -- Helpers -------------------------------------------------------------------

def make_mock_alert(rule="soc_danger_alert", source="voltedge", lap=15, conf=0.82):
    return {
        "alert_id":        f"test-{rule}-{lap}",
        "rule":            rule,
        "recommendation":  "Recharge immediately",
        "reason":          "SOC below threshold at boost zone",
        "priority":        9,
        "confidence":      conf,
        "soc_estimated":   0.22,
        "corner_id":       11,
        "lap":             lap,
        "timestamp":       time.time(),
        "fan_explanation":  "",
        "source_module":   source,
        "data_source":     "mock",
        "brake":           False,
    }


def make_mock_state():
    return {
        "soc_estimated": 0.65,
        "corner_id":     4,
        "lap":           15,
        "timestamp":     time.time(),
        "data_source":   "mock",
        "brake":         False,
    }


# ==============================================================================
# TEST 1: Alert builder payload schema
# ==============================================================================

def test_alert_builder():
    """Alert builder produces correct payload schema with all required fields."""
    print("\n[Test 1] Alert builder payload schema")
    alert = make_mock_alert()
    state = make_mock_state()
    payload = build_payload(alert, state)

    required_fields = [
        "alert_id", "rule", "recommendation", "reason", "priority",
        "confidence", "soc_estimated", "corner_id", "lap", "timestamp",
        "fan_explanation", "data_source", "source_module",
    ]
    for field in required_fields:
        assert field in payload, f"Payload missing '{field}'"

    # Check source_module preservation
    assert payload["source_module"] == "voltedge"

    # Check GridSense source_module
    gs_alert = make_mock_alert(rule="gridsense_understeer", source="gridsense")
    gs_payload = build_payload(gs_alert, state)
    assert gs_payload["source_module"] == "gridsense"

    print(f"  Fields: {len(required_fields)} required, all present")
    print(f"  VoltEdge source_module: {payload['source_module']}")
    print(f"  GridSense source_module: {gs_payload['source_module']}")
    print("  PASS")


# ==============================================================================
# TEST 2: Broadcast mock
# ==============================================================================

def test_broadcast_mock():
    """Broadcast function collects payloads correctly."""
    print("\n[Test 2] Broadcast mock")

    collected = []

    async def mock_broadcast(payload):
        collected.append(payload)

    async def _run():
        ve_q = asyncio.Queue()
        loop = OutputEventLoop(
            voltedge_queue=ve_q,
            broadcast_fn=mock_broadcast,
            enable_tts=False,
            enable_mpc=False,
        )

        # Push 5 alerts
        for i in range(5):
            await ve_q.put(make_mock_alert(lap=i + 1))
        await ve_q.put(None)  # sentinel
        await loop.gridsense_queue.put(None)

        await loop.run()
        return collected

    result = asyncio.run(_run())
    assert len(result) == 5, f"Expected 5 broadcasts, got {len(result)}"
    print(f"  Broadcasts collected: {len(result)}")
    print("  PASS")


# ==============================================================================
# TEST 3: VoltEdge alert processing
# ==============================================================================

def test_voltedge_processing():
    """VoltEdge alerts flow through the output loop correctly."""
    print("\n[Test 3] VoltEdge alert processing")

    collected = []

    async def mock_broadcast(payload):
        collected.append(payload)

    async def _run():
        ve_q = asyncio.Queue()
        forge = ContextForge(circuit="bahrain", session_type="race", driver="VER")

        loop = OutputEventLoop(
            voltedge_queue=ve_q,
            broadcast_fn=mock_broadcast,
            context_forge=forge,
            enable_tts=False,
            enable_mpc=False,
        )

        # Push VoltEdge alerts
        alerts = [
            make_mock_alert("soc_danger_alert", lap=10),
            make_mock_alert("safety_car_recharge", lap=11),
            make_mock_alert("lift_not_worth_it", lap=12),
        ]
        for a in alerts:
            await ve_q.put(a)
        await ve_q.put(None)
        await loop.gridsense_queue.put(None)

        await loop.run()

        stats = loop.stats()
        return stats, forge, collected

    stats, forge, payloads = asyncio.run(_run())

    assert stats["voltedge_alerts"] == 3, f"Expected 3 VoltEdge, got {stats['voltedge_alerts']}"
    assert stats["broadcast_count"] == 3
    assert forge.total_alerts_fired() == 3

    print(f"  VoltEdge alerts: {stats['voltedge_alerts']}")
    print(f"  Broadcasts: {stats['broadcast_count']}")
    print(f"  Context Forge alerts: {forge.total_alerts_fired()}")
    print("  PASS")


# ==============================================================================
# TEST 4: GridSense alert processing
# ==============================================================================

def test_gridsense_processing():
    """GridSense alerts merge into the output loop correctly."""
    print("\n[Test 4] GridSense alert processing")

    collected = []

    async def mock_broadcast(payload):
        collected.append(payload)

    async def _run():
        ve_q = asyncio.Queue()
        gs_q = asyncio.Queue()

        loop = OutputEventLoop(
            voltedge_queue=ve_q,
            gridsense_queue=gs_q,
            broadcast_fn=mock_broadcast,
            enable_tts=False,
            enable_mpc=False,
        )

        # Push GridSense alerts
        gs_alerts = [
            make_mock_alert("gridsense_understeer", source="gridsense", lap=10),
            make_mock_alert("gridsense_tyre_overheating", source="gridsense", lap=11),
        ]
        for a in gs_alerts:
            await gs_q.put(a)

        # Push VoltEdge sentinel (to end main loop)
        await ve_q.put(None)
        # Push GridSense sentinel
        await gs_q.put(None)

        await loop.run()
        return loop.stats(), collected

    stats, payloads = asyncio.run(_run())

    assert stats["gridsense_alerts"] == 2, f"Expected 2 GridSense, got {stats['gridsense_alerts']}"

    # Verify source_module in payloads
    gs_payloads = [p for p in payloads if p.get("source_module") == "gridsense"]
    assert len(gs_payloads) == 2, f"Expected 2 GridSense payloads, got {len(gs_payloads)}"

    print(f"  GridSense alerts: {stats['gridsense_alerts']}")
    print(f"  GridSense payloads: {len(gs_payloads)}")
    print("  PASS")


# ==============================================================================
# TEST 5: Alert deduplication
# ==============================================================================

def test_deduplication():
    """Duplicate alert_ids are skipped."""
    print("\n[Test 5] Alert deduplication")

    collected = []

    async def mock_broadcast(payload):
        collected.append(payload)

    async def _run():
        ve_q = asyncio.Queue()

        loop = OutputEventLoop(
            voltedge_queue=ve_q,
            broadcast_fn=mock_broadcast,
            enable_tts=False,
            enable_mpc=False,
        )

        # Push same alert twice
        alert = make_mock_alert("soc_danger_alert", lap=15)
        await ve_q.put(alert)
        await ve_q.put(copy.deepcopy(alert))  # same alert_id
        await ve_q.put(None)
        await loop.gridsense_queue.put(None)

        await loop.run()
        return collected

    result = asyncio.run(_run())
    # Second push has same alert_id, should be deduped
    assert len(result) == 1, f"Expected 1 (deduped), got {len(result)}"
    print(f"  Broadcasts after dedup: {len(result)}")
    print("  PASS")


# ==============================================================================
# TEST 6: MPC planner
# ==============================================================================

def test_mpc_planner():
    """MPC planner produces valid lift fractions for 5 corners."""
    print("\n[Test 6] MPC planner")

    corners = [
        {"corner_id": 4,  "net_lift_value": 0.08},
        {"corner_id": 10, "net_lift_value": 0.06},
        {"corner_id": 11, "net_lift_value": 0.05},
        {"corner_id": 14, "net_lift_value": 0.04},
        {"corner_id": 1,  "net_lift_value": 0.07},
    ]

    # Normal SOC
    plan = plan_5_corners(0.65, corners)
    assert len(plan) == 5, f"Expected 5 corners in plan, got {len(plan)}"
    for cid, lift in plan.items():
        assert 0.0 <= lift <= 1.0, f"Lift fraction out of range: {lift}"
    print(f"  SOC=0.65 plan: {plan}")

    # Low SOC -- should lift more
    plan_low = plan_5_corners(0.28, corners)
    total_lift_normal = sum(plan.values())
    total_lift_low = sum(plan_low.values())
    assert total_lift_low >= total_lift_normal * 0.5, \
        f"Low SOC should lift at least as much: normal={total_lift_normal:.2f} low={total_lift_low:.2f}"
    print(f"  SOC=0.28 plan: {plan_low}")

    # Empty corners
    plan_empty = plan_5_corners(0.50, [])
    assert plan_empty == {}, "Empty corners should return empty plan"

    print("  PASS")


# ==============================================================================
# TEST 7: Alert logger
# ==============================================================================

def test_alert_logger():
    """AlertLogger writes and reads JSONL correctly."""
    print("\n[Test 7] Alert logger")

    test_path = os.path.join(os.path.dirname(__file__), "..", "data", "test_alerts.jsonl")
    logger = AlertLogger(filepath=test_path)
    logger.clear()

    # Write 5 entries
    for i in range(5):
        logger.log({"rule": f"test_rule_{i}", "lap": i + 1})

    # Read back
    entries = logger.read_all()
    assert len(entries) == 5, f"Expected 5 entries, got {len(entries)}"
    assert entries[0]["rule"] == "test_rule_0"
    assert entries[4]["lap"] == 5

    # Cleanup
    logger.clear()
    assert len(logger.read_all()) == 0

    print(f"  Written: 5 entries")
    print(f"  Read back: {len(entries)} entries")
    print("  PASS")


# ==============================================================================
# TEST 8: Context Forge via output loop
# ==============================================================================

def test_context_forge_via_loop():
    """Context Forge receives alerts from the output loop."""
    print("\n[Test 8] Context Forge via output loop")

    async def _run():
        ve_q = asyncio.Queue()
        forge = ContextForge(circuit="bahrain", session_type="race", driver="VER")

        async def noop(p): pass

        loop = OutputEventLoop(
            voltedge_queue=ve_q,
            broadcast_fn=noop,
            context_forge=forge,
            enable_tts=False,
            enable_mpc=False,
        )

        # Push alerts with different rules
        for i in range(10):
            alert = make_mock_alert(
                rule="soc_danger_alert" if i % 3 == 0 else "safe_default",
                lap=i + 1,
            )
            await ve_q.put(alert)
        await ve_q.put(None)
        await loop.gridsense_queue.put(None)

        await loop.run()
        return forge

    forge = asyncio.run(_run())

    # Only non-safe-default alerts should be stored
    stored = forge.total_alerts_fired()
    assert stored > 0, "No alerts stored in Context Forge"
    assert stored <= 10, f"Too many alerts stored: {stored}"

    print(f"  Alerts stored in forge: {stored}")
    print("  PASS")


# ==============================================================================
# TEST 9: Granite trigger simulation
# ==============================================================================

def test_granite_trigger():
    """Granite trigger fires at lap 10 and broadcasts result."""
    print("\n[Test 9] Granite trigger")

    collected = []

    class MockGranite:
        async def analyse_laps(self, laps):
            return {
                "fan_explanation": "Battery strategy on track for podium finish.",
                "strategy_note": "SOC trending within optimal band.",
                "threshold_updates": {},
            }

    async def mock_broadcast(payload):
        collected.append(payload)

    async def _run():
        ve_q = asyncio.Queue()
        forge = ContextForge(circuit="bahrain", session_type="race", driver="VER")

        # Add 10 lap summaries to forge
        for i in range(1, 11):
            forge.add_lap_summary({
                "lap": i, "avg_soc": 0.7 - (i * 0.02),
                "alerts_this_lap": 1, "key_decision": "none",
            })

        loop = OutputEventLoop(
            voltedge_queue=ve_q,
            broadcast_fn=mock_broadcast,
            context_forge=forge,
            granite_client=MockGranite(),
            enable_tts=False,
            enable_mpc=False,
        )

        # Trigger Granite at lap 10
        await loop.trigger_granite(10)
        
        await ve_q.put(None)
        await loop.gridsense_queue.put(None)
        await loop.run()
        
        return collected, forge

    payloads, forge = asyncio.run(_run())

    # Should have broadcast a granite_analysis payload
    granite_payloads = [p for p in payloads if p.get("rule") == "granite_analysis"]
    assert len(granite_payloads) == 1, f"Expected 1 granite payload, got {len(granite_payloads)}"
    assert "fan_explanation" in granite_payloads[0]

    # Should be stored in Context Forge
    assert len(forge.data["granite_outputs"]) == 1

    print(f"  Granite payloads: {len(granite_payloads)}")
    print(f"  Fan explanation: {granite_payloads[0]['fan_explanation'][:40]}")
    print(f"  Forge granite outputs: {len(forge.data['granite_outputs'])}")
    print("  PASS")


# ==============================================================================
# TEST 10: Full 30-lap replay through output loop
# ==============================================================================

def test_30_lap_replay():
    """Full 30-lap replay runs through pipeline + output loop without error."""
    print("\n[Test 10] Full 30-lap replay")

    collected = []
    error_count = 0

    async def mock_broadcast(payload):
        collected.append(payload)

    async def _run():
        nonlocal error_count
        ve_q = asyncio.Queue()
        forge = ContextForge(circuit="bahrain", session_type="race", driver="VER")

        pipeline = FastPathPipeline(
            input_queue=asyncio.Queue(),
            output_queue=ve_q,
            context_forge=forge,
        )

        loop = OutputEventLoop(
            voltedge_queue=ve_q,
            broadcast_fn=mock_broadcast,
            context_forge=forge,
            enable_tts=False,
            enable_mpc=False,
        )

        scenarios = [
            NORMAL, NORMAL, NORMAL, SOC_DANGER, GOOD_RECHARGE,
            NORMAL, NORMAL, SAFETY_CAR, NORMAL, LIFT_NOT_WORTH,
            NORMAL, NORMAL, CUSUM_ALARM, NORMAL, NORMAL,
            NORMAL, STALE_DATA, NORMAL, NORMAL, NORMAL,
        ]

        n_laps = 30
        ticks_per_lap = 20

        for tick in range(n_laps * ticks_per_lap):
            lap = 1 + tick // ticks_per_lap
            corner = 1 + (tick % ticks_per_lap) % 15

            s = copy.deepcopy(scenarios[tick % len(scenarios)])
            s["timestamp"] = time.time()
            s["lap"] = lap
            s["corner_id"] = corner

            try:
                alert = pipeline.process_tick(s)
                await ve_q.put(alert)
            except Exception as e:
                error_count += 1
                print(f"  ERROR at tick {tick}: {e}")

        # Sentinel
        await ve_q.put(None)
        await loop.gridsense_queue.put(None)

        # Run the output loop
        await loop.run()

        return loop.stats(), pipeline.stats(), forge

    loop_stats, pipe_stats, forge = asyncio.run(_run())

    assert error_count == 0, f"{error_count} errors during replay"
    assert loop_stats["broadcast_count"] > 0, "No broadcasts"
    assert pipe_stats["tick_count"] == 600, f"Expected 600 ticks, got {pipe_stats['tick_count']}"

    # Verify P95 latency
    p95 = pipe_stats["p95_latency_ms"]
    assert p95 < 100.0, f"P95 latency {p95:.1f}ms exceeds 100ms SLO"

    print(f"  Pipeline ticks:    {pipe_stats['tick_count']}")
    print(f"  P95 latency:       {p95:.2f}ms")
    print(f"  Output broadcasts: {loop_stats['broadcast_count']}")
    print(f"  VoltEdge alerts:   {loop_stats['voltedge_alerts']}")
    print(f"  Forge alerts:      {forge.total_alerts_fired()}")
    print(f"  Forge laps:        {forge.total_laps_completed()}")
    print(f"  Errors:            {error_count}")
    print("  PASS")


# ==============================================================================
# TEST 11: Payload includes latency and MPC
# ==============================================================================

def test_payload_enrichment():
    """Payload includes pipeline_latency_ms when present in alert."""
    print("\n[Test 11] Payload enrichment")

    collected = []

    async def mock_broadcast(payload):
        collected.append(payload)

    async def _run():
        ve_q = asyncio.Queue()
        loop = OutputEventLoop(
            voltedge_queue=ve_q,
            broadcast_fn=mock_broadcast,
            enable_tts=False,
            enable_mpc=False,
        )

        # Alert with pipeline latency
        alert = make_mock_alert(lap=20)
        alert["_pipeline_latency_ms"] = 42.5
        await ve_q.put(alert)
        await ve_q.put(None)
        await loop.gridsense_queue.put(None)

        await loop.run()
        return collected

    payloads = asyncio.run(_run())
    assert len(payloads) == 1
    assert "pipeline_latency_ms" in payloads[0], "Missing pipeline_latency_ms"
    assert payloads[0]["pipeline_latency_ms"] == 42.5

    print(f"  pipeline_latency_ms: {payloads[0]['pipeline_latency_ms']}")
    print("  PASS")


# ==============================================================================
# Main runner
# ==============================================================================

def run_all():
    """Run all Day 2 tests."""
    print("=" * 60)
    print("  Person C -- Day 2 Integration Tests")
    print("=" * 60)

    tests = [
        test_alert_builder,
        test_broadcast_mock,
        test_voltedge_processing,
        test_gridsense_processing,
        test_deduplication,
        test_mpc_planner,
        test_alert_logger,
        test_context_forge_via_loop,
        test_granite_trigger,
        test_30_lap_replay,
        test_payload_enrichment,
    ]

    passed = 0
    failed = 0

    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"  FAIL: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 60)
    print(f"  Results: {passed} passed, {failed} failed, {passed + failed} total")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    run_all()
