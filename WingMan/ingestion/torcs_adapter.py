"""TORCS Adapter: reads live sensor data from TORCS via snakeoil UDP client.
Converts TORCS sensor dict into WingMan state vector format.
Requires TORCS running with scr_server driver and torcs_jm_par.py running.

Day 2 additions:
  - Graceful disconnect detection and reconnection
  - data_age_ms tracking (stale detection if TORCS drops out)
  - Lap tracking from distRaced / track_length
  - Energy delta estimation from fuel consumption
"""

import asyncio
import time
import sys
import os

sys.path.insert(0, ".")
from state.schema import new_state

# Path to gym_torcs folder — adjust if different on your machine
TORCS_PATH = r"C:\RaceYourCode\gym_torcs"
sys.path.insert(0, TORCS_PATH)

TRACK_LENGTH = 3773.57   # Alpine-2 default. Update per track.
MAX_FUEL     = 94.0      # TORCS default fuel capacity in litres
NUM_CORNERS  = 15        # Bucket track into this many corner zones


def get_corner_id(dist_from_start: float) -> int:
    """Divide track into NUM_CORNERS equal buckets."""
    pos = dist_from_start % TRACK_LENGTH
    return int(pos / (TRACK_LENGTH / NUM_CORNERS)) + 1


def torcs_to_state(sensors: dict, prev_soc: float = 0.85) -> dict:
    """
    Convert TORCS sensor dict to WingMan state vector.

    TORCS sensor keys (from snakeoil3_gym.py):
        speedX, speedY, speedZ   — speed components in m/s
        accel                    — 0.0 to 1.0
        brake                    — 0.0 to 1.0
        gear                     — current gear int
        trackPos                 — -1 to 1 (0 = centre)
        distFromStart            — metres from start line
        fuel                     — current fuel in litres
        opponents                — list of 36 opponent distances
        damage                   — damage points
        distRaced                — total distance raced
        racePos                  — position in race
        lapTimes                 — [current, last, best]
    """
    now = time.time()

    # Speed: TORCS gives m/s components, convert to km/h
    speed_ms = float(sensors.get("speedX", 0))
    speed_kmh = abs(speed_ms) * 3.6

    throttle  = float(sensors.get("accel", 0))
    brake_val = float(sensors.get("brake", 0))
    brake     = brake_val > 0.1

    fuel         = float(sensors.get("fuel", MAX_FUEL))
    soc_raw      = round(fuel / MAX_FUEL, 3)   # fuel level as SOC proxy

    # Energy delta: difference from previous SOC reading
    energy_delta = round(soc_raw - prev_soc, 4)

    dist         = float(sensors.get("distFromStart", 0))
    corner_id    = get_corner_id(dist)
    lap_fraction = round((dist % TRACK_LENGTH) / TRACK_LENGTH, 3)

    # Lap number from total distance raced
    dist_raced = float(sensors.get("distRaced", 0))
    lap        = max(1, int(dist_raced / TRACK_LENGTH) + 1)

    # Gap ahead: nearest opponent from opponents array
    opponents = sensors.get("opponents", [200] * 36)
    gap_ahead = round(min(opponents) if opponents else 0.0, 2)

    return new_state(
        timestamp     = now,
        driver        = "TORCS_CAR_1",
        lap           = lap,
        corner_id     = corner_id,
        lap_fraction  = lap_fraction,
        speed         = round(speed_kmh, 2),
        throttle      = round(throttle, 3),
        brake         = brake,
        drs           = False,           # TORCS has no DRS
        aero_state    = "straight_mode" if throttle > 0.8 and not brake else "corner_mode",
        soc_raw       = soc_raw,
        soc_estimated = 0.0,             # Person B fills via Kalman
        energy_delta  = energy_delta,
        gap_ahead     = gap_ahead,
        session_flag  = "green",
        data_age_ms   = 0,
        data_source   = "torcs",
    )


async def stream(queue: asyncio.Queue, interval: float = 0.25):
    """
    Reads TORCS sensor data via snakeoil and puts state vectors onto queue.
    Requires torcs_jm_par.py running and TORCS on blue waiting screen.

    Day 2: graceful disconnect detection and reconnection.
    """
    try:
        import snakeoil3_gym as snakeoil
    except ImportError:
        print(f"[TORCS] snakeoil3_gym not found at {TORCS_PATH}")
        print("[TORCS] Make sure TORCS_PATH is set correctly in torcs_adapter.py")
        return

    _prev_soc     = 0.85
    _last_read_at = time.time()
    _reconnect    = True
    _last_lap     = 0

    while _reconnect:
        try:
            print("[TORCS] Connecting to TORCS on port 3001...")
            client = snakeoil.Client(p=3001)
            print("[TORCS] Connected. Streaming state vectors...")

            while True:
                start = time.perf_counter()
                try:
                    client.get_servers_input()
                    sensors = client.S.d
                    state   = torcs_to_state(sensors, prev_soc=_prev_soc)

                    # Track SOC for energy_delta calculation
                    _prev_soc = state["soc_raw"]
                    _last_read_at = time.time()

                    # Lap transition logging
                    current_lap = state.get("lap", 0)
                    if current_lap > _last_lap and _last_lap > 0:
                        print(f"[TORCS] Lap transition: {_last_lap} -> {current_lap}")
                    _last_lap = current_lap

                    await queue.put(state)

                except Exception as e:
                    # Calculate data_age_ms for stale detection
                    age_ms = int((time.time() - _last_read_at) * 1000)
                    if age_ms > 2000:
                        # Push a stale marker state so fast path triggers safe_default
                        stale_state = new_state(
                            data_source="torcs",
                            data_age_ms=age_ms,
                            soc_estimated=0.0,
                        )
                        await queue.put(stale_state)
                        print(f"[TORCS] Read error (stale {age_ms}ms): {e}")
                    else:
                        print(f"[TORCS] Read error: {e}")

                    await asyncio.sleep(1.0)
                    continue

                elapsed    = time.perf_counter() - start
                sleep_time = max(0.0, interval - elapsed)
                await asyncio.sleep(sleep_time)

        except ConnectionRefusedError:
            print("[TORCS] Connection refused — is TORCS running?")
            print("[TORCS] Retrying in 5 seconds ...")
            await asyncio.sleep(5.0)
        except Exception as e:
            print(f"[TORCS] Disconnected: {e}")
            print("[TORCS] Reconnecting in 3 seconds ...")
            await asyncio.sleep(3.0)