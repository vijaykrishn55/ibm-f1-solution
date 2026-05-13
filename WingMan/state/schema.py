# state/schema.py
# ─────────────────────────────────────────────────────────────────────────────
# THE canonical state vector definition for WingMan.
# Person B owns this file. EVERYONE else imports from here.
# Never redefine this struct locally in any other module.
# ─────────────────────────────────────────────────────────────────────────────

import copy
import time


DEFAULT_STATE = {
    # ── Core metadata ──────────────────────────────────────────────────────
    "timestamp":          0.0,       # Unix time of this reading (float)
    "driver":             "",        # e.g. "VER", "HAM", or "TORCS_CAR_1"
    "lap":                0,         # Current lap number (int)
    "corner_id":          0,         # Which corner 1..N for this circuit (int)
    "lap_fraction":       0.0,       # 0.0 → 1.0  (float)

    # ── Motion ─────────────────────────────────────────────────────────────
    "speed":              0.0,       # km/h — Kalman-filtered (float)
    "throttle":           0.0,       # 0.0 → 1.0 (float)
    "brake":              False,     # True = braking
    "drs":                False,     # DRS open = True  (False for TORCS)
    "aero_state":         "corner_mode",  # "straight_mode" | "corner_mode"

    # ── Energy / SOC ───────────────────────────────────────────────────────
    "soc_raw":            0.85,      # Raw proxy SOC before Kalman
    "soc_estimated":      0.85,      # Kalman-filtered SOC  0.0 → 1.0
    "soc_uncertainty":    0.05,      # Kalman P[0][0] covariance
    "energy_delta":       0.0,       # MJ change since last reading

    # ── Race context ────────────────────────────────────────────────────────
    "gap_ahead":          0.0,       # Seconds to car ahead  (0.0 if unknown)
    "session_flag":       "green",   # "green"|"yellow"|"sc"|"vsc"|"red"

    # ── Data quality ────────────────────────────────────────────────────────
    "data_age_ms":        0,         # Age of reading in ms
    "data_source":        "unknown", # "openf1" | "torcs" | "mock"

    # ── Extension fields — filled by later modules, default None ───────────
    "radio_transcript":   None,      # GridSense fills this
    "complaint_detected": None,      # GridSense fills this
    "corner_direction":   None,      # TyreWhisperer fills this (future)
    "delta_from_optimal": None,      # GhostDelta fills this (future)

    # ── Fast-path computed fields (Person B adds these live) ───────────────
    "cusum_soc_alarm":    False,     # CUSUM SOC depletion alarm
    "cusum_speed_alarm":  False,     # CUSUM speed loss alarm
}


def new_state(**overrides) -> dict:
    """
    Create a fresh state vector with timestamp = now.
    Pass any field as a keyword argument to override the default.

    Example:
        s = new_state(driver="VER", lap=5, data_source="torcs")
    """
    s = copy.deepcopy(DEFAULT_STATE)
    s["timestamp"] = time.time()
    s.update(overrides)
    return s


def validate_state(s: dict) -> list[str]:
    """
    Light validation — returns list of warnings (empty = all good).
    Does NOT raise — callers decide what to do with warnings.
    """
    warnings = []
    required = list(DEFAULT_STATE.keys())
    for key in required:
        if key not in s:
            warnings.append(f"Missing field: {key}")

    if not (0.0 <= s.get("soc_estimated", 0) <= 1.0):
        warnings.append(f"soc_estimated out of range: {s.get('soc_estimated')}")
    if not (0.0 <= s.get("throttle", 0) <= 1.0):
        warnings.append(f"throttle out of range: {s.get('throttle')}")
    if s.get("data_age_ms", 0) < 0:
        warnings.append("data_age_ms is negative")

    return warnings
