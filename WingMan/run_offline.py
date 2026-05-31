"""
run_offline.py -- WingMan Offline Mode Runner
Replays fixture data with all four modules active and displays results on UI.

Usage:
  python run_offline.py

Features:
    - WingMan (Energy-Aero)
    - Tyre Health Monitor (TyreWhisperer) — Grip Asymmetry
    - Lap Time Predictor (GhostDelta) — Lap Delta
    - Race Radio Intelligence (GridSense) — Radio Intelligence

Browser: http://localhost:9000/ui/index.html
"""

import asyncio
import sys
import time
import threading
import subprocess
import json
import os

# Force UTF-8 on Windows
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# Kill stale process on port 9000 before we bind
def _free_port(port: int):
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"$p=(Get-NetTCPConnection -LocalPort {port} -EA SilentlyContinue).OwningProcess;"
             f"if($p){{Stop-Process -Id $p -Force;Start-Sleep -Milliseconds 500}}"],
            capture_output=True, timeout=8
        )
    except Exception:
        pass

_free_port(9000)

import uvicorn
from output.websocket_server import app as ws_app
from fast_path.pipeline       import FastPathPipeline
from output.websocket_server  import broadcast
from output.tts               import speak
from output.alert_builder     import build_payload
from slow_path.context_forge  import ContextForge

from modules.shared_store   import store
from modules.tyrewhisperer  import TyreWhisperer
from modules.ghostdelta     import GhostDelta
from modules.gridsense      import GridSense

TTS_DEBOUNCE_S = 8.0
_last_tts      = 0.0
_SYNTHETIC_LAP_TICKS = 36
_SYNTHETIC_CORNER_PATTERN = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]


def _reset_demo_state():
    store.soc_history.clear()
    store.alert_history.clear()
    store.lap_profiles.clear()
    store.optimal_profile.clear()
    store.best_lap_time = 999.0
    store.best_lap_number = None
    store.current_lap_recording.clear()
    store.left_asym_history.clear()
    store.right_asym_history.clear()
    store.asym_alarm = False
    store.asym_alarm_side = None
    store.setup_recommendations.clear()
    store.current_lap = 0


def _synthetic_offline_tick(i: int) -> dict:
    lap_index = i // _SYNTHETIC_LAP_TICKS
    lap_tick = i % _SYNTHETIC_LAP_TICKS
    entry_index = lap_tick // 4
    in_corner = (lap_tick % 4) < 3

    if in_corner:
        corner_id = _SYNTHETIC_CORNER_PATTERN[entry_index % len(_SYNTHETIC_CORNER_PATTERN)]
        corner_direction = "left" if corner_id % 2 == 0 else "right"
        steer = 0.22
    else:
        corner_id = 0
        corner_direction = "left" if entry_index % 2 == 0 else "right"
        steer = 0.0

    if in_corner:
        if corner_direction == "left":
            wheel_fl = 118.0 - (entry_index % 3) * 2.0
            wheel_fr = 78.0 - (entry_index % 2) * 1.0
        else:
            wheel_fl = 108.0 - (entry_index % 2) * 1.0
            wheel_fr = 96.0 - (entry_index % 3) * 1.0
    else:
        wheel_fl = 0.0
        wheel_fr = 0.0

    lap_time_current = float(lap_tick) + (0.5 if in_corner else 0.2)
    last_lap_time = float(_SYNTHETIC_LAP_TICKS) if lap_index > 0 else 0.0

    return {
        "lap": lap_index + 1,
        "corner_id": corner_id,
        "corner_direction": corner_direction,
        "steer": steer,
        "wheel_fl": wheel_fl,
        "wheel_fr": wheel_fr,
        "wheel_rl": wheel_fl - 2.0 if in_corner else 99.0,
        "wheel_rr": wheel_fr - 2.0 if in_corner else 99.0,
        "lap_time_current": lap_time_current,
        "last_lap_time": last_lap_time,
    }


