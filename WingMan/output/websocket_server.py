"""WebSocket Server: pushes alert payloads to connected UI clients.

Day 2 additions:
  - merge_queues(): merge VoltEdge and GridSense alert streams
  - run_broadcast_loop(): main async loop consuming from output queue
  - CORS-enabled static file serving for UI
  - Graceful client disconnect handling
  - Alert deduplication by alert_id
  - Stats endpoint for monitoring
"""

import asyncio
import json
import time
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

app = FastAPI(title="WingMan WebSocket Server")

# Mount the UI folder so index.html is served at http://localhost:8001/ui/index.html
app.mount("/ui", StaticFiles(directory="ui"), name="ui")

connected_clients: list[WebSocket] = []
_recent_alert_ids: list[str] = []      # ring buffer for dedup
_broadcast_count: int = 0
_start_time: float = time.time()


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    connected_clients.append(ws)
    print(f"[WS] Client connected. Total: {len(connected_clients)}")
    try:
        while True:
            await ws.receive_text()   # Keep connection alive
    except WebSocketDisconnect:
        connected_clients.remove(ws)
        print(f"[WS] Client disconnected. Total: {len(connected_clients)}")
    except Exception:
        if ws in connected_clients:
            connected_clients.remove(ws)


@app.get("/stats")
def get_stats():
    """Stats endpoint for monitoring."""
    return {
        "connected_clients": len(connected_clients),
        "total_broadcasts": _broadcast_count,
        "uptime_seconds": round(time.time() - _start_time, 1),
        "recent_alert_ids": len(_recent_alert_ids),
    }


@app.post("/internal/alert")
async def receive_internal_alert(alert: dict):
    """
    Internal endpoint: pipeline posts alerts here -> broadcast to WS clients.
    Used by run_torcs.py and run_pipeline.py to push alerts without a shared queue.
    """
    await broadcast(alert)
    return {"status": "ok", "clients": len(connected_clients)}


async def broadcast(payload: dict):
    """Push a payload to all connected UI clients with deduplication."""
    global _broadcast_count

    # Deduplicate by alert_id
    alert_id = payload.get("alert_id", "")
    if alert_id and alert_id in _recent_alert_ids:
        return
    if alert_id:
        _recent_alert_ids.append(alert_id)
        if len(_recent_alert_ids) > 200:
            _recent_alert_ids.pop(0)

    if not connected_clients:
        return

    message = json.dumps(payload)
    dead = []
    for client in connected_clients:
        try:
            await client.send_text(message)
        except Exception:
            dead.append(client)
    for client in dead:
        connected_clients.remove(client)

    _broadcast_count += 1


async def merge_queues(*queues: asyncio.Queue):
    """
    Merge multiple async queues into a single stream.
    Yields items from whichever queue has data available first.

    Day 2: Used to merge VoltEdge output_queue and GridSense output_queue
    so both flow through the same alert_builder -> broadcast -> UI path.
    """
    async def _reader(q: asyncio.Queue, out: asyncio.Queue, name: str):
        while True:
            item = await q.get()
            await out.put((name, item))
            q.task_done()

    merged = asyncio.Queue()
    tasks = []
    names = ["voltedge", "gridsense", "extra_1", "extra_2"]

    for i, q in enumerate(queues):
        name = names[i] if i < len(names) else f"source_{i}"
        task = asyncio.create_task(_reader(q, merged, name))
        tasks.append(task)

    try:
        while True:
            source_name, item = await merged.get()
            yield source_name, item
    finally:
        for task in tasks:
            task.cancel()


async def run_broadcast_loop(
    output_queue: asyncio.Queue,
    gridsense_queue: asyncio.Queue = None,
    tts_fn=None,
    get_state_fn=None,
):
    """
    Main broadcast loop. Consumes alerts from one or more output queues,
    packages them via alert_builder, broadcasts to WebSocket clients,
    and optionally triggers TTS.

    Day 2: Merges VoltEdge and GridSense queues.
    """
    from output.alert_builder import build_payload

    queues = [output_queue]
    if gridsense_queue is not None:
        queues.append(gridsense_queue)

    print(f"[WS] Broadcast loop started with {len(queues)} queue(s)")

    if len(queues) == 1:
        # Single queue mode (Day 1 compatible)
        while True:
            alert = await output_queue.get()
            state = get_state_fn() if get_state_fn else {}
            payload = build_payload(alert, state)
            await broadcast(payload)

            # TTS: speak recommendation if not braking
            if tts_fn and not state.get("brake", False):
                try:
                    await tts_fn(alert.get("recommendation", ""), state)
                except Exception as e:
                    print(f"[WS] TTS error: {e}")
    else:
        # Multi-queue merge mode (Day 2)
        async for source_name, alert in merge_queues(*queues):
            state = get_state_fn() if get_state_fn else {}
            payload = build_payload(alert, state)
            await broadcast(payload)

            if tts_fn and not state.get("brake", False):
                try:
                    await tts_fn(alert.get("recommendation", ""), state)
                except Exception as e:
                    print(f"[WS] TTS error: {e}")