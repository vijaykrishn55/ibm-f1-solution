# offline/feature_extract.py
# ─────────────────────────────────────────────────────────────────────────────
# Feature extractor — Person B (Task B7 Step 1)
#
# Extracts 8-dimensional feature vectors from FastF1 lap telemetry.
# Run ONCE offline to build the training set for the FAISS index.
#
# Output: two numpy files in offline/
#   - vectors.npy   shape (N, 8) float32
#   - outcomes.npy  shape (N,)   string array ("warning_fired" | "no_warning")
#
# Usage:
#   python -m offline.feature_extract
# ─────────────────────────────────────────────────────────────────────────────

import os
import numpy as np

MAX_FUEL = 110.0   # kg — TORCS default
SOC_DANGER_THRESHOLD = 0.25


def extract_features_from_state(state: dict) -> list[float]:
    """
    Convert a state vector into an 8-dimensional feature vector.

    Dimensions:
      0  soc_estimated      (0.0 – 1.0)
      1  throttle           (0.0 – 1.0)
      2  speed_norm         (0.0 – 1.0, normalised over 350 km/h max)
      3  corner_id_norm     (0.0 – 1.0, over 15 corners)
      4  lap_fraction       (0.0 – 1.0)
      5  energy_delta_norm  (scaled from [-0.01, 0.01] to [0, 1])
      6  gap_ahead_norm     (0.0 – 1.0, over 5s max gap)
      7  aero_flag          (0.0 = corner_mode, 1.0 = straight_mode)
    """
    soc        = float(np.clip(state.get("soc_estimated",  0.85), 0.0, 1.0))
    throttle   = float(np.clip(state.get("throttle",       0.0),  0.0, 1.0))
    speed_norm = float(np.clip(state.get("speed",          0.0) / 350.0, 0.0, 1.0))
    corner_norm = float(state.get("corner_id", 0)) / 15.0
    lap_frac   = float(np.clip(state.get("lap_fraction",   0.0), 0.0, 1.0))
    e_delta    = float(state.get("energy_delta", 0.0))
    e_norm     = float(np.clip((e_delta + 0.01) / 0.02, 0.0, 1.0))  # [-0.01,0.01]→[0,1]
    gap_norm   = float(np.clip(state.get("gap_ahead", 0.0) / 5.0, 0.0, 1.0))
    aero_flag  = 1.0 if state.get("aero_state") == "straight_mode" else 0.0

    return [soc, throttle, speed_norm, corner_norm, lap_frac, e_norm, gap_norm, aero_flag]


def label_state(state: dict) -> str:
    """
    Determine the 'outcome' label for a historical state vector.
    'warning_fired' if this state would trigger a meaningful alert.
    'no_warning'    otherwise.
    """
    soc = state.get("soc_estimated", 1.0)
    flag = state.get("session_flag", "green")
    if soc < SOC_DANGER_THRESHOLD and flag == "green":
        return "warning_fired"
    if flag in ("sc", "vsc"):
        return "warning_fired"
    if state.get("cusum_soc_alarm", False):
        return "warning_fired"
    return "no_warning"


