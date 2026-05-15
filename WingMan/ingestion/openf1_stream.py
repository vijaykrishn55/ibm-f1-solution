"""OpenF1 Stream: polls mock server at 4Hz and builds partial state vectors."""

import asyncio
import time
import httpx
import json
import os
import sys

sys.path.insert(0, ".")
from state.schema import new_state

BASE_URL = "http://localhost:8000"


def load_corner_map(circuit: str = "bahrain") -> dict:
    path = os.path.join("config", "circuits", f"{circuit}.json")
    if not os.path.exists(path):
        print(f"[Stream] Corner map not found at {path}, corner_id will be 0")
        return {}
    with open(path) as f:
        return json.load(f)


def get_corner_id(distance: float, corner_map: dict) -> int:
    for corner_id, bounds in corner_map.get("corner_distances", {}).items():
        if bounds["dist_min"] <= distance < bounds["dist_max"]:
            return int(corner_id)
    return 0


async def fetch_all(client: httpx.AsyncClient) -> dict:
    """Fetch car_data, position, and intervals in parallel."""
    try:
        car_task = client.get(f"{BASE_URL}/v1/car_data")
        pos_task = client.get(f"{BASE_URL}/v1/position")
        int_task = client.get(f"{BASE_URL}/v1/intervals")

        car_r, pos_r, int_r = await asyncio.gather(car_task, pos_task, int_task)

        car  = car_r.json()  if car_r.status_code  == 200 else {}
        pos  = pos_r.json()  if pos_r.status_code  == 200 else {}
        intv = int_r.json()  if int_r.status_code  == 200 else {}

        return {**car, **pos, **intv}
    except Exception as e:
        print(f"[Stream] Fetch error: {e}")
        return {}


def build_state(raw: dict, corner_map: dict) -> dict:
    """Convert raw merged API response into a partial state vector."""
    now = time.time()

    # Parse timestamp from API or use now
    api_time = raw.get("date", None)
    if api_time:
        try:
            from datetime import datetime, timezone
            api_ts = datetime.fromisoformat(
                api_time.replace("Z", "+00:00")
            ).timestamp()
            data_age_ms = int((now - api_ts) * 1000)
        except Exception:
            data_age_ms = 0
    else:
        data_age_ms = 0

    speed    = float(raw.get("speed", 0))
    throttle = float(raw.get("throttle", 0)) / 100.0   # OpenF1 gives 0-100
    brake_raw = raw.get("brake", 0)
    brake    = bool(brake_raw) if isinstance(brake_raw, bool) else brake_raw > 50
    drs_raw  = raw.get("drs", 0)
    # OpenF1 DRS: 8 or 10 = open, 0 = closed
    drs      = drs_raw in [8, 10]

    distance = float(raw.get("distance", 0))
    corner_id = get_corner_id(distance, corner_map)

    lap_fraction = float(raw.get("lap_distance", 0)) / max(
        float(raw.get("track_length", 5412)), 1
    )

    gap_ahead_raw = raw.get("gap_to_leader", None) or raw.get("interval", None)
    try:
        gap_ahead = float(str(gap_ahead_raw).replace("+", "")) if gap_ahead_raw else 0.0
    except Exception:
        gap_ahead = 0.0

    aero_state = "straight_mode" if drs else "corner_mode"

    return new_state(
        timestamp    = now,
        driver       = str(raw.get("driver_number", "VER")),
        lap          = int(raw.get("lap_number", 0)),
        corner_id    = corner_id,
        lap_fraction = round(lap_fraction, 3),
        speed        = speed,
        throttle     = round(throttle, 3),
        brake        = brake,
        drs          = drs,
        aero_state   = aero_state,
        soc_raw      = 0.85,        # Person B fills via Kalman
        soc_estimated = 0.0,        # Person B fills via Kalman
        gap_ahead    = gap_ahead,
        session_flag = "green",     # OpenF1 flag endpoint wired on Day 2
        data_age_ms  = data_age_ms,
        data_source  = "openf1",
    )


async def stream(queue: asyncio.Queue, circuit: str = "bahrain", interval: float = 0.25):
    """
    Main polling loop. Puts partial state vectors onto queue every 250ms.
    Run as a background asyncio task.
    """
    corner_map = load_corner_map(circuit)
    print(f"[Stream] Starting OpenF1 poll loop at {1/interval:.0f}Hz")

    async with httpx.AsyncClient(timeout=2.0) as client:
        while True:
            start = time.perf_counter()
            raw = await fetch_all(client)
            if raw:
                state = build_state(raw, corner_map)
                await queue.put(state)
            elapsed = time.perf_counter() - start
            sleep_time = max(0.0, interval - elapsed)
            await asyncio.sleep(sleep_time)