async def offline_stream(queue: asyncio.Queue):
    """Stream fixture data from local JSON files at 4 Hz."""
    print("\n[Offline] Loading fixture data from tests/fixtures/")

    fixtures_dir = os.path.join(os.path.dirname(__file__), "tests", "fixtures")
    fixture_prefix = os.environ.get("FIXTURE_PREFIX", "")
    loop_laps_env = os.environ.get("FIXTURE_LOOP_LAPS")
    duration_s_env = os.environ.get("FIXTURE_DURATION_S")
    try:
        loop_laps = int(loop_laps_env) if loop_laps_env else None
    except Exception:
        loop_laps = None
    try:
        duration_s = int(duration_s_env) if duration_s_env else None
    except Exception:
        duration_s = None
    try:
        car_fname = f"{fixture_prefix + '_' if fixture_prefix else ''}car_data.json"
        pos_fname = f"{fixture_prefix + '_' if fixture_prefix else ''}position.json"
        int_fname = f"{fixture_prefix + '_' if fixture_prefix else ''}intervals.json"
        with open(os.path.join(fixtures_dir, car_fname)) as f:
            car_records = json.load(f)
        with open(os.path.join(fixtures_dir, pos_fname)) as f:
            pos_records = json.load(f)
        with open(os.path.join(fixtures_dir, int_fname)) as f:
            interval_records = json.load(f)
    except Exception as e:
        print(f"[Offline] ERROR: Could not load fixture files: {e}")
        return
    
    print(f"[Offline] Loaded {len(car_records)} car records, {len(pos_records)} position records")
    print(f"[Offline] Starting replay at 4 Hz (250ms per tick)")
    
    # Load corner map
    from ingestion.openf1_stream import _xy_to_corner, _xy_to_direction, _xy_to_lap_fraction, DRS_OPEN_CODES
    from state.schema import new_state
    
    prev_soc = 0.85
    lap_n = 1
    prev_distance = None
    flag = "green"
    
    # Replay loop
    base_records = min(len(car_records), len(pos_records))
    if base_records == 0:
        print("[Offline] No records in fixtures — aborting")
        return

    # Determine total ticks to run: prefer explicit env vars; otherwise default
    # If the user didn't provide either a loop count or a duration, default
    # to a demo-friendly 3 minute run. With `_SYNTHETIC_LAP_TICKS = 36` and
    # a 4 Hz tick rate this yields ~20 synthetic laps over 180 seconds.
    if loop_laps is None and duration_s is None:
        print("[Offline] No FIXTURE_LOOP_LAPS or FIXTURE_DURATION_S set; defaulting to 180s demo")
        duration_s = 180

    if loop_laps and loop_laps > 0:
        max_records = base_records * loop_laps
    elif duration_s and duration_s > 0:
        max_records = duration_s * 4
    else:
        max_records = base_records

    for i in range(int(max_records)):
        t0 = time.perf_counter()
        idx = i % base_records
        car = car_records[idx]
        pos = pos_records[idx] if idx < len(pos_records) else None
        interval = interval_records[idx] if idx < len(interval_records) else None
        
        # Build state vector
        speed    = float(car.get("speed") or 0)
        throttle = float(car.get("throttle") or 0) / 100.0
        brake    = float(car.get("brake") or 0) > 0
        drs      = int(car.get("drs") or 0) in DRS_OPEN_CODES
        
        # SOC proxy
        soc_raw      = max(0.0, min(1.0, prev_soc - throttle * 0.0002 + (0.0001 if brake else 0)))
        energy_delta = round(soc_raw - prev_soc, 4)
        
        x = float(pos.get("x", 0)) if pos else 0.0
        y = float(pos.get("y", 0)) if pos else 0.0
        
        corner_id    = _xy_to_corner(x, y)
        direction    = _xy_to_direction(x, y)
        lap_fraction = _xy_to_lap_fraction(x, y)
        
        gap_ahead = 0.0
        if interval:
            raw = interval.get("gap_to_leader", 0) or 0
            try:
                gap_ahead = float(str(raw).replace("+", ""))
            except Exception:
                gap_ahead = 0.0
        
        # Advance the lap estimate when the fixture distance wraps around.
        distance = float(car.get("distance") or 0)
        if prev_distance is not None and distance + 100.0 < prev_distance:
            lap_n += 1
        elif i > 0 and i % 80 == 0:
            # Fallback for sparse fixtures that do not include a clean wrap.
            lap_n += 1
        prev_distance = distance

        synthetic = _synthetic_offline_tick(i)
        lap_n = synthetic["lap"]
        
        state = new_state(
            driver           = "VER_1",
            lap              = lap_n,
            corner_id        = corner_id,
            lap_fraction     = lap_fraction,
            speed            = round(speed, 2),
            throttle         = round(throttle, 3),
            brake            = brake,
            drs              = drs,
            aero_state       = "straight_mode" if throttle > 0.8 and not brake else "corner_mode",
            soc_raw          = round(soc_raw, 4),
            soc_estimated    = round(soc_raw, 4),
            energy_delta     = energy_delta,
            gap_ahead        = gap_ahead,
            session_flag     = flag,
            data_age_ms      = 0,
            data_source      = "offline",
            corner_direction = direction,
        )
        
        # Synthetic demo signals for the offline module triggers.
        state["corner_id"]        = synthetic["corner_id"]
        state["corner_direction"] = synthetic["corner_direction"]
        state["wheel_fl"]         = synthetic["wheel_fl"]
        state["wheel_fr"]         = synthetic["wheel_fr"]
        state["wheel_rl"]         = synthetic["wheel_rl"]
        state["wheel_rr"]         = synthetic["wheel_rr"]
        state["lap_time_current"] = synthetic["lap_time_current"]
        state["last_lap_time"]    = synthetic["last_lap_time"]
        state["steer"]            = synthetic["steer"]
        state["pos_x"]            = x
        state["pos_y"]            = y
        
        prev_soc = soc_raw
        queue.put_nowait(state)
        
        # Maintain 4 Hz rate
        elapsed = time.perf_counter() - t0
        await asyncio.sleep(max(0.0, 0.25 - elapsed))
    
    print(f"[Offline] Replay complete — {max_records} ticks processed")


