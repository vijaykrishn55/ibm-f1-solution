"""
run_pipeline.py  — Run from WingMan/ root:
    python run_pipeline.py

Starts the full WingMan fast-path pipeline using mock state vectors.
Imports work because this file lives at the WingMan/ root (on sys.path).
"""
import sys
import os

# Ensure WingMan root is on path (handles both `python run_pipeline.py`
# and `python -m run_pipeline` invocations)
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import asyncio
import time
from fast_path.pipeline import FastPathPipeline


async def _mock_producer(queue: asyncio.Queue):
    """Replays mock state vectors at 4 Hz (250 ms intervals)."""
    from tests.mock_state_vectors import (
        NORMAL, SOC_DANGER, LIFT_NOT_WORTH, GOOD_RECHARGE,
        SAFETY_CAR, STALE_DATA, TORCS_STATE,
    )
    import copy

    scenarios = [NORMAL, SOC_DANGER, LIFT_NOT_WORTH, GOOD_RECHARGE,
                 SAFETY_CAR, STALE_DATA, TORCS_STATE]

    print(f"[Producer] Sending {28} state vectors ...")
    for i in range(28):
        s = copy.deepcopy(scenarios[i % len(scenarios)])
        s["timestamp"] = time.time()
        s["lap"] = 1 + i // 7
        await queue.put(s)
        await asyncio.sleep(0.05)   # 50 ms — faster than real-time for smoke test

    await queue.put(None)   # sentinel — tells main loop to stop


async def _main():
    input_q  = asyncio.Queue()
    output_q = asyncio.Queue()

    def print_alert(alert: dict):
        print(
            f"  [Alert] rule={alert['rule']:<28} "
            f"conf={alert['confidence']:.2f}  "
            f"lat={alert['_pipeline_latency_ms']:.1f}ms"
        )

    pipeline = FastPathPipeline(input_q, output_q, on_alert=print_alert)

    print("=" * 60)
    print("  WingMan FastPathPipeline -- end-to-end smoke test")
    print("=" * 60)

    producer = asyncio.create_task(_mock_producer(input_q))

    while True:
        raw = await input_q.get()
        if raw is None:
            break
        alert = pipeline.process_tick(raw)
        await output_q.put(alert)
        print_alert(alert)

    await producer

    stats = pipeline.stats()
    print("\n-- Pipeline Stats " + "-" * 42)
    for k, v in stats.items():
        print(f"  {k}: {v}")

    p95 = stats["p95_latency_ms"]
    slo = 100.0
    status = "PASS" if p95 < slo else "FAIL"
    print(f"\n  P95 latency {p95:.1f} ms  vs  SLO {slo} ms  -->  {status}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(_main())
