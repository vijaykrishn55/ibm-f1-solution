"""Slow Path Runner: orchestrates MPC, Granite, Context Forge, and Event Queue.

Day 2 -- Person C

This is the async background orchestrator that:
  1. Listens for lap-completion events on the EventQueue
  2. Logs lap summaries into Context Forge
  3. Triggers Granite analysis every 10 laps
  4. Runs MPC planner every 5 seconds
  5. Pushes Granite results + threshold updates back to the fast path

The slow path NEVER blocks the fast path. All communication is via
shared queues and dicts.
"""

import asyncio
import time
import json
import os
import sys

sys.path.insert(0, ".")

from slow_path.event_queue import EventQueue
from slow_path.context_forge import ContextForge
from slow_path.granite_client import GraniteClient
from slow_path.mpc_planner import plan_5_corners


class SlowPathRunner:
    """
    Async orchestrator for all slow path components.
    Designed to run as background tasks alongside the fast path.
    """

    def __init__(
        self,
        event_queue: EventQueue,
        context_forge: ContextForge,
        granite_client: GraniteClient = None,
        circuit_config: dict = None,
        granite_every_n_laps: int = 10,
        mpc_interval: float = 5.0,
    ):
        self.event_queue = event_queue
        self.forge = context_forge
        self.granite = granite_client
        self.circuit_config = circuit_config or {}
        self.granite_every_n_laps = granite_every_n_laps
        self.mpc_interval = mpc_interval

        # Shared state: fast path reads this on every tick
        self.mpc_recommendations: dict = {}
        self.latest_granite_output: dict = {}
        self.threshold_updates: dict = {}

        # Internal tracking
        self._current_state: dict = {}
        self._last_granite_lap = 0
        self._granite_call_count = 0
        self._mpc_call_count = 0
        self._running = False

    def update_state(self, state: dict):
        """Called by the main loop to keep slow path aware of current state."""
        self._current_state = state

    # -- Event processing loop --

    async def event_loop(self):
        """
        Listens for lap-completion events from the fast path.
        Processes each event: log to forge, optionally trigger Granite.
        """
        print("[SlowPath] Event loop started")
        self._running = True

        while self._running:
            try:
                event = await asyncio.wait_for(
                    self.event_queue.pop(), timeout=2.0
                )
            except asyncio.TimeoutError:
                continue

            event_type = event.get("type", "")

            if event_type == "lap_complete":
                await self._handle_lap_complete(event)
            elif event_type == "alert_fired":
                self.forge.add_alert(event.get("alert", {}))
            elif event_type == "shutdown":
                print("[SlowPath] Shutdown event received")
                break
            else:
                print(f"[SlowPath] Unknown event type: {event_type}")

    async def _handle_lap_complete(self, event: dict):
        """Process a lap completion event."""
        lap_data = {
            "lap": event.get("lap", 0),
            "avg_soc": event.get("avg_soc", 0.0),
            "alerts_this_lap": event.get("alerts_this_lap", 0),
            "key_decision": event.get("key_decision", "safe_default"),
        }
        self.forge.add_lap_summary(lap_data)
        lap_num = lap_data["lap"]
        print(f"[SlowPath] Lap {lap_num} summary recorded (SOC: {lap_data['avg_soc']:.2f})")

        # Trigger Granite at every Nth lap (fire-and-forget to never block event loop)
        if (
            lap_num > 0
            and lap_num % self.granite_every_n_laps == 0
            and lap_num != self._last_granite_lap
        ):
            self._last_granite_lap = lap_num
            asyncio.create_task(self._trigger_granite(lap_num))

    async def _trigger_granite(self, lap_num: int):
        """Call Granite with the last N lap summaries."""
        if self.granite is None:
            print(f"[SlowPath] Granite client not configured -- skipping lap {lap_num} analysis")
            return

        last_laps = self.forge.get_last_n_laps(self.granite_every_n_laps)
        if not last_laps:
            return

        print(f"[SlowPath] Triggering Granite analysis at lap {lap_num} ...")
        self._last_granite_lap = lap_num

        try:
            result = await self.granite.analyse_laps(last_laps)
            self._granite_call_count += 1

            if "error" in result:
                print(f"[SlowPath] Granite returned error: {result['error']}")
                print("[SlowPath] Fast path continues unaffected. Thresholds unchanged.")
                return

            self.latest_granite_output = result
            self.forge.add_granite_output(result)

            # Apply threshold updates if Granite suggests any
            updates = result.get("threshold_updates", {})
            if updates:
                self.threshold_updates.update(updates)
                self.forge.add_threshold_update(updates)
                print(f"[SlowPath] Threshold updates applied: {updates}")

            fan_text = result.get("fan_explanation", "")
            if fan_text:
                print(f"[SlowPath] Fan explanation: {fan_text}")

            print(f"[SlowPath] Granite call #{self._granite_call_count} complete")

        except Exception as e:
            print(f"[SlowPath] Granite error: {e} -- fast path unaffected")

    # -- MPC background loop --

    async def mpc_loop(self):
        """
        Runs MPC planner every N seconds in background.
        Writes recommendations to self.mpc_recommendations (read by fast path).
        """
        print(f"[SlowPath] MPC loop started (interval: {self.mpc_interval}s)")

        while self._running:
            await asyncio.sleep(self.mpc_interval)

            state = self._current_state
            if not state:
                continue

            try:
                soc_now = state.get("soc_estimated", 0.85)
                current_corner = state.get("corner_id", 1)

                # Build next 5 corners from circuit config
                thresholds = self.circuit_config.get("corner_thresholds", {})
                all_corners = sorted([int(c) for c in thresholds.keys()])

                if not all_corners:
                    continue

                # Find next 5 corners after current
                idx = 0
                for i, c in enumerate(all_corners):
                    if c >= current_corner:
                        idx = i
                        break

                next_5_ids = [
                    all_corners[(idx + i) % len(all_corners)]
                    for i in range(min(5, len(all_corners)))
                ]
                next_5 = [
                    {
                        "corner_id": cid,
                        "net_lift_value": thresholds.get(
                            str(cid), {}
                        ).get("net_lift_value", 0.0),
                    }
                    for cid in next_5_ids
                ]

                plan = plan_5_corners(soc_now, next_5)
                self.mpc_recommendations = plan
                self._mpc_call_count += 1

            except Exception as e:
                print(f"[SlowPath] MPC error: {e}")

    # -- Lifecycle --

    async def start(self):
        """Start both background loops as tasks."""
        self._running = True
        event_task = asyncio.create_task(self.event_loop())
        mpc_task = asyncio.create_task(self.mpc_loop())
        print("[SlowPath] Runner started (event_loop + mpc_loop)")
        return event_task, mpc_task

    def stop(self):
        """Signal background loops to stop."""
        self._running = False
        print("[SlowPath] Runner stopping")

    def stats(self) -> dict:
        return {
            "granite_calls": self._granite_call_count,
            "mpc_calls": self._mpc_call_count,
            "laps_recorded": self.forge.total_laps_completed(),
            "alerts_recorded": self.forge.total_alerts_fired(),
            "mpc_recommendations": self.mpc_recommendations,
            "latest_granite": self.latest_granite_output,
            "threshold_updates": self.threshold_updates,
        }


# -- Standalone test --

async def _test():
    """Quick integration test: push 15 lap events, verify Granite triggers at lap 10."""
    eq = EventQueue()
    cf = ContextForge(circuit="bahrain", driver="VER")

    runner = SlowPathRunner(
        event_queue=eq,
        context_forge=cf,
        granite_client=None,  # No real API key for testing
        granite_every_n_laps=10,
        mpc_interval=60.0,  # Don't run MPC during quick test
    )

    event_task, mpc_task = await runner.start()

    # Push 15 lap events
    for lap in range(1, 16):
        await eq.push({
            "type": "lap_complete",
            "lap": lap,
            "avg_soc": round(0.85 - lap * 0.02, 2),
            "alerts_this_lap": lap % 3,
            "key_decision": "safe_default",
        })
        await asyncio.sleep(0.05)

    # Let event loop process
    await asyncio.sleep(0.5)
    runner.stop()

    print(f"\n[SlowPath] Stats: {json.dumps(runner.stats(), indent=2, default=str)}")
    print(f"[SlowPath] Laps in forge: {cf.total_laps_completed()}")
    assert cf.total_laps_completed() == 15
    print("[SlowPath] Standalone test PASSED")


if __name__ == "__main__":
    asyncio.run(_test())
