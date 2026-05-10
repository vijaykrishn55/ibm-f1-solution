"""Simple WebSocket server stub (placeholder)
In production use `websockets` or `aiohttp` for robust server implementation.
"""

import asyncio

async def broadcast(alert):
    # TODO: push alert to connected websocket clients
    print('Broadcasting alert:', alert['alert_id'])
