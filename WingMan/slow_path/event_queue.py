"""Event Queue: async bridge between fast path and slow path."""

import asyncio


class EventQueue:
    def __init__(self):
        self.q = asyncio.Queue()

    async def push(self, event: dict):
        await self.q.put(event)

    async def pop(self) -> dict:
        return await self.q.get()

    def size(self) -> int:
        return self.q.qsize()