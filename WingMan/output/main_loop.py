# output/main_loop.py
# -----------------------------------------------------------------------------
# Person C -- Day 2: Full Event Loop
#
# The main orchestration loop that ties the entire output pipeline together:
#   - Reads alerts from VoltEdge queue (Person B's output)
#   - Reads alerts from GridSense queue (Person E's output)
#   - Runs each alert through alert_builder for uniform payload schema
#   - Broadcasts to all WebSocket clients
#   - Triggers TTS (non-blocking, skips braking zones)
#   - Runs MPC planner in background (every 5 seconds)
#   - Logs all alerts to file + Context Forge
#   - Triggers Granite at lap boundaries
#
# Usage:
#   python -m output.main_loop                     # mock mode
#   python -m output.main_loop --mode replay       # 30-lap replay
#   python -m output.main_loop --mode live         # live WS server
# -----------------------------------------------------------------------------

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from output.alert_builder import build_payload
from slow_path.context_forge import ContextForge
from slow_path.mpc_planner import plan_5_corners


# -- Alert file logger ---------------------------------------------------------

class AlertLogger:
    """Appends every alert to a JSONL file for post-race review."""

    def __init__(self, filepath: str = None):
        if filepath is None:
            log_dir = os.path.join(os.path.dirname(__file__), "..", "data")
            os.makedirs(log_dir, exist_ok=True)
            filepath = os.path.join(log_dir, "alerts.jsonl")
        self.filepath = filepath

    def log(self, payload: dict):
        try:
            with open(self.filepath, "a") as f:
                f.write(json.dumps(payload, default=str) + "\n")
        except Exception as e:
            print(f"[AlertLogger] Write error: {e}")

    def read_all(self) -> list:
        if not os.path.exists(self.filepath):
            return []
        entries = []
        with open(self.filepath) as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries

    def clear(self):
        if os.path.exists(self.filepath):
            os.remove(self.filepath)


# -- TTS wrapper (safe import) -------------------------------------------------

class SafeTTS:
    """Wraps TTS so the system runs even if pyttsx3/gTTS is not installed."""

    def __init__(self):
        self._available = False
        try:
            import pyttsx3
            self._engine = pyttsx3.init()
            self._engine.setProperty("rate", 160)
            self._available = True
        except Exception:
            print("[TTS] pyttsx3 not available -- TTS disabled")

    async def speak(self, text: str, state: dict):
        if not self._available:
            return
        if state.get("brake") is True:
            print(f"[TTS] Skipped (braking): {text[:40]}")
            return
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._speak_sync, text)
        except Exception as e:
            print(f"[TTS] Error: {e}")

    def _speak_sync(self, text: str):
        self._engine.say(text)
        self._engine.runAndWait()


# -- Main Event Loop -----------------------------------------------------------

