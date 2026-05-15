"""FastF1 historical loader — extracts state vectors from FastF1 session data.

Day 2 — Person A

Loads historical F1 sessions via FastF1 library and extracts state vectors
in the same schema as the live pipeline. Used for:
  - FAISS index building (offline/build_index.py)
  - Threshold calibration (offline/calibrate.py)
  - Replay validation

Output: list of state vector dicts or export to CSV/Parquet.
"""

import os
import sys
import time
import json

sys.path.insert(0, ".")
from state.schema import new_state


# ── Configuration ────────────────────────────────────────────────────────────

BAHRAIN_TRACK_LENGTH = 5412   # metres
NUM_CORNERS          = 15


def distance_to_corner_id(distance: float, track_length: float = BAHRAIN_TRACK_LENGTH) -> int:
    """Map lap distance to corner ID (1..15) using equal-bucket method."""
    pos = distance % track_length
    return int(pos / (track_length / NUM_CORNERS)) + 1


# ── Core extraction ──────────────────────────────────────────────────────────

def extract_states_from_session(year: int, circuit: str, session_type: str,
                                 driver: str = "VER", max_laps: int = 70) -> list[dict]:
    """
    Load a FastF1 session and extract state vectors from telemetry.

    Returns a list of state vector dicts compatible with the pipeline.
    Requires fastf1 to be installed and internet for first download.

    Args:
        year:         Season year (e.g. 2024)
        circuit:      Circuit name (e.g. 'Bahrain')
        session_type: Session type (e.g. 'Race', 'FP1', 'Qualifying')
        driver:       Driver code (e.g. 'VER')
        max_laps:     Maximum number of laps to process
    """
    try:
        import fastf1
        import pandas as pd
    except ImportError:
        print("[FastF1Loader] fastf1 not installed — run: pip install fastf1")
        return []

    print(f"[FastF1Loader] Loading {year} {circuit} {session_type} for {driver} ...")
    session = fastf1.get_session(year, circuit, session_type)
    session.load(telemetry=True)

    driver_laps = session.laps.pick_driver(driver)
    if driver_laps.empty:
        print(f"[FastF1Loader] No laps found for {driver}")
        return []

    states = []
    lap_count = 0

    for _, lap in driver_laps.iterrows():
        if lap_count >= max_laps:
            break

        try:
            tel = lap.get_car_data().add_distance()
        except Exception as e:
            print(f"[FastF1Loader] Skipping lap {lap['LapNumber']}: {e}")
            continue

        if tel.empty:
            continue

        lap_number = int(lap["LapNumber"])

        # Sample every 10th row (~24Hz -> ~2.4Hz, close to our 4Hz pipeline)
        sampled = tel.iloc[::10]

        for _, row in sampled.iterrows():
            distance  = float(row.get("Distance", 0))
            speed     = float(row.get("Speed", 0))
            throttle  = float(row.get("Throttle", 0)) / 100.0   # FastF1 gives 0–100
            brake_val = row.get("Brake", False)
            brake     = bool(brake_val)
            drs_raw   = int(row.get("DRS", 0))
            drs       = drs_raw in [8, 10, 12, 14]
            n_gear    = int(row.get("nGear", 0))

            corner_id    = distance_to_corner_id(distance)
            lap_fraction = round(distance / BAHRAIN_TRACK_LENGTH, 3)
            aero_state   = "straight_mode" if drs else "corner_mode"

            state = new_state(
                timestamp     = time.time(),
                driver        = driver,
                lap           = lap_number,
                corner_id     = corner_id,
                lap_fraction  = min(lap_fraction, 1.0),
                speed         = round(speed, 2),
                throttle      = round(max(0.0, min(throttle, 1.0)), 3),
                brake         = brake,
                drs           = drs,
                aero_state    = aero_state,
                soc_raw       = 0.85,       # No raw SOC in FastF1 — proxy
                soc_estimated = 0.0,        # Will be filled by Kalman offline
                gap_ahead     = 0.0,        # Gap data handled separately
                session_flag  = "green",
                data_age_ms   = 0,
                data_source   = "fastf1",
            )
            states.append(state)

        lap_count += 1

    print(f"[FastF1Loader] Extracted {len(states)} vectors from {lap_count} laps")
    return states


# ── Export helpers ────────────────────────────────────────────────────────────

def export_to_csv(states: list[dict], filepath: str):
    """Export extracted state vectors to CSV for FAISS index building."""
    try:
        import pandas as pd
    except ImportError:
        print("[FastF1Loader] pandas not installed for CSV export")
        return

    df = pd.DataFrame(states)
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    df.to_csv(filepath, index=False)
    print(f"[FastF1Loader] Exported {len(states)} vectors to {filepath}")


def export_corner_distances(year: int, circuit: str, session_type: str = "FP1",
                             driver: str = "VER", output_path: str = None):
    """
    Extract corner distance boundaries from FastF1 telemetry.
    Produces a bahrain_corners.json with {corner_id: {dist_min, dist_max}}.

    Task A5 from Day 1 plan — refined with real data on Day 2.
    """
    try:
        import fastf1
    except ImportError:
        print("[FastF1Loader] fastf1 not installed")
        return

    print(f"[FastF1Loader] Extracting corner distances from {year} {circuit} {session_type} ...")
    session = fastf1.get_session(year, circuit, session_type)
    session.load(telemetry=True)

    driver_laps = session.laps.pick_driver(driver)
    if driver_laps.empty:
        print(f"[FastF1Loader] No laps for {driver}")
        return

    # Use a mid-stint lap for clean data
    mid_lap_idx = min(5, len(driver_laps) - 1)
    lap = driver_laps.iloc[mid_lap_idx]

    try:
        tel = lap.get_car_data().add_distance()
    except Exception as e:
        print(f"[FastF1Loader] Cannot extract telemetry: {e}")
        return

    max_dist = float(tel["Distance"].max())
    print(f"[FastF1Loader] Track length from telemetry: {max_dist:.1f}m")

    # Divide into NUM_CORNERS equal segments
    segment = max_dist / NUM_CORNERS
    corners = {}
    for i in range(1, NUM_CORNERS + 1):
        corners[str(i)] = {
            "dist_min": round(segment * (i - 1)),
            "dist_max": round(segment * i) if i < NUM_CORNERS else round(max_dist),
        }

    if output_path is None:
        output_path = os.path.join("config", "circuits", f"{circuit.lower()}_corners.json")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(corners, f, indent=2)
    print(f"[FastF1Loader] Saved corner distances -> {output_path}")


# ── Standalone runner ────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Quick test with a small session — requires fastf1 + internet
    states = extract_states_from_session(2024, "Bahrain", "FP1", driver="VER", max_laps=3)
    if states:
        print(f"\nSample state vector (first):")
        for k, v in states[0].items():
            print(f"  {k}: {v}")
        export_to_csv(states, "offline/bahrain_fp1_vectors.csv")
    else:
        print("\nNo states extracted — check fastf1 installation and network.")
