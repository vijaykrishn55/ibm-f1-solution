# fast_path/pipeline.py
# -----------------------------------------------------------------------------
# Person B's Fast Path Pipeline -- Day 2 Upgrade
#
# Wires together: state queue -> Kalman -> CUSUM -> Window -> Rules -> FAISS -> Confidence
# Outputs enriched alert dicts onto an output queue for Person C.
#
# Day 2 additions:
#   - Lap boundary detection and lap summary generation
#   - Context Forge integration (stores lap summaries + alerts)
#   - Granite trigger at every 10th lap boundary
#   - GridSense alert queue merging (dual-source alerts)
#   - Source-aware metrics (separate stats for openf1 vs torcs)
#
# Run standalone:
#   python -m fast_path.pipeline
#
# Designed to be imported by Person C's websocket orchestrator:
#   from fast_path.pipeline import FastPathPipeline
# -----------------------------------------------------------------------------

from __future__ import annotations

import asyncio
import copy
import time
from typing import Callable

from state.schema           import new_state, validate_state
from state.kalman           import BatterySOCEstimator
from state.window           import CornerWindow
from fast_path.cusum        import CUSUMDetector, update_cusums
from fast_path.rules_engine import RulesEngine
from fast_path.confidence   import ConfidenceScorer
from fast_path.faiss_index  import FAISSIndex


