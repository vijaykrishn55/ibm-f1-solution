# fast_path/pipeline.py
# ─────────────────────────────────────────────────────────────────────────────
# Person B's Fast Path Pipeline
#
# Wires together: state queue → Kalman → CUSUM → Window → Rules → FAISS → Confidence
# Outputs enriched alert dicts onto an output queue for Person C.
#
# Run standalone:
#   python -m fast_path.pipeline
#
# Designed to be imported by Person C's websocket orchestrator:
#   from fast_path.pipeline import FastPathPipeline
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import asyncio
import time
from typing import Callable

from state.schema        import new_state, validate_state
from state.kalman        import BatterySOCEstimator
from state.window        import CornerWindow
from fast_path.cusum     import cusum_soc, cusum_speed
from fast_path.rules_engine import RulesEngine
from fast_path.confidence   import ConfidenceScorer
from fast_path.faiss_index  import FAISSIndex


class FastPathPipeline:
    """
    Reads raw state vectors from `input_queue` (put there by Person A).
    Processes each tick through the full fast path.
    Puts final alert dicts onto `output_queue` (consumed by Person C).

    All processing is synchronous per tick — no async I/O in the hot path.
    """

    def __init__(
        self,
        input_queue:  asyncio.Queue,
        output_queue: asyncio.Queue,
        circuit_config: dict | None = None,
        on_alert: Callable[[dict], None] | None = None,
    ):
        self.input_queue  = input_queue
        self.output_queue = output_queue
        self.on_alert     = on_alert   # optional callback for logging

        # ── Components ────────────────────────────────────────────────────
        self.kalman    = BatterySOCEstimator()
        self.window    = CornerWindow(maxlen=5)
        self.rules     = RulesEngine(config=circuit_config)
        self.scorer    = ConfidenceScorer()
        self.faiss     = FAISSIndex()

        # Metrics
        self._tick_count   = 0
        self._total_latency_ms = 0.0
        self._p95_latencies: list[float] = []

    # ── Core tick ────────────────────────────────────────────────────────────

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

        # 2. Kalman filter — enriches soc_estimated, soc_uncertainty
        self.kalman.update(raw_state)

        # 3. CUSUM — writes cusum_soc_alarm, cusum_speed_alarm into state
        cusum_soc.update(raw_state.get("energy_delta", 0.0))
        raw_state["cusum_soc_alarm"]   = cusum_soc.cumsum > cusum_soc.threshold * 0.9
        cusum_speed.update(raw_state.get("speed", 0.0))
        raw_state["cusum_speed_alarm"] = cusum_speed.cumsum > cusum_speed.threshold * 0.9

        # 4. Sliding window — track per-corner SOC/speed trends
        self.window.push_from_state(raw_state)

        # 5. Rules engine — deterministic, synchronous
        raw_alert = self.rules.evaluate(raw_state)

        # 6. FAISS similarity lookup (gracefully returns [] if not ready)
        faiss_matches = self.faiss.query(raw_state, k=3)

        # 7. Confidence scoring — may override with safe_default
        final_alert = self.scorer.score(raw_alert, raw_state, faiss_matches)

        # 8. Latency tracking
        latency_ms = (time.perf_counter() - t0) * 1000
        final_alert["_pipeline_latency_ms"] = round(latency_ms, 3)
        self._tick_count += 1
        self._total_latency_ms += latency_ms
        self._p95_latencies.append(latency_ms)
        if len(self._p95_latencies) > 200:
            self._p95_latencies.pop(0)

        return final_alert

    # ── Async run loop ───────────────────────────────────────────────────────

    async def run(self):
        """
        Main async loop. Reads from input_queue, processes, pushes to output_queue.
        Runs until cancelled.
        """
        print("[FastPath] Pipeline started — waiting for state vectors ...")
        while True:
            raw_state = await self.input_queue.get()
            try:
                alert = self.process_tick(raw_state)
                await self.output_queue.put(alert)
                if self.on_alert:
                    self.on_alert(alert)
            except Exception as e:
                print(f"[FastPath] Error processing tick: {e}")
            finally:
                self.input_queue.task_done()

    # ── Metrics ──────────────────────────────────────────────────────────────

    def p95_latency_ms(self) -> float:
        if not self._p95_latencies:
            return 0.0
        sorted_lat = sorted(self._p95_latencies)
        idx = int(len(sorted_lat) * 0.95)
        return round(sorted_lat[idx], 3)

    def avg_latency_ms(self) -> float:
        if self._tick_count == 0:
            return 0.0
        return round(self._total_latency_ms / self._tick_count, 3)

    def stats(self) -> dict:
        return {
            "tick_count":    self._tick_count,
            "avg_latency_ms": self.avg_latency_ms(),
            "p95_latency_ms": self.p95_latency_ms(),
            "faiss_ready":   self.faiss.is_ready(),
            "faiss_vectors": self.faiss.n_vectors(),
        }


# ── Standalone test — runs mock state vectors through the full pipeline ───────

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
    import copy

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

    input_q  = asyncio.Queue()
    output_q = asyncio.Queue()

    def print_alert(alert: dict):
        print(
            f"  [Alert] rule={alert['rule']:<28} "
            f"conf={alert['confidence']:.2f}  "
            f"lat={alert['_pipeline_latency_ms']:.1f}ms"
        )

    pipeline = FastPathPipeline(input_q, output_q, on_alert=print_alert)

    print("FastPathPipeline — end-to-end test")
    print("─" * 60)

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
    print("\n── Pipeline Stats ──────────────────────────────────")
    for k, v in stats.items():
        print(f"  {k}: {v}")

    # SLO check
    p95 = stats["p95_latency_ms"]
    slo = 100.0
    status = "✓ PASS" if p95 < slo else "✗ FAIL"
    print(f"\n  P95 latency {p95:.1f}ms vs SLO {slo}ms → {status}")


if __name__ == "__main__":
    asyncio.run(_main())