class OutputEventLoop:
    """
    Consumes alerts from both VoltEdge and GridSense queues,
    builds payloads, broadcasts via WebSocket, triggers TTS + MPC.
    """

    def __init__(
        self,
        voltedge_queue: asyncio.Queue,
        gridsense_queue: asyncio.Queue | None = None,
        broadcast_fn=None,
        context_forge: ContextForge | None = None,
        granite_client=None,
        circuit_config: dict | None = None,
        enable_tts: bool = False,
        enable_mpc: bool = True,
    ):
        self.voltedge_queue  = voltedge_queue
        self.gridsense_queue = gridsense_queue or asyncio.Queue()
        self.broadcast_fn    = broadcast_fn or self._default_broadcast
        self.context_forge   = context_forge
        self.granite_client  = granite_client
        self.circuit_config  = circuit_config or {}
        self.enable_tts      = enable_tts
        self.enable_mpc      = enable_mpc

        self.alert_logger = AlertLogger()
        self.tts = SafeTTS() if enable_tts else None

        # -- Metrics -----------------------------------------------------------
        self._broadcast_count = 0
        self._voltedge_count  = 0
        self._gridsense_count = 0
        self._tts_count       = 0
        self._last_payload: dict = {}
        self._last_state: dict   = {}
        self._last_alert_id: str = ""
        self._mpc_plan: dict     = {}

    # -- Queue merging ---------------------------------------------------------

    async def run(self):
        """Start the event loop. Reads from both queues concurrently."""
        print("[OutputLoop] Starting event loop ...")
        tasks = [
            asyncio.create_task(self._consume_voltedge()),
            asyncio.create_task(self._consume_gridsense()),
        ]
        if self.enable_mpc:
            tasks.append(asyncio.create_task(self._mpc_background()))

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            print("[OutputLoop] Event loop cancelled")

    async def _consume_voltedge(self):
        """Main VoltEdge alert consumer."""
        while True:
            alert = await self.voltedge_queue.get()
            if alert is None:
                print("[OutputLoop] VoltEdge sentinel received")
                break
            self._voltedge_count += 1
            await self._process_alert(alert, source="voltedge")

    async def _consume_gridsense(self):
        """GridSense alert consumer."""
        while True:
            try:
                alert = await asyncio.wait_for(self.gridsense_queue.get(), timeout=1.0)
                if alert is None:
                    print("[OutputLoop] GridSense sentinel received")
                    break
                self._gridsense_count += 1
                await self._process_alert(alert, source="gridsense")
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    async def _process_alert(self, alert: dict, source: str):
        """
        Process a single alert:
          1. Build payload via alert_builder
          2. Deduplication check
          3. Broadcast to WebSocket
          4. Log to file + Context Forge
          5. Trigger TTS (if enabled)
          6. Attach MPC plan if available
        """
        # Build a minimal state dict from the alert itself
        state = {
            "soc_estimated": alert.get("soc_estimated", 0.0),
            "corner_id":     alert.get("corner_id", 0),
            "lap":           alert.get("lap", 0),
            "timestamp":     alert.get("timestamp", time.time()),
            "data_source":   alert.get("data_source", "mock"),
            "brake":         alert.get("brake", False),
        }

        # Ensure source_module is set
        if "source_module" not in alert:
            alert["source_module"] = source

        # Build payload
        payload = build_payload(alert, state)

        # Attach MPC plan if available
        if self._mpc_plan:
            payload["mpc_plan"] = self._mpc_plan

        # Attach latency if present
        if "_pipeline_latency_ms" in alert:
            payload["pipeline_latency_ms"] = alert["_pipeline_latency_ms"]

        # Deduplication
        alert_id = payload.get("alert_id", "")
        if alert_id and alert_id == self._last_alert_id:
            return
        self._last_alert_id = alert_id

        # Broadcast
        await self.broadcast_fn(payload)
        self._broadcast_count += 1
        self._last_payload = payload

        # Log
        self.alert_logger.log(payload)

        # Context Forge
        if self.context_forge and payload.get("rule") != "safe_default":
            self.context_forge.add_alert(payload)

        # TTS (non-blocking)
        if self.tts and payload.get("rule") != "safe_default":
            self._tts_count += 1
            asyncio.create_task(
                self.tts.speak(payload.get("recommendation", ""), state)
            )

        # Log to console (non-safe only)
        if payload.get("rule") != "safe_default":
            print(
                f"  [Broadcast #{self._broadcast_count:04d}] "
                f"src={source:<10} rule={payload['rule']:<28} "
                f"conf={payload['confidence']:.2f}  lap={payload['lap']}"
            )

    async def _default_broadcast(self, payload: dict):
        """Default broadcast -- just prints. In production, calls WebSocket."""
        pass

    # -- MPC background --------------------------------------------------------

    async def _mpc_background(self):
        """Runs MPC planner every 5 seconds in background."""
        while True:
            await asyncio.sleep(5.0)
            try:
                if not self._last_payload:
                    continue
                soc = self._last_payload.get("soc_estimated", 0.85)
                corner = self._last_payload.get("corner_id", 1)

                # Build next 5 corners from circuit config
                thresholds = self.circuit_config.get("corner_thresholds", {})
                all_corners = sorted([int(c) for c in thresholds.keys()])
                if not all_corners:
                    # Use default corners 1-15
                    all_corners = list(range(1, 16))

                idx = 0
                for i, c in enumerate(all_corners):
                    if c >= corner:
                        idx = i
                        break

                next_5_ids = [all_corners[(idx + i) % len(all_corners)] for i in range(5)]
                next_5 = [
                    {
                        "corner_id": cid,
                        "net_lift_value": thresholds.get(str(cid), {}).get(
                            "net_lift_value", 0.05
                        ),
                    }
                    for cid in next_5_ids
                ]

                self._mpc_plan = plan_5_corners(soc, next_5)
                print(f"[MPC] SOC={soc:.2f}  Plan: {self._mpc_plan}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[MPC] Error: {e}")

    # -- Granite trigger -------------------------------------------------------

    async def trigger_granite(self, lap: int):
        """
        Called externally (by the pipeline) when a lap boundary is crossed.
        Fires Granite at every 10th lap.
        """
        if lap % 10 != 0 or lap == 0:
            return
        if self.granite_client is None or self.context_forge is None:
            print(f"[Granite] Lap {lap}: Granite trigger skipped (not configured)")
            return

        laps = self.context_forge.get_last_n_laps(10)
        if not laps:
            return

        print(f"[Granite] Lap {lap}: Triggering Granite analysis ...")
        try:
            result = await self.granite_client.analyse_laps(laps)
            if "error" not in result:
                fan_text = result.get("fan_explanation", "")[:80]
                print(f"[Granite] Response: {fan_text}")

                # Store in Context Forge
                self.context_forge.add_granite_output(result)

                # Broadcast Granite update to UI
                granite_payload = {
                    "alert_id":        f"granite-lap-{lap}",
                    "rule":            "granite_analysis",
                    "recommendation":  result.get("strategy_note", ""),
                    "reason":          f"Granite analysis after lap {lap}",
                    "priority":        5,
                    "confidence":      0.90,
                    "soc_estimated":   self._last_payload.get("soc_estimated", 0.0),
                    "corner_id":       0,
                    "lap":             lap,
                    "timestamp":       time.time(),
                    "fan_explanation": fan_text,
                    "data_source":     "granite",
                    "source_module":   "voltedge",
                }
                await self.broadcast_fn(granite_payload)

                # Apply threshold updates
                updates = result.get("threshold_updates", {})
                if updates:
                    self.context_forge.add_threshold_update(updates)
                    print(f"[Granite] Threshold updates: {updates}")
            else:
                print(f"[Granite] Error: {result['error']}")
        except Exception as e:
            print(f"[Granite] Call failed: {e}")

    # -- Stats -----------------------------------------------------------------

    def stats(self) -> dict:
        return {
            "broadcast_count":   self._broadcast_count,
            "voltedge_alerts":   self._voltedge_count,
            "gridsense_alerts":  self._gridsense_count,
            "tts_triggered":     self._tts_count,
            "mpc_plan":          self._mpc_plan,
            "last_rule":         self._last_payload.get("rule", "none"),
            "last_confidence":   self._last_payload.get("confidence", 0),
        }


