"""Async event queue between fast and slow path (placeholder)"""

import asyncio

class EventQueue:
    def __init__(self):
        self.q = asyncio.Queue()

    async def push(self, item):
        await self.q.put(item)

    async def get(self):
        return await self.q.get()
