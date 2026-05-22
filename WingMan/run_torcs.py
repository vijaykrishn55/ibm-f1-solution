"""
run_torcs.py  — Run from WingMan/ root:
    python run_torcs.py

Full WingMan system connected to a live TORCS game session.

Architecture (Thread-based — avoids asyncio + blocking UDP conflict):
  Thread 1  TORCS Drive Loop  — synchronous snakeoil client, ~60 Hz
  Thread 2  WingMan Asyncio   — pipeline + alert consumer + WebSocket

The drive thread is fully synchronous: get_servers_input → drive_autopilot →
respond_to_server, as fast as TORCS sends frames.  It drops state vectors
onto a thread-safe Queue.  The asyncio thread polls that queue, runs the
fast-path pipeline, applies alerts to control_mapper (which the drive thread
reads each tick), and broadcasts to WebSocket clients.

Prerequisites:
  1. TORCS running with scr_server robot driver (waiting on blue screen)
  2. WebSocket server running:  python run_websocket_server.py  (port 8001)
  3. gym_torcs (snakeoil3_gym.py) at TORCS_PATH below

Usage:
  python run_torcs.py
"""
import sys
import os
import math
import time
import threading
import queue as stdlib_queue

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

TORCS_PATH = r"E:\ibm\ibm f1 solution\gym_torcs"
if TORCS_PATH not in sys.path:
    sys.path.insert(0, TORCS_PATH)

import asyncio
import httpx

from fast_path.pipeline      import FastPathPipeline
from ingestion               import control_mapper
from ingestion.torcs_adapter import torcs_to_state, drive_autopilot

# ── Config ────────────────────────────────────────────────────────────────────

WS_SERVER_URL  = "http://localhost:8001"
TORCS_PORT     = 3001
PRINT_INTERVAL = 100   # print TORCS status every N drive ticks

# Thread-safe bridge: drive thread → asyncio pipeline
_state_bridge: stdlib_queue.Queue = stdlib_queue.Queue(maxsize=200)

# ── Thread 1: TORCS Drive Loop (pure synchronous) ─────────────────────────────

def _torcs_drive_thread():
    """
    Runs the TORCS drive loop on a dedicated thread.

    This MUST be synchronous — get_servers_input() is a blocking UDP recv
    that would freeze the asyncio event loop if run in a coroutine.

    Each tick:
      1. get_servers_input()        — blocks until TORCS sends sensor frame
      2. torcs_to_state()           — convert to WingMan state vector
      3. drive_autopilot()          — compute steer/accel/gear
         (uses control_mapper.get_modifier() — updated by asyncio thread)
      4. respond_to_server()        — send controls to TORCS  ← CRITICAL
      5. _state_bridge.put_nowait() — hand state to pipeline (non-blocking)
    """
    import snakeoil3_gym as snakeoil

    while True:   # reconnect loop
        try:
            print("[TORCS] Connecting to TORCS on port 3001 ...")
            C = snakeoil.Client(p=TORCS_PORT)
            print(f"[TORCS] Connected! maxSteps={C.maxSteps}")

            prev_soc = 0.85
            tick     = 0

            for _step in range(C.maxSteps, 0, -1):

                # 1. Read sensor frame (blocking — safe on this thread)
                C.get_servers_input()
                sensors = C.S.d

                # 2. Convert to WingMan state
                state    = torcs_to_state(sensors, prev_soc=prev_soc)
                prev_soc = state.get("soc_raw", prev_soc)

                # 3. Drive — applies current WingMan modifier
                modifier = control_mapper.get_modifier()
                drive_autopilot(C, accel_modifier=modifier)

                # 4. Send controls — MUST happen every tick
                C.respond_to_server()

                # 5. Feed state to pipeline (never block drive loop)
                try:
                    _state_bridge.put_nowait(state)
                except stdlib_queue.Full:
                    pass   # pipeline lagging — safe to drop

                tick += 1
                if tick % PRINT_INTERVAL == 0:
                    speed = sensors.get("speedX",  0)
                    gear  = C.R.d.get("gear",      1)
                    steer = C.R.d.get("steer",     0)
                    accel = C.R.d.get("accel",     0)
                    print(
                        f"[TORCS] tick={tick:5d}  "
                        f"speed={speed:6.1f} km/h  gear={gear}  "
                        f"steer={steer:+.3f}  accel={accel:.2f}  "
                        f"modifier={modifier:.2f}"
                    )

            C.shutdown()
            print("[TORCS] Max steps reached — reconnecting ...")

        except Exception as e:
            print(f"[TORCS] Drive error: {e}")
            print("[TORCS] Reconnecting in 3 seconds ...")
            time.sleep(3.0)


# ── Thread 2: WingMan asyncio tasks ──────────────────────────────────────────