class FastPathPipeline:
    """
    Reads raw state vectors from `input_queue` (put there by Person A).
    Processes each tick through the full fast path.
    Puts final alert dicts onto `output_queue` (consumed by Person C).

    Day 2: also tracks laps, feeds Context Forge, triggers Granite,
    and merges GridSense alerts into the output stream.
    """

    def __init__(
        self,
        input_queue:  asyncio.Queue,
        output_queue: asyncio.Queue,
        circuit_config: dict | None = None,
        on_alert: Callable[[dict], None] | None = None,
        context_forge=None,
        granite_client=None,
        gridsense_queue: asyncio.Queue | None = None,
    ):
        self.input_queue     = input_queue
        self.output_queue    = output_queue
        self.on_alert        = on_alert     # optional callback for logging
        self.context_forge   = context_forge
        self.granite_client  = granite_client
        self.gridsense_queue = gridsense_queue

        # -- Components --------------------------------------------------------
        self.kalman    = BatterySOCEstimator()
        self.window    = CornerWindow(maxlen=5)
        self.rules     = RulesEngine(config=circuit_config)
        self.scorer    = ConfidenceScorer()
        self.faiss     = FAISSIndex()

        # -- Per-instance CUSUM detectors (not module-level singletons) --------
        self.cusum_soc = CUSUMDetector(
            expected_value=-0.003, threshold=0.015, name="cusum_soc",
        )
        self.cusum_speed = CUSUMDetector(
            expected_value=0.0, threshold=5.0, name="cusum_speed",
        )

        # -- Lap tracking ------------------------------------------------------
        self._current_lap: int      = 0
        self._lap_soc_accum: list   = []    # SOC values within current lap
        self._lap_alert_count: int  = 0
        self._lap_key_decision: str = "none"

        # -- Metrics -----------------------------------------------------------
        self._tick_count         = 0
        self._total_latency_ms   = 0.0
        self._p95_latencies: list[float] = []
        self._alerts_by_rule: dict[str, int] = {}
        self._source_tick_count: dict[str, int] = {"openf1": 0, "torcs": 0, "mock": 0}

    # -- Core tick -------------------------------------------------------------

    def process_tick(self, raw_state: dict) -> dict:
        """
        Process one state vector through the full fast path.
        Returns a final alert dict. Must complete in < 100ms.
        """
        t0 = time.perf_counter()

        # 1. Validate incoming state
        warnings = validate_state(raw_state)
        if warnings:
            raw_state["_warnings"] = warnings

        # 2. Kalman filter -- enriches soc_estimated, soc_uncertainty
        self.kalman.update(raw_state)

        # 3. CUSUM -- per-instance detectors (not module singletons)
        raw_state["cusum_soc_alarm"] = self.cusum_soc.update(
            raw_state.get("energy_delta", 0.0)
        )
        raw_state["cusum_speed_alarm"] = self.cusum_speed.update(
            raw_state.get("speed", 0.0)
        )

        # 4. Sliding window -- track per-corner SOC/speed trends
        self.window.push_from_state(raw_state)

        # 5. Rules engine -- deterministic, synchronous
        raw_alert = self.rules.evaluate(raw_state)

        # 6. FAISS similarity lookup (gracefully returns [] if not ready)
        faiss_matches = self.faiss.query(raw_state, k=3)

        # 7. Confidence scoring -- may override with safe_default
        final_alert = self.scorer.score(raw_alert, raw_state, faiss_matches)

        # 8. Lap tracking + Context Forge integration
        self._track_lap(raw_state, final_alert)

        # 9. Latency tracking
        latency_ms = (time.perf_counter() - t0) * 1000
        final_alert["_pipeline_latency_ms"] = round(latency_ms, 3)
        self._tick_count += 1
        self._total_latency_ms += latency_ms
        self._p95_latencies.append(latency_ms)
        if len(self._p95_latencies) > 200:
            self._p95_latencies.pop(0)

        # 10. Per-source and per-rule counters
        src = raw_state.get("data_source", "mock")
        self._source_tick_count[src] = self._source_tick_count.get(src, 0) + 1
        rule_name = final_alert.get("rule", "unknown")
        self._alerts_by_rule[rule_name] = self._alerts_by_rule.get(rule_name, 0) + 1

        return final_alert

    # -- Lap tracking ----------------------------------------------------------

    def _track_lap(self, state: dict, alert: dict):
        """Detect lap boundaries and generate lap summaries."""
        lap = state.get("lap", 0)

        # Accumulate SOC for averaging
        self._lap_soc_accum.append(state.get("soc_estimated", 0.0))

        # Track alerts within this lap
        if alert.get("rule", "safe_default") != "safe_default":
            self._lap_alert_count += 1
            self._lap_key_decision = alert.get("recommendation", "none")

        # Detect lap boundary
        if lap > self._current_lap and self._current_lap > 0:
            self._on_lap_complete(self._current_lap)

        self._current_lap = lap

    def _on_lap_complete(self, completed_lap: int):
        """Called when a lap boundary is crossed."""
        avg_soc = 0.0
        if self._lap_soc_accum:
            avg_soc = sum(self._lap_soc_accum) / len(self._lap_soc_accum)

        lap_summary = {
            "lap":             completed_lap,
            "avg_soc":         round(avg_soc, 4),
            "alerts_this_lap": self._lap_alert_count,
            "key_decision":    self._lap_key_decision,
        }

        # Store in Context Forge if available
        if self.context_forge is not None:
            try:
                self.context_forge.add_lap_summary(lap_summary)
                print(
                    f"[FastPath] Lap {completed_lap} summary stored  "
                    f"avg_soc={avg_soc:.3f}  alerts={self._lap_alert_count}"
                )
            except Exception as e:
                print(f"[FastPath] Context Forge error: {e}")

        # Trigger Granite at every 10th lap
        if completed_lap % 10 == 0 and completed_lap > 0:
            self._trigger_granite(completed_lap)

        # Reset lap accumulators
        self._lap_soc_accum    = []
        self._lap_alert_count  = 0
        self._lap_key_decision = "none"

    def _trigger_granite(self, lap: int):
        """Schedule a Granite analysis call (non-blocking to fast path)."""
        if self.granite_client is None or self.context_forge is None:
            print(f"[FastPath] Lap {lap}: Granite trigger skipped (client not configured)")
            return

        laps = self.context_forge.get_last_n_laps(10)
        if not laps:
            return

        print(f"[FastPath] Lap {lap}: Triggering Granite analysis (last 10 laps)")

        # Schedule in background -- never block the fast path
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._run_granite(laps, lap))
        except RuntimeError:
            # No running loop (standalone test mode) -- skip
            print(f"[FastPath] Lap {lap}: Granite skipped (no event loop)")

    async def _run_granite(self, laps: list, trigger_lap: int):
        """Background Granite call -- never blocks the fast path."""
        try:
            result = await self.granite_client.analyse_laps(laps)
            if "error" not in result:
                print(
                    f"[FastPath] Granite response at lap {trigger_lap}: "
                    f"fan_explanation={result.get('fan_explanation', '')[:60]}"
                )
                if self.context_forge:
                    self.context_forge.add_granite_output(result)
                # Apply threshold updates if suggested
                updates = result.get("threshold_updates", {})
                if updates:
                    self.rules.config.update(updates)
                    if self.context_forge:
                        self.context_forge.add_threshold_update(updates)
                    print(f"[FastPath] Threshold updates applied: {updates}")
            else:
                print(f"[FastPath] Granite error: {result['error']}")
        except Exception as e:
            print(f"[FastPath] Granite call failed: {e}")

    # -- Async run loop --------------------------------------------------------

    async def run(self):
        """
        Main async loop. Reads from input_queue, processes, pushes to output_queue.
        Optionally merges GridSense alerts. Runs until cancelled.
        """
        print("[FastPath] Pipeline started -- waiting for state vectors ...")

        # If GridSense queue exists, start a merge task
        if self.gridsense_queue is not None:
            asyncio.create_task(self._gridsense_merge_loop())

        while True:
            raw_state = await self.input_queue.get()
            if raw_state is None:
                print("[FastPath] Received sentinel -- shutting down")
                break
            try:
                alert = self.process_tick(raw_state)
                await self.output_queue.put(alert)
                if self.on_alert:
                    self.on_alert(alert)
                # Store alert in Context Forge
                if self.context_forge and alert.get("rule") != "safe_default":
                    self.context_forge.add_alert(alert)
            except Exception as e:
                print(f"[FastPath] Error processing tick: {e}")
            finally:
                self.input_queue.task_done()

    async def _gridsense_merge_loop(self):
        """
        Merges GridSense alerts into the output queue.
        Runs as a background task alongside the main VoltEdge loop.
        Both VoltEdge and GridSense alerts go through the same output.
        """
        print("[FastPath] GridSense merge loop started")
        while True:
            try:
                gs_alert = await self.gridsense_queue.get()
                if gs_alert is None:
                    break
                # GridSense alerts already have the standard schema from gridsense_rules
                await self.output_queue.put(gs_alert)
                if self.on_alert:
                    self.on_alert(gs_alert)
                if self.context_forge:
                    self.context_forge.add_alert(gs_alert)
                print(
                    f"[FastPath] GridSense alert merged: "
                    f"rule={gs_alert.get('rule', '?')}"
                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[FastPath] GridSense merge error: {e}")

    # -- Reset -----------------------------------------------------------------

    def reset(self):
        """Reset all stateful components -- use between sessions."""
        self.kalman.reset()
        self.cusum_soc.reset()
        self.cusum_speed.reset()
        self.window = CornerWindow(maxlen=5)
        self._current_lap      = 0
        self._lap_soc_accum    = []
        self._lap_alert_count  = 0
        self._lap_key_decision = "none"
        self._tick_count       = 0
        self._total_latency_ms = 0.0
        self._p95_latencies    = []
        self._alerts_by_rule   = {}
        self._source_tick_count = {"openf1": 0, "torcs": 0, "mock": 0}
        print("[FastPath] Pipeline reset")

    # -- Metrics ---------------------------------------------------------------

    def p95_latency_ms(self) -> float:
        if not self._p95_latencies:
            return 0.0
        sorted_lat = sorted(self._p95_latencies)
        idx = int(len(sorted_lat) * 0.95)
        return round(sorted_lat[min(idx, len(sorted_lat) - 1)], 3)

    def avg_latency_ms(self) -> float:
        if self._tick_count == 0:
            return 0.0
        return round(self._total_latency_ms / self._tick_count, 3)

    def stats(self) -> dict:
        return {
            "tick_count":        self._tick_count,
            "avg_latency_ms":    self.avg_latency_ms(),
            "p95_latency_ms":    self.p95_latency_ms(),
            "faiss_ready":       self.faiss.is_ready(),
            "faiss_vectors":     self.faiss.n_vectors(),
            "laps_completed":    self._current_lap,
            "alerts_by_rule":    dict(self._alerts_by_rule),
            "source_ticks":      dict(self._source_tick_count),
            "cusum_soc_alarms":  self.cusum_soc.alarm_count,
            "cusum_speed_alarms": self.cusum_speed.alarm_count,
            "window_corners":    self.window.all_corner_ids(),
        }


# -- Standalone test -- runs mock state vectors through the full pipeline ------

async def _mock_producer(queue: asyncio.Queue):
    """
    Replays mock state vectors into the pipeline at 4 Hz (every 250ms).
    Uses mock_state_vectors.py so this can run without Person A's server.
    """
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from tests.mock_state_vectors import (
        NORMAL, SOC_DANGER, LIFT_NOT_WORTH, GOOD_RECHARGE,
        SAFETY_CAR, STALE_DATA, TORCS_STATE,
    )

    scenarios = [NORMAL, SOC_DANGER, LIFT_NOT_WORTH, GOOD_RECHARGE,
                 SAFETY_CAR, STALE_DATA, TORCS_STATE]

    for i in range(28):   # 28 ticks = ~7 seconds of replay
        s = copy.deepcopy(scenarios[i % len(scenarios)])
        s["timestamp"] = time.time()
        s["lap"] = 1 + i // 7
        await queue.put(s)
        await asyncio.sleep(0.05)   # faster than real-time for testing

    await queue.put(None)   # sentinel


async def _main():
    import os, sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from slow_path.context_forge import ContextForge

    input_q  = asyncio.Queue()
    output_q = asyncio.Queue()
    forge    = ContextForge(circuit="bahrain", session_type="race", driver="VER")

    def print_alert(alert: dict):
        print(
            f"  [Alert] rule={alert['rule']:<28} "
            f"conf={alert['confidence']:.2f}  "
            f"lat={alert['_pipeline_latency_ms']:.1f}ms"
        )

    pipeline = FastPathPipeline(
        input_q, output_q,
        on_alert=print_alert,
        context_forge=forge,
    )

    print("FastPathPipeline -- Day 2 end-to-end test")
    print("-" * 60)

    producer = asyncio.create_task(_mock_producer(input_q))

    # Run pipeline until producer sends sentinel None
    while True:
        raw = await input_q.get()
        if raw is None:
            break
        alert = pipeline.process_tick(raw)
        await output_q.put(alert)
        print_alert(alert)

    await producer

    # Print stats
    stats = pipeline.stats()
    print("\n-- Pipeline Stats " + "-" * 42)
    for k, v in stats.items():
        print(f"  {k}: {v}")

    # SLO check
    p95 = stats["p95_latency_ms"]
    slo = 100.0
    status = "PASS" if p95 < slo else "FAIL"
    print(f"\n  P95 latency {p95:.1f}ms vs SLO {slo}ms -> {status}")

    # Context Forge summary
    print(f"\n-- Context Forge " + "-" * 43)
    print(f"  Laps stored:  {forge.total_laps_completed()}")
    print(f"  Alerts stored: {forge.total_alerts_fired()}")


if __name__ == "__main__":
    asyncio.run(_main())
