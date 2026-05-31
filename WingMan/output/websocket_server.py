"""WebSocket server — broadcasts WingMan alerts to the dashboard UI.

Serves:
  ws://localhost:8001/ws      ← WebSocket endpoint (alerts JSON)
  http://localhost:8001/ui/   ← Static UI (ui/index.html)

Run: python -m output.websocket_server
"""

import asyncio
import json
import os
import sys
from pathlib import Path

# Force UTF-8 on Windows
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
import uvicorn

app = FastAPI()

# Serve the ui/ folder as static files
_ui_dir = Path(__file__).parent.parent / "ui"
if _ui_dir.exists():
    app.mount("/ui", StaticFiles(directory=str(_ui_dir), html=True), name="ui")

# ── Connection manager ────────────────────────────────────────────────────────

class _Manager:
    def __init__(self):
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._connections.append(ws)
        print(f"[WS] Client connected  ({len(self._connections)} total)")

    def disconnect(self, ws: WebSocket):
        self._connections.remove(ws)
        print(f"[WS] Client disconnected ({len(self._connections)} remaining)")

    async def broadcast(self, payload: dict):
        if not self._connections:
            return
        msg = json.dumps(payload)
        dead = []
        for ws in self._connections:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._connections.remove(ws)


_manager = _Manager()
_recent_alert_ids = set()


# ── Public API (called from run_openf1.py) ────────────────────────────────────

async def broadcast(payload: dict):
    """Broadcast an alert dict to all connected dashboard clients."""
    aid = payload.get("alert_id")
    if aid:
        if aid in _recent_alert_ids:
            return
        _recent_alert_ids.add(aid)
    await _manager.broadcast(payload)


async def merge_queues(q1: asyncio.Queue, q2: asyncio.Queue):
    """Asynchronously merge two queues and yield (source, item) tuples."""
    while True:
        t1 = asyncio.create_task(q1.get())
        t2 = asyncio.create_task(q2.get())

        try:
            done, pending = await asyncio.wait([t1, t2], return_when=asyncio.FIRST_COMPLETED)

            for t in pending:
                t.cancel()

            for t in done:
                item = t.result()
                source = item.get("source_module") or item.get("source") or "voltedge"
                yield source, item
        except asyncio.CancelledError:
            t1.cancel()
            t2.cancel()
            raise


# ── WebSocket endpoint ────────────────────────────────────────────────────────

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await _manager.connect(ws)
    try:
        while True:
            await ws.receive_text()   # keep connection alive; we only push
    except WebSocketDisconnect:
        _manager.disconnect(ws)


@app.get("/")
async def root():
    return {"status": "WingMan WS server running", "ui": "/ui/index.html"}


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("[WS] Starting WingMan WebSocket server on http://localhost:9000")
    print("[WS] Dashboard -> http://localhost:9000/ui/index.html")
    uvicorn.run(app, host="0.0.0.0", port=9000, log_level="warning")
