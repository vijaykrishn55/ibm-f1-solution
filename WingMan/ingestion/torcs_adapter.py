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
import math
import time
import sys
import os

sys.path.insert(0, ".")
from state.schema import new_state

# Path to gym_torcs folder — adjust if different on your machine
TORCS_PATH = r"E:\ibm\ibm f1 solution\gym_torcs"
sys.path.insert(0, TORCS_PATH)

TRACK_LENGTH = 3773.57   # Alpine-2 default. Update per track.
MAX_FUEL     = 94.0      # TORCS default fuel capacity in litres
NUM_CORNERS  = 15        # Bucket track into this many corner zones


# ── Layer 1: Autopilot ──────────────────────────────────────────────────────

def drive_autopilot(client, accel_modifier: float = 1.0) -> None:
    """
    Minimal autopilot that keeps the TORCS car driving while WingMan observes.

    Mirrors snakeoil3_gym's tested drive_example() exactly:
      - Steering gain 15/pi  (matches drive_example — 10/pi was too weak)
      - Traction Control System (TCS) — prevents wheel-spin spin-outs
      - 6-speed automatic gearbox  (speedX thresholds in km/h)
      - target_speed = 300 km/h cap

    The accel_modifier (0.0–1.0) is injected by WingMan's control_mapper
    AFTER TCS so TCS can still cut power if wheels are spinning.
    """
    S, R = client.S.d, client.R.d

    target_speed = 300  # km/h — matches snakeoil3_gym drive_example

    # ── Steering: align to track axis + push toward centre ──
    R["steer"]  = float(S.get("angle", 0)) * 15.0 / math.pi
    R["steer"] -= float(S.get("trackPos", 0)) * 0.10

    # ── Throttle: cruise toward target speed, back off in corners ──
    speed = float(S.get("speedX", 0))   # already km/h
    if speed < target_speed - abs(R["steer"] * 50.0):
        R["accel"] = min(1.0, R.get("accel", 0.2) + 0.01)
    else:
        R["accel"] = max(0.0, R.get("accel", 0.2) - 0.01)
    if speed < 10:
        R["accel"] = min(1.0, R["accel"] + 1.0 / (speed + 0.1))

    # ── Traction Control System (TCS) — prevents rear-wheel spin-outs ──
    # Compares rear (index 2,3) vs front (index 0,1) wheel spin velocity.
    # If rear spins >5 rad/s faster than front → wheels are slipping → cut throttle.
    wheel_spin = S.get("wheelSpinVel", [0.0, 0.0, 0.0, 0.0])
    if len(wheel_spin) >= 4:
        rear_vs_front = (wheel_spin[2] + wheel_spin[3]) - (wheel_spin[0] + wheel_spin[1])
        if rear_vs_front > 5.0:
            R["accel"] -= 0.2

    # ── WingMan energy modifier applied after TCS ──
    R["accel"] = max(0.0, min(1.0, R["accel"] * accel_modifier))

    # ── No autonomous braking — lift throttle handles corners ──
    R["brake"] = 0.0

    # ── Automatic gearbox (thresholds in km/h, matches drive_example) ──
    gear = 1
    if speed > 50:  gear = 2
    if speed > 80:  gear = 3
    if speed > 110: gear = 4
    if speed > 140: gear = 5
    if speed > 170: gear = 6
    R["gear"] = gear


# ── Helper ───────────────────────────────────────────────────────────────────

def get_corner_id(dist_from_start: float) -> int:
    """Divide track into NUM_CORNERS equal buckets."""
    pos = dist_from_start % TRACK_LENGTH
    return int(pos / (TRACK_LENGTH / NUM_CORNERS)) + 1


def torcs_to_state(sensors: dict, prev_soc: float = 0.85) -> dict:
    """
    Convert TORCS sensor dict to WingMan state vector.

    TORCS sensor keys (from snakeoil3_gym.py):
        speedX, speedY, speedZ   — speed components in km/h (TORCS native unit)
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

    # Speed: TORCS speedX is already in km/h — do NOT multiply by 3.6
    speed_kmh = abs(float(sensors.get("speedX", 0)))

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
        # pyrefly: ignore [missing-import]
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

                    # ── Drive the car (MUST call respond_to_server every tick) ──
                    from ingestion import control_mapper
                    drive_autopilot(client, accel_modifier=control_mapper.get_modifier())
                    client.respond_to_server()

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