async def _push_to_ws_server(alert: dict):
    """Post alert to WebSocket server. Falls back silently if not running."""
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            await client.post(f"{WS_SERVER_URL}/internal/alert", json=alert)
    except Exception:
        pass


async def _pipeline_loop(pipeline: FastPathPipeline, alert_queue: asyncio.Queue):
    """
    Polls the thread-safe _state_bridge for state vectors from the drive thread,
    runs the fast-path pipeline, and puts alerts onto the asyncio alert_queue.
    """
    print("[Pipeline] Waiting for TORCS state vectors ...")
    tick = 0
    while True:
        # Non-blocking poll so we yield back to the event loop regularly
        try:
            state = _state_bridge.get_nowait()
        except stdlib_queue.Empty:
            await asyncio.sleep(0.01)   # 10 ms — low CPU, fast enough
            continue

        try:
            alert = pipeline.process_tick(state)
            await alert_queue.put(alert)
            tick += 1

            if tick % 20 == 0:
                stats = pipeline.stats()
                print(
                    f"[Pipeline] tick={tick:>5}  "
                    f"avg={stats['avg_latency_ms']:.1f}ms  "
                    f"p95={stats['p95_latency_ms']:.1f}ms  "
                    f"faiss={'OK' if stats['faiss_ready'] else 'EMPTY'}"
                )
        except Exception as e:
            print(f"[Pipeline] Error on tick {tick}: {e}")


async def _alert_consumer(alert_queue: asyncio.Queue):
    """
    Consumes alerts from the pipeline.
    Applies each alert to control_mapper (drive thread reads this every tick).
    Forwards alerts to WebSocket server for UI display.
    """
    _last_rule = None
    while True:
        alert = await alert_queue.get()
        try:
            # Update the shared throttle modifier (drive thread reads this)
            control_mapper.apply(alert)

            rule     = alert.get("rule",                  "?")
            conf     = alert.get("confidence",             0.0)
            speed    = alert.get("speed",                  0.0)
            soc      = alert.get("soc_estimated",          0.0)
            corner   = alert.get("corner_id",              0)
            lat      = alert.get("_pipeline_latency_ms",   0.0)
            src      = alert.get("data_source",            "torcs")
            rec      = alert.get("recommendation",         "")
            modifier = control_mapper.get_modifier()

            if rule != _last_rule or conf >= 0.7:
                if modifier < 1.0:
                    action = f"accel ×{modifier:.2f} (lifting {int((1-modifier)*100)}%)"
                else:
                    action = "accel ×1.00 (full throttle)"

                print(
                    f"[WingMan] corner={corner:<3} "
                    f"rule={rule:<28} conf={conf:.2f}  → {action}"
                )
                print(
                    f"          Speed: {speed:.0f} km/h  "
                    f"SOC: {soc:.3f}  "
                    f"lat={lat:.1f}ms  src={src}"
                )
                if rec and rule != "safe_default":
                    print(f"          → {rec}")
                _last_rule = rule

            await _push_to_ws_server(alert)

        except Exception as e:
            print(f"[Alert consumer] Error: {e}")
        finally:
            alert_queue.task_done()


async def _wingman_main():
    """Main asyncio coroutine: runs pipeline + alert consumer."""
    alert_queue = asyncio.Queue(maxsize=200)

    pipeline = FastPathPipeline(
        input_queue=asyncio.Queue(),    # unused — we call process_tick() directly
        output_queue=alert_queue,
    )

    print("=" * 60)
    print("  WingMan x TORCS — Threaded Integration Mode")
    print("=" * 60)
    print(f"  TORCS drive loop   → dedicated thread (synchronous)")
    print(f"  WingMan pipeline   → asyncio event loop")
    print(f"  Alerts             → {WS_SERVER_URL}")
    print(f"  UI                 → http://localhost:8001/ui/index.html")
    print("  Press Ctrl+C to stop\n")

    tasks = [
        asyncio.create_task(_pipeline_loop(pipeline, alert_queue), name="pipeline"),
        asyncio.create_task(_alert_consumer(alert_queue),           name="alert-consumer"),
    ]

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    finally:
        for t in tasks:
            t.cancel()
        stats = pipeline.stats()
        print("\n-- Final Stats " + "-" * 44)
        for k, v in stats.items():
            print(f"  {k}: {v}")
        print("=" * 60)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    # Start TORCS drive thread (daemon — dies with main process)
    drive_thread = threading.Thread(
        target=_torcs_drive_thread,
        name="TORCS-Drive",
        daemon=True,
    )
    drive_thread.start()

    # Run WingMan pipeline on the main thread's asyncio loop
    try:
        asyncio.run(_wingman_main())
    except KeyboardInterrupt:
        print("\n[WingMan] Ctrl+C received — shutting down.")


if __name__ == "__main__":
    main()
