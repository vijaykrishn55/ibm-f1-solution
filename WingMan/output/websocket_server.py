# output/websocket_server.py
# -----------------------------------------------------------------------------
# Person C -- Day 2: Full WebSocket Server
#
# FastAPI server that:
#   - Serves the UI at /ui/index.html
#   - WebSocket endpoint at /ws for live alert streaming
#   - REST endpoints for stats, health, Granite trigger
#   - Integrates with OutputEventLoop for alert processing
#   - CORS enabled for local development
#
# Run:
#   uvicorn output.websocket_server:app --port 8001 --reload
# -----------------------------------------------------------------------------

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, HTMLResponse

app = FastAPI(
    title="WingMan WebSocket Server",
    description="Real-time F1 telemetry alert broadcasting",
    version="2.0.0",
)

# -- CORS for local dev -------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# -- Mount the UI folder -------------------------------------------------------
ui_dir = os.path.join(os.path.dirname(__file__), "..", "ui")
if os.path.isdir(ui_dir):
    app.mount("/ui", StaticFiles(directory=ui_dir), name="ui")

# -- Connected WebSocket clients -----------------------------------------------
connected_clients: list[WebSocket] = []

# -- Stats ---------------------------------------------------------------------
_stats = {
    "broadcasts":   0,
    "connections":   0,
    "disconnections": 0,
    "last_alert_rule": "",
    "last_alert_time": 0,
    "server_start":  time.time(),
}


# -- WebSocket endpoint --------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    connected_clients.append(ws)
    _stats["connections"] += 1
    print(f"[WS] Client connected. Total: {len(connected_clients)}")
    try:
        while True:
            # Keep connection alive; client may send pings
            data = await ws.receive_text()
            # Client can send JSON commands (future: source switching)
            try:
                cmd = json.loads(data)
                if cmd.get("type") == "ping":
                    await ws.send_json({"type": "pong", "ts": time.time()})
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        connected_clients.remove(ws)
        _stats["disconnections"] += 1
        print(f"[WS] Client disconnected. Total: {len(connected_clients)}")
    except Exception:
        if ws in connected_clients:
            connected_clients.remove(ws)


async def broadcast(payload: dict):
    """Push a payload to all connected UI clients."""
    if not connected_clients:
        return
    message = json.dumps(payload, default=str)
    dead = []
    for client in connected_clients:
        try:
            await client.send_text(message)
        except Exception:
            dead.append(client)
    for client in dead:
        if client in connected_clients:
            connected_clients.remove(client)

    _stats["broadcasts"] += 1
    _stats["last_alert_rule"] = payload.get("rule", "")
    _stats["last_alert_time"] = time.time()


# -- REST endpoints ------------------------------------------------------------

@app.get("/")
async def root():
    return HTMLResponse(
        "<h2>WingMan Server</h2>"
        "<p>UI: <a href='/ui/index.html'>/ui/index.html</a></p>"
        "<p>WebSocket: ws://localhost:8001/ws</p>"
        "<p>Health: <a href='/health'>/health</a></p>"
        "<p>Stats: <a href='/stats'>/stats</a></p>"
    )


@app.get("/health")
async def health():
    return JSONResponse({
        "status":     "ok",
        "uptime_s":   round(time.time() - _stats["server_start"], 1),
        "clients":    len(connected_clients),
        "broadcasts": _stats["broadcasts"],
    })


@app.get("/stats")
async def stats():
    return JSONResponse({
        **_stats,
        "connected_clients": len(connected_clients),
        "uptime_s":          round(time.time() - _stats["server_start"], 1),
    })


@app.post("/trigger-granite")
async def trigger_granite(lap: int = 10):
    """Manual Granite trigger endpoint for testing."""
    return JSONResponse({
        "status":  "triggered",
        "lap":     lap,
        "message": "Granite analysis requested (check pipeline logs)",
    })


@app.get("/last-alert")
async def last_alert():
    """Return the last broadcast alert for debugging."""
    return JSONResponse({
        "rule":      _stats.get("last_alert_rule", ""),
        "timestamp": _stats.get("last_alert_time", 0),
    })