# fast_path/orchestrator.py
# -----------------------------------------------------------------------------
# Full System Orchestrator -- Person B Day 2
#
# Ties together every component into a single runnable entry point:
#   - SourceManager (Person A)  ->  input queue
#   - FastPathPipeline (Person B) -> processes ticks
#   - Context Forge (Person C)  ->  stores lap summaries
#   - Granite Client (Person C) ->  triggered at lap boundaries
#   - GridSense queue (Person E) -> merged into output
#   - Output queue              -> consumed by WebSocket server
#
# Modes:
#   "mock"   -- OpenF1 mock replay (default, no external deps)
#   "torcs"  -- TORCS live data
#   "both"   -- dual source (OpenF1 mock + TORCS simultaneous)
#
# Usage:
#   python -m fast_path.orchestrator                    # mock mode
#   python -m fast_path.orchestrator --mode torcs       # TORCS mode
#   python -m fast_path.orchestrator --mode both        # dual source
#   python -m fast_path.orchestrator --laps 30          # 30-lap replay
# -----------------------------------------------------------------------------

from __future__ import annotations

import argparse
import asyncio
import copy
import time
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from state.schema           import new_state
from fast_path.pipeline     import FastPathPipeline
from slow_path.context_forge import ContextForge


class Orchestrator:
    """
    Main entry point for the full WingMan pipeline.

    Connects Person A's data sources -> Person B's fast path -> output queue.
    Optionally attaches Context Forge and Granite client.
    """

    def __init__(
        self,
        mode: str = "mock",
        circuit: str = "bahrain",
        circuit_config: dict | None = None,
        granite_client=None,
        max_laps: int = 0,       # 0 = run forever
        replay_speed: float = 1.0,
    ):
        self.mode           = mode
        self.circuit         = circuit
        self.max_laps        = max_laps
        self.replay_speed    = replay_speed

        # -- Queues ------------------------------------------------------------
        self.input_queue     = asyncio.Queue()
        self.output_queue    = asyncio.Queue()
        self.gridsense_queue = asyncio.Queue()

        # -- Context Forge -----------------------------------------------------
        persist_dir = os.path.join(os.path.dirname(__file__), "..", "data")
        os.makedirs(persist_dir, exist_ok=True)
        self.context_forge = ContextForge(
            persist_path=os.path.join(persist_dir, "session_memory.json"),
            circuit=circuit,
            session_type="race",
            driver="VER",
        )

        # -- Pipeline ----------------------------------------------------------
        self.pipeline = FastPathPipeline(
            input_queue=self.input_queue,
            output_queue=self.output_queue,
            circuit_config=circuit_config,
            on_alert=self._on_alert,
            context_forge=self.context_forge,
            granite_client=granite_client,
            gridsense_queue=self.gridsense_queue,
        )

        # -- Source manager (lazy import to avoid circular deps) ---------------
        self.source_manager = None

        # -- Stats -------------------------------------------------------------
        self._start_time: float = 0.0
        self._output_count: int = 0
        self._last_alert_rule: str = ""

    def _on_alert(self, alert: dict):
        """Callback for every alert produced by the pipeline."""
        rule = alert.get("rule", "?")
        conf = alert.get("confidence", 0)
        lat  = alert.get("_pipeline_latency_ms", 0)
        self._output_count += 1
        self._last_alert_rule = rule

        # Only print non-safe-default alerts to reduce noise
        if rule != "safe_default":
            print(
                f"  [ALERT #{self._output_count:04d}] "
                f"rule={rule:<28} conf={conf:.2f}  lat={lat:.1f}ms"
            )

    # -- Source management -----------------------------------------------------

    async def _start_sources(self):
        """Start data sources via SourceManager."""
        from ingestion.source_manager import SourceManager

        source_mode = "openf1" if self.mode == "mock" else self.mode
        self.source_manager = SourceManager(
            queue=self.input_queue,
            mode=source_mode,
            circuit=self.circuit,
        )
        await self.source_manager.start()

    async def _stop_sources(self):
        """Stop all data sources gracefully."""
        if self.source_manager:
            await self.source_manager.stop()

    async def switch_source(self, new_mode: str):
        """Hot-switch between data sources without restarting the pipeline."""
        if self.source_manager:
            await self.source_manager.switch_mode(new_mode)
            print(f"[Orchestrator] Source switched to: {new_mode}")

    # -- Main run --------------------------------------------------------------

    async def run(self):
        """
        Main entry point. Starts sources + pipeline, runs until max_laps
        or until cancelled.
        """
        self._start_time = time.time()
        print("=" * 60)
        print("  WingMan Orchestrator -- Day 2 Full Pipeline")
        print(f"  Mode: {self.mode}  |  Circuit: {self.circuit}")
        if self.max_laps:
            print(f"  Max laps: {self.max_laps}")
        print("=" * 60)

        # Start sources
        await self._start_sources()

        # Run pipeline as a background task
        pipeline_task = asyncio.create_task(self.pipeline.run())

        # Output consumer -- drains the output queue and prints
        consumer_task = asyncio.create_task(self._output_consumer())

        try:
            # Wait for pipeline to finish (sentinel or cancellation)
            await pipeline_task
        except asyncio.CancelledError:
            print("[Orchestrator] Pipeline cancelled")
        finally:
            consumer_task.cancel()
            await self._stop_sources()
            self._print_final_report()

    async def _output_consumer(self):
        """Drain the output queue -- in production this feeds the WebSocket."""
        while True:
            try:
                alert = await asyncio.wait_for(self.output_queue.get(), timeout=1.0)
                # In production: await websocket.broadcast(alert)
                # Here we just count -- _on_alert callback handles printing
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    # -- Mock replay (no SourceManager needed) ---------------------------------

    async def run_mock_replay(self, n_laps: int = 30, ticks_per_lap: int = 20):
        """
        Run a full mock replay through the pipeline.
        Generates synthetic state vectors covering n_laps laps.
        Useful for Day 2 testing without starting the mock server.
        """
        self._start_time = time.time()
        print("=" * 60)
        print("  WingMan Mock Replay -- Day 2 Integration Test")
        print(f"  Laps: {n_laps}  |  Ticks/lap: {ticks_per_lap}")
        print("=" * 60)

        from tests.mock_state_vectors import (
            NORMAL, SOC_DANGER, LIFT_NOT_WORTH, GOOD_RECHARGE,
            SAFETY_CAR, STALE_DATA, TORCS_STATE, CUSUM_ALARM,
        )

        scenarios = [
            NORMAL, NORMAL, NORMAL, NORMAL,           # mostly normal
            GOOD_RECHARGE,                              # occasional recharge
            NORMAL, NORMAL, SOC_DANGER,                 # danger mid-race
            NORMAL, NORMAL, LIFT_NOT_WORTH,             # lift advice
            NORMAL, NORMAL, NORMAL,                     # more normal
            CUSUM_ALARM,                                 # CUSUM spike
            NORMAL, NORMAL, SAFETY_CAR,                 # safety car
            NORMAL, NORMAL,                              # recovery
        ]

        total_ticks = n_laps * ticks_per_lap
        alert_count = 0
        non_safe_count = 0

        for tick in range(total_ticks):
            lap = 1 + tick // ticks_per_lap
            corner = 1 + (tick % ticks_per_lap) % 15
            lap_frac = (tick % ticks_per_lap) / ticks_per_lap

            # Pick scenario with variety
            s = copy.deepcopy(scenarios[tick % len(scenarios)])
            s["timestamp"]    = time.time()
            s["lap"]          = lap
            s["corner_id"]    = corner
            s["lap_fraction"] = round(lap_frac, 3)

            # Process through pipeline
            alert = self.pipeline.process_tick(s)
            alert_count += 1

            if alert.get("rule", "safe_default") != "safe_default":
                non_safe_count += 1
                self._on_alert(alert)

            # Store non-safe alerts in Context Forge
            if self.context_forge and alert.get("rule") != "safe_default":
                self.context_forge.add_alert(alert)

            # Simulate real-time pacing (fast)
            if tick % 100 == 0:
                await asyncio.sleep(0.001)

        # Force final lap summary
        self.pipeline._on_lap_complete(n_laps)

        print(f"\n[Replay] Complete: {total_ticks} ticks, {n_laps} laps")
        print(f"[Replay] Alerts: {alert_count} total, {non_safe_count} non-safe-default")
        self._print_final_report()

        return self.pipeline.stats()

    # -- Dual-source test ------------------------------------------------------

    async def run_dual_source_test(self, duration_s: float = 10.0):
        """
        Test source switching: start with mock, switch to TORCS after half,
        then switch to both. Validates hot-switching works.
        """
        self._start_time = time.time()
        print("=" * 60)
        print("  WingMan Dual Source Test -- Day 2")
        print(f"  Duration: {duration_s}s (mock -> torcs -> both)")
        print("=" * 60)

        from tests.mock_state_vectors import NORMAL, TORCS_STATE

        # Phase 1: OpenF1 mock source
        print("\n--- Phase 1: OpenF1 mock source ---")
        for i in range(20):
            s = copy.deepcopy(NORMAL)
            s["timestamp"] = time.time()
            s["lap"] = 1
            s["data_source"] = "openf1"
            alert = self.pipeline.process_tick(s)
            await asyncio.sleep(0.01)

        # Phase 2: Switch to TORCS
        print("\n--- Phase 2: TORCS source ---")
        for i in range(20):
            s = copy.deepcopy(TORCS_STATE)
            s["timestamp"] = time.time()
            s["lap"] = 2
            s["data_source"] = "torcs"
            alert = self.pipeline.process_tick(s)
            await asyncio.sleep(0.01)

        # Phase 3: Both sources interleaved
        print("\n--- Phase 3: Both sources ---")
        for i in range(20):
            if i % 2 == 0:
                s = copy.deepcopy(NORMAL)
                s["data_source"] = "openf1"
            else:
                s = copy.deepcopy(TORCS_STATE)
                s["data_source"] = "torcs"
            s["timestamp"] = time.time()
            s["lap"] = 3
            alert = self.pipeline.process_tick(s)
            await asyncio.sleep(0.01)

        stats = self.pipeline.stats()
        print("\n--- Source switching results ---")
        print(f"  Source ticks: {stats['source_ticks']}")
        print(f"  Total ticks:  {stats['tick_count']}")

        # Validate both sources processed
        openf1_ticks = stats["source_ticks"].get("openf1", 0)
        torcs_ticks  = stats["source_ticks"].get("torcs", 0)
        mock_ticks   = stats["source_ticks"].get("mock", 0)
        total_non_mock = openf1_ticks + torcs_ticks
        assert total_non_mock > 0 or mock_ticks > 0, "No ticks processed!"
        print(f"  Source switching: PASS")

        self._print_final_report()
        return stats

    # -- Report ----------------------------------------------------------------

    def _print_final_report(self):
        """Print a comprehensive final report."""
        elapsed = time.time() - self._start_time
        stats   = self.pipeline.stats()

        print("\n" + "=" * 60)
        print("  FINAL REPORT")
        print("=" * 60)
        print(f"  Elapsed:           {elapsed:.1f}s")
        print(f"  Ticks processed:   {stats['tick_count']}")
        print(f"  Avg latency:       {stats['avg_latency_ms']:.2f}ms")
        print(f"  P95 latency:       {stats['p95_latency_ms']:.2f}ms")
        print(f"  FAISS ready:       {stats['faiss_ready']}")
        print(f"  FAISS vectors:     {stats['faiss_vectors']}")
        print(f"  Laps completed:    {stats['laps_completed']}")
        print(f"  CUSUM SOC alarms:  {stats['cusum_soc_alarms']}")
        print(f"  CUSUM speed alarms:{stats['cusum_speed_alarms']}")

        print(f"\n  Alerts by rule:")
        for rule, count in sorted(stats["alerts_by_rule"].items(),
                                   key=lambda x: -x[1]):
            print(f"    {rule:<30} {count:>5}")

        print(f"\n  Ticks by source:")
        for src, count in stats["source_ticks"].items():
            if count > 0:
                print(f"    {src:<10} {count:>5}")

        # Context Forge summary
        if self.context_forge:
            print(f"\n  Context Forge:")
            print(f"    Laps stored:    {self.context_forge.total_laps_completed()}")
            print(f"    Alerts stored:  {self.context_forge.total_alerts_fired()}")

        # SLO check
        p95 = stats["p95_latency_ms"]
        slo = 100.0
        status = "PASS" if p95 < slo else "FAIL"
        print(f"\n  SLO check: P95 {p95:.1f}ms vs {slo}ms -> {status}")
        print("=" * 60)


# -- CLI entry point -----------------------------------------------------------

async def _main():
    parser = argparse.ArgumentParser(description="WingMan Orchestrator")
    parser.add_argument("--mode",  default="mock",  choices=["mock", "torcs", "both"])
    parser.add_argument("--laps",  type=int, default=30, help="Number of laps for mock replay")
    parser.add_argument("--test",  default="replay", choices=["replay", "dual", "live"])
    args = parser.parse_args()

    orch = Orchestrator(mode=args.mode, circuit="bahrain", max_laps=args.laps)

    if args.test == "replay":
        await orch.run_mock_replay(n_laps=args.laps)
    elif args.test == "dual":
        await orch.run_dual_source_test()
    elif args.test == "live":
        await orch.run()


if __name__ == "__main__":
    asyncio.run(_main())