async def run():
    print("\n╔════════════════════════════════════════════════════════════╗")
    print("║         WingMan OFFLINE MODE — All Features Active        ║")
    print("╚════════════════════════════════════════════════════════════╝")
    print("  [WingMan] | [Tyre Health Monitor] | [Lap Time Predictor] | [Race Radio Intelligence]")
    print("=" * 60)
    print("  Dashboard -> http://localhost:9000/ui/index.html")
    print("  Data Source: Local fixtures (tests/fixtures/)")
    print("  FAISS Index: Loaded from offline/bahrain_index.faiss")
    print("=" * 60)

    state_q = asyncio.Queue()
    alert_q = asyncio.Queue()

    _reset_demo_state()

    pipeline = FastPathPipeline(input_queue=state_q, output_queue=alert_q)
    forge    = ContextForge()
    tw       = TyreWhisperer()
    gd       = GhostDelta(track_length_m=5412.0)   # Bahrain 5.412 km
    gs       = GridSense()

    manual_trigger = threading.Event()

    def _kbd():
        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    break
                if line.strip().lower() == "g":
                    manual_trigger.set()
                    print("[Race Radio Intelligence] Manual trigger armed")
            except Exception:
                break

    threading.Thread(target=_kbd, daemon=True).start()
    print("  Type 'g' + Enter to fire a Race Radio Intelligence demo\n")

    # Start offline stream
    asyncio.create_task(offline_stream(state_q))

    tick = 0
    wingman_alerts = 0
    tw_alerts = 0
    gd_alerts = 0
    gs_alerts = 0
    
    while True:
        try:
            state = await asyncio.wait_for(state_q.get(), timeout=5.0)
        except asyncio.TimeoutError:
            print("[Offline] Stream ended or timeout reached")
            break
        
        tick += 1

        # Fast path — WingMan
        t0    = time.perf_counter()
        alert = pipeline.process_tick(state)
        lat   = (time.perf_counter() - t0) * 1000

        if alert:
            wingman_alerts += 1
            store.record_alert(alert)
            alert["alert_id"] = f"wm_{tick}"
            alert["module"]   = "wingman"
            await broadcast(build_payload(alert, state))
            _maybe_speak(alert.get("recommendation", ""), state)
            print(f"[WingMan]       {alert['recommendation'][:70]}")

        # TyreWhisperer
        tw_alert = tw.update(state)
        if tw_alert:
            tw_alerts += 1
            tw_alert["alert_id"] = f"tw_{tick}"
            tw_alert["module"]   = "tyrewhisperer"
            await broadcast(build_payload(tw_alert, state))
            print(f"[Tyre Health Monitor] {tw_alert['recommendation'][:70]}")

        # GhostDelta
        gd_alert = gd.update(state)
        if gd_alert:
            gd_alerts += 1
            gd_alert["alert_id"] = f"gd_{tick}"
            gd_alert["module"]   = "ghostdelta"
            await broadcast(build_payload(gd_alert, state))
            ghost = gd_alert.get("ghost_data", {})
            print(f"[Lap Time Predictor]    Lap {ghost.get('lap')}: "
                  f"{ghost.get('delta', 0):+.3f}s  worst: {ghost.get('worst_corner', '?')}")

        # GridSense
        manual = manual_trigger.is_set()
        if manual:
            manual_trigger.clear()
        trigger, ctype, corner = gs.should_trigger(state, manual=manual)
        if trigger:
            gs_alerts += 1
            asyncio.create_task(_gridsense(gs, state, ctype, corner))

        # Heartbeat every 20 ticks (~5 s at 4 Hz)
        if tick % 20 == 0:
            rule = alert.get("rule", "—") if alert else "—"
            conf = alert.get("confidence", 0.0) if alert else 0.0
            flag = state.get("session_flag", "?")
            print(f"[tick {tick:5d}]  {state['speed']:6.1f} km/h  "
                  f"soc={state['soc_estimated']:.3f}  "
                  f"corner={state['corner_id']:2d}  "
                  f"flag={flag}  lat={lat:.1f}ms  {rule}({conf:.2f})")

        # Context Forge + Granite every 5 laps
        if gd_alert and gd_alert.get("ghost_data", {}).get("lap"):
            forge.add_lap_summary({
                "lap":             gd_alert["ghost_data"]["lap"],
                "avg_soc":         state["soc_estimated"],
                "alerts_this_lap": sum(1 for a in store.alert_history
                                       if a.get("module") == "wingman"),
                "key_decision":    alert.get("rule", "none") if alert else "none",
            })
            if forge.total_laps_completed() % 5 == 0:
                asyncio.create_task(_granite(forge))
    
    # Summary
    print("\n" + "=" * 60)
    print("  OFFLINE MODE SUMMARY")
    print("=" * 60)
    print(f"  Total Ticks:        {tick}")
    print(f"  WingMan Alerts:     {wingman_alerts}")
    print(f"  Tyre Health Monitor:      {tw_alerts}")
    print(f"  Lap Time Predictor:         {gd_alerts}")
    print(f"  Race Radio Intelligence:     {gs_alerts}")
    print("=" * 60)


