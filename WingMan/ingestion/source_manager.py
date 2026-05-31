"""Source Manager: runs OpenF1 data stream into a shared queue.

Day 2 — Person A

Mode:
  "openf1"  : OpenF1 live/replay polling (default)

The fast path pipeline reads from one queue regardless of session type.
"""

import asyncio
import time
import sys
import os

sys.path.insert(0, ".")


class SourceManager:
    """
    Manages one or more ingestion sources and feeds their output
    into a single shared asyncio.Queue consumed by the fast path.
    """

    def __init__(self, queue: asyncio.Queue, mode: str = "openf1", circuit: str = "bahrain"):
        self.queue   = queue
        self.mode    = mode.lower()
        self.circuit = circuit

        self._tasks: list[asyncio.Task] = []
        self._active_sources: list[str] = []

    async def start(self):
        """Start the configured data sources as background tasks."""
        print(f"[SourceManager] Starting in '{self.mode}' mode")

        if self.mode in ("openf1",):
            from ingestion.openf1_stream import stream as openf1_stream
            task = asyncio.create_task(
                openf1_stream(self.queue, circuit=self.circuit)
            )
            self._tasks.append(task)
            self._active_sources.append("openf1")
            print("[SourceManager] OpenF1 stream task created")

        if not self._tasks:
            print(f"[SourceManager] ERROR: unknown mode '{self.mode}'")
            return

        print(f"[SourceManager] Active sources: {self._active_sources}")

    async def stop(self):
        """Cancel all running source tasks."""
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._active_sources.clear()
        print("[SourceManager] All sources stopped")

    async def switch_mode(self, new_mode: str):
        """
        Hot-switch between source modes without restarting the pipeline.
        Stops current sources, starts new ones. Queue is preserved.
        """
        if new_mode.lower() == self.mode:
            print(f"[SourceManager] Already in '{self.mode}' mode")
            return
        print(f"[SourceManager] Switching: {self.mode} -> {new_mode}")
        await self.stop()
        self.mode = new_mode.lower()
        await self.start()

    @property
    def active_sources(self) -> list[str]:
        return list(self._active_sources)

    def stats(self) -> dict:
        return {
            "mode":           self.mode,
            "active_sources": self._active_sources,
            "queue_size":     self.queue.qsize(),
            "n_tasks":        len(self._tasks),
        }


# ── Standalone test ──────────────────────────────────────────────────────────

async def _main():
    """
    Quick integration test: run OpenF1 mock source for 5 seconds,
    print every state vector received.
    """
    q = asyncio.Queue()
    mgr = SourceManager(q, mode="openf1", circuit="bahrain")
    await mgr.start()

    print("[SourceManager] Running for 5 seconds ...")
    end = time.time() + 5

    count = 0
    while time.time() < end:
        try:
            state = await asyncio.wait_for(q.get(), timeout=1.0)
            count += 1
            src = state.get("data_source", "?")
            spd = state.get("speed", 0)
            flg = state.get("session_flag", "?")
            cid = state.get("corner_id", 0)
            print(
                f"  [{count:04d}] src={src:<6}  "
                f"speed={spd:>5.0f}  corner={cid:>2}  "
                f"flag={flg}"
            )
        except asyncio.TimeoutError:
            pass

    await mgr.stop()
    print(f"\n[SourceManager] Received {count} state vectors in 5s")
    print(f"[SourceManager] Stats: {mgr.stats()}")


if __name__ == "__main__":
    asyncio.run(_main())
