"""WebSocket Server: pushes alert payloads to connected UI clients."""

import asyncio
import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

app = FastAPI()

# Mount the UI folder so index.html is served at http://localhost:8001/ui/index.html
app.mount("/ui", StaticFiles(directory="ui"), name="ui")

connected_clients: list[WebSocket] = []


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


async def broadcast(payload: dict):
    """Push a payload to all connected UI clients."""
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