def _maybe_speak(text: str, state: dict):
    global _last_tts
    now = time.time()
    if now - _last_tts >= TTS_DEBOUNCE_S:
        _last_tts = now
        asyncio.create_task(speak(text, state))


async def _gridsense(gs, state, ctype, corner):
    result = await gs.process(state, ctype, corner)
    if result:
        await broadcast(result)
        print(f"[Race Radio Intelligence]     → {result['recommendation'][:70]}")


async def _granite(forge):
    try:
        from slow_path.granite_client import GraniteClient
        client = GraniteClient()
        result = await client.analyse_laps(forge.get_last_n_laps(5))
        if "error" in result:
            print(f"[Granite]  skipped: {result['error']}")
            return
        if result.get("fan_explanation"):
            await broadcast({
                "alert_id":        "granite-slow",
                "type":            "strategy_update",
                "module":          "wingman_granite",
                "recommendation":  result.get("strategy_note", ""),
                "fan_explanation": result.get("fan_explanation", ""),
                "confidence":      0.85,
                "corner":          0,
                "audio_text":      "",
            })
            print(f"[Granite]  {result['fan_explanation'][:70]}")
    except Exception as e:
        print(f"[Granite]  slow path error: {e}")


if __name__ == "__main__":
    async def main():
        config = uvicorn.Config(ws_app, host="0.0.0.0", port=9000, log_level="warning")
        server = uvicorn.Server(config)
        server_task = asyncio.create_task(server.serve())
        try:
            await run()
        finally:
            server.should_exit = True
            await server_task

    asyncio.run(main())

# Made with Bob
