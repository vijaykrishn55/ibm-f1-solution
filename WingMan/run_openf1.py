"""
run_openf1.py -- WingMan all-in-one entry point.

  python run_openf1.py
  Browser -> http://localhost:9000/ui/index.html
    Race Radio Intelligence demo: type 'g' + Enter
"""

import asyncio
import sys
import time
import threading
import subprocess

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
from ingestion.openf1_stream  import stream
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


async def run():
    print("\n  WingMan x OpenF1 -- Live F1 Data")
    print("  [WingMan] | [Tyre Health Monitor] | [Lap Time Predictor] | [Race Radio Intelligence]")
    print("=" * 60)
    print("  Dashboard -> http://localhost:9000/ui/index.html")

    state_q = asyncio.Queue()
    alert_q = asyncio.Queue()

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

    asyncio.create_task(stream(state_q))

    tick = 0
    while True:
        state = await state_q.get()
        tick += 1

        # Fast path
        t0    = time.perf_counter()
        alert = pipeline.process_tick(state)
        lat   = (time.perf_counter() - t0) * 1000

        if alert:
            store.record_alert(alert)
            alert["alert_id"] = f"wm_{tick}"
            alert["module"]   = "wingman"
            await broadcast(build_payload(alert, state))
            _maybe_speak(alert.get("recommendation", ""), state)

        # TyreWhisperer
        tw_alert = tw.update(state)
        if tw_alert:
            tw_alert["alert_id"] = f"tw_{tick}"
            tw_alert["module"]   = "tyrewhisperer"
            await broadcast(build_payload(tw_alert, state))
            print(f"[Tyre Health Monitor] {tw_alert['recommendation'][:80]}")

        # GhostDelta
        gd_alert = gd.update(state)
        if gd_alert:
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
        print(f"[Race Radio Intelligence]     → {result['recommendation'][:80]}")


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
            print(f"[Granite]  {result['fan_explanation'][:80]}")
    except Exception as e:
        print(f"[Granite]  slow path error: {e}")


if __name__ == "__main__":
    # Run uvicorn server + pipeline in the SAME event loop.
    # This is critical: broadcast() sends to WebSocket clients that live
    # in the same loop, so send_text() works correctly.
    async def main():
        config = uvicorn.Config(ws_app, host="0.0.0.0", port=9000, log_level="warning")
        server = uvicorn.Server(config)
        await asyncio.gather(server.serve(), run())

    asyncio.run(main())