# -- Standalone mock replay test -----------------------------------------------

async def _mock_replay():
    """
    Run a standalone mock replay to test the output loop.
    Produces mock alerts and feeds them through the full output pipeline.
    """
    from tests.mock_state_vectors import (
        NORMAL, SOC_DANGER, LIFT_NOT_WORTH, GOOD_RECHARGE,
        SAFETY_CAR, STALE_DATA, TORCS_STATE, CUSUM_ALARM,
    )
    from fast_path.pipeline import FastPathPipeline

    print("=" * 60)
    print("  Person C -- Day 2 Output Loop Mock Replay")
    print("=" * 60)

    input_q     = asyncio.Queue()
    output_q    = asyncio.Queue()
    gs_q        = asyncio.Queue()
    forge       = ContextForge(circuit="bahrain", session_type="race", driver="VER")

    # Build fast path pipeline
    pipeline = FastPathPipeline(
        input_queue=input_q,
        output_queue=output_q,
        context_forge=forge,
    )

    # Build output loop
    loop = OutputEventLoop(
        voltedge_queue=output_q,
        gridsense_queue=gs_q,
        context_forge=forge,
        enable_tts=False,
        enable_mpc=False,
    )

    # Clear previous log
    loop.alert_logger.clear()

    # Produce mock ticks through pipeline
    scenarios = [
        NORMAL, NORMAL, SOC_DANGER, NORMAL, GOOD_RECHARGE,
        NORMAL, SAFETY_CAR, NORMAL, LIFT_NOT_WORTH, NORMAL,
        CUSUM_ALARM, NORMAL, TORCS_STATE, NORMAL, STALE_DATA,
        NORMAL, NORMAL, NORMAL, NORMAL, NORMAL,
    ]

    for lap in range(1, 16):
        for tick_in_lap in range(len(scenarios)):
            idx = (lap * len(scenarios) + tick_in_lap) % len(scenarios)
            s = copy.deepcopy(scenarios[idx])
            s["timestamp"] = time.time()
            s["lap"] = lap
            s["corner_id"] = 1 + tick_in_lap % 15

            alert = pipeline.process_tick(s)
            await output_q.put(alert)

    # Put sentinel
    await output_q.put(None)

    # Inject a mock GridSense alert
    gs_alert = {
        "alert_id":       "gs-test-001",
        "rule":           "gridsense_understeer",
        "recommendation": "Increase front wing angle",
        "reason":         "Driver radio: pushing a lot on entry",
        "priority":       8,
        "confidence":     0.75,
        "soc_estimated":  0.65,
        "corner_id":      4,
        "lap":            12,
        "timestamp":      time.time(),
        "fan_explanation": "",
        "source_module":  "gridsense",
    }
    await gs_q.put(gs_alert)
    await gs_q.put(None)

    # Run event loop
    await loop.run()

    # Print stats
    stats = loop.stats()
    print("\n" + "=" * 60)
    print("  OUTPUT LOOP STATS")
    print("=" * 60)
    for k, v in stats.items():
        print(f"  {k}: {v}")

    # Verify log file
    logged = loop.alert_logger.read_all()
    print(f"\n  Alerts logged to file: {len(logged)}")
    print(f"  Context Forge alerts:  {forge.total_alerts_fired()}")
    print(f"  Context Forge laps:    {forge.total_laps_completed()}")

    return stats


if __name__ == "__main__":
    asyncio.run(_mock_replay())