def build_from_fastf1(year: int = 2024, gp: str = "Bahrain",
                      session_type: str = "Race") -> tuple[np.ndarray, np.ndarray]:
    """
    Load a FastF1 session and extract feature vectors from all laps.
    Returns (vectors_float32, outcomes_str_array).

    Requires: pip install fastf1
    """
    try:
        import fastf1
    except ImportError:
        print("fastf1 not installed — using synthetic data instead")
        return _build_synthetic()

    print(f"Loading FastF1: {year} {gp} {session_type} ...")
    cache_dir = os.path.join(os.path.dirname(__file__), "..", ".ff1_cache")
    os.makedirs(cache_dir, exist_ok=True)
    fastf1.Cache.enable_cache(cache_dir)

    session = fastf1.get_session(year, gp, session_type)
    session.load(telemetry=True)

    vectors, outcomes = [], []
    soc_proxy = 0.85

    for driver_num in [1, 11, 44, 16]:   # VER, PER, HAM, LEC
        driver_laps = session.laps.pick_driver(driver_num)
        if driver_laps.empty:
            continue

        for _, lap in driver_laps.iterrows():
            try:
                tel = lap.get_car_data().add_distance()
            except Exception:
                continue

            for _, row in tel.iterrows():
                # Update soc proxy
                throttle = row.get("Throttle", 0) / 100.0
                brake    = bool(row.get("Brake", False))
                drs      = bool(row.get("DRS", 0) > 0)

                if throttle > 0.8 and not brake:
                    soc_proxy -= 0.003
                if brake:
                    soc_proxy += 0.002
                if drs:
                    soc_proxy -= 0.001
                soc_proxy -= 0.0005
                soc_proxy = max(0.0, min(1.0, soc_proxy))

                state = {
                    "soc_estimated":  soc_proxy,
                    "throttle":       throttle,
                    "speed":          float(row.get("Speed", 0)),
                    "corner_id":      0,    # simplified
                    "lap_fraction":   float(row.get("Distance", 0)) / 5412.0,  # Bahrain length
                    "energy_delta":   -0.003 if throttle > 0.8 else 0.001,
                    "gap_ahead":      0.0,
                    "aero_state":     "straight_mode" if drs else "corner_mode",
                    "session_flag":   "green",
                }

                vectors.append(extract_features_from_state(state))
                outcomes.append(label_state(state))

    if not vectors:
        print("No data extracted — using synthetic data")
        return _build_synthetic()

    print(f"Extracted {len(vectors)} feature vectors")
    return np.array(vectors, dtype=np.float32), np.array(outcomes)


def _build_synthetic(n: int = 500) -> tuple[np.ndarray, np.ndarray]:
    """
    Generate synthetic feature vectors when FastF1 is unavailable.
    Realistic distributions for demo/testing.
    """
    rng = np.random.default_rng(42)
    vectors, outcomes = [], []

    for _ in range(n):
        soc      = float(rng.uniform(0.1, 0.95))
        throttle = float(rng.uniform(0.0, 1.0))
        speed    = float(rng.uniform(50, 340)) / 350.0
        corner   = float(rng.integers(1, 16)) / 15.0
        lap_frac = float(rng.uniform(0, 1))
        e_delta  = float(rng.uniform(-0.01, 0.005))
        gap      = float(rng.uniform(0, 3)) / 5.0
        aero     = float(rng.choice([0.0, 1.0]))

        state = {
            "soc_estimated": soc,
            "throttle":      throttle,
            "speed":         speed * 350,
            "corner_id":     int(corner * 15),
            "lap_fraction":  lap_frac,
            "energy_delta":  e_delta,
            "gap_ahead":     gap * 5,
            "aero_state":    "straight_mode" if aero else "corner_mode",
            "session_flag":  "green",
        }

        vectors.append([soc, throttle, speed, corner, lap_frac,
                         (e_delta + 0.01) / 0.02, gap, aero])
        outcomes.append(label_state(state))

    print(f"Built {n} synthetic feature vectors")
    return np.array(vectors, dtype=np.float32), np.array(outcomes)


if __name__ == "__main__":
    out_dir = os.path.dirname(os.path.abspath(__file__))
    vectors, outcomes = build_from_fastf1()

    vec_path = os.path.join(out_dir, "vectors.npy")
    out_path = os.path.join(out_dir, "outcomes.npy")
    np.save(vec_path, vectors)
    np.save(out_path, outcomes)

    print(f"Saved {len(vectors)} vectors → {vec_path}")
    print(f"Saved outcomes → {out_path}")
    warning_count = sum(1 for o in outcomes if o == "warning_fired")
    print(f"Label balance: warning_fired={warning_count}, no_warning={len(outcomes)-warning_count}")
