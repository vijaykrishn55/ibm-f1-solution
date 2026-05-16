"""Latency test -- measures ingestion path latency statistics.

Day 3 -- Person A

Measures the time from raw fixture data to validated state vector,
across all fixture data and mock state vectors. Reports avg, P95,
P99, and max latency. SLO: ingestion path < 50ms (fast path total < 100ms).

Usage:
    python tests/latency_test.py
"""

import os
import sys
import time
import copy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from state.schema import new_state, validate_state
from ingestion.openf1_stream import build_state, load_corner_map
from ingestion.torcs_adapter import torcs_to_state
from tests.mock_state_vectors import MOCK_STATE_VECTORS


def measure_openf1_latency(iterations: int = 200):
    """Measure build_state latency over fixture data."""
    import json

    corner_map = load_corner_map("bahrain")

    fixture_path = os.path.join("tests", "fixtures", "car_data.json")
    with open(fixture_path) as f:
        rows = json.load(f)

    latencies = []
    for i in range(iterations):
        row = rows[i % len(rows)]
        t0 = time.perf_counter()
        state = build_state(row, corner_map)
        _ = validate_state(state)
        elapsed = (time.perf_counter() - t0) * 1000
        latencies.append(elapsed)

    return latencies


def measure_torcs_latency(iterations: int = 200):
    """Measure torcs_to_state latency over synthetic sensor data."""
    sensor_sets = [
        {"speedX": 50.0, "accel": 0.7, "brake": 0.0, "fuel": 80.0,
         "distFromStart": 500.0, "opponents": [100.0], "distRaced": 500.0},
        {"speedX": 70.0, "accel": 0.9, "brake": 0.0, "fuel": 60.0,
         "distFromStart": 2000.0, "opponents": [20.0, 50.0], "distRaced": 5000.0},
        {"speedX": 30.0, "accel": 0.3, "brake": 0.8, "fuel": 40.0,
         "distFromStart": 3500.0, "opponents": [200.0], "distRaced": 10000.0},
    ]

    latencies = []
    for i in range(iterations):
        sensors = sensor_sets[i % len(sensor_sets)]
        t0 = time.perf_counter()
        state = torcs_to_state(sensors)
        _ = validate_state(state)
        elapsed = (time.perf_counter() - t0) * 1000
        latencies.append(elapsed)

    return latencies


def measure_mock_vectors_latency():
    """Measure validate_state latency over all mock state vectors."""
    latencies = []
    for sv in MOCK_STATE_VECTORS:
        s = copy.deepcopy(sv)
        t0 = time.perf_counter()
        _ = validate_state(s)
        elapsed = (time.perf_counter() - t0) * 1000
        latencies.append(elapsed)

    return latencies


def report(name: str, latencies: list):
    """Print latency statistics."""
    if not latencies:
        print(f"  {name}: no data")
        return

    n = len(latencies)
    avg = sum(latencies) / n
    sorted_lat = sorted(latencies)
    p50 = sorted_lat[int(n * 0.50)]
    p95 = sorted_lat[int(n * 0.95)]
    p99 = sorted_lat[min(int(n * 0.99), n - 1)]
    mx  = sorted_lat[-1]

    status = "PASS" if p95 < 50.0 else "FAIL"

    print(f"  {name}:")
    print(f"    N={n}  Avg={avg:.3f}ms  P50={p50:.3f}ms  "
          f"P95={p95:.3f}ms  P99={p99:.3f}ms  Max={mx:.3f}ms  [{status}]")

    return p95


def test_latency():
    """Run all latency measurements and assert SLOs."""
    print("=" * 60)
    print("Ingestion Latency Profiling")
    print("=" * 60)

    # Warmup
    corner_map = load_corner_map("bahrain")
    for _ in range(10):
        build_state({"speed": 200, "throttle": 50, "distance": 500}, corner_map)

    print("\n-- Measurements --")

    openf1_lat = measure_openf1_latency(200)
    p95_openf1 = report("OpenF1 build_state", openf1_lat)

    torcs_lat = measure_torcs_latency(200)
    p95_torcs = report("TORCS torcs_to_state", torcs_lat)

    mock_lat = measure_mock_vectors_latency()
    p95_mock = report("Mock validate_state", mock_lat)

    # SLO assertions (ingestion only -- pipeline total is 100ms)
    print(f"\n-- SLO Check (ingestion < 50ms) --")

    all_pass = True
    for name, p95 in [("OpenF1", p95_openf1), ("TORCS", p95_torcs), ("Mock", p95_mock)]:
        if p95 is not None and p95 >= 50.0:
            print(f"  FAIL: {name} P95={p95:.3f}ms exceeds 50ms SLO")
            all_pass = False
        elif p95 is not None:
            print(f"  PASS: {name} P95={p95:.3f}ms")

    assert all_pass, "One or more ingestion paths exceed 50ms P95 SLO"

    print(f"\n{'=' * 60}")
    print("Latency test PASSED")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    test_latency()
