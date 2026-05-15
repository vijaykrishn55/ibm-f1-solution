"""
Hand-crafted mock state vectors for Day 0 testing.
Create ~20 realistic vectors here for unit tests and component integration.
"""
import time

BASE = {
    "timestamp": time.time(),
    "driver": "VER",
    "lap": 15,
    "corner_id": 4,
    "lap_fraction": 0.31,
    "speed": 285.0,
    "throttle": 0.92,
    "brake": False,
    "drs": True,
    "aero_state": "straight_mode",
    "soc_raw": 0.72,
    "soc_estimated": 0.72,
    "soc_uncertainty": 0.03,
    "energy_delta": -0.002,
    "gap_ahead": 1.4,
    "session_flag": "green",
    "data_age_ms": 80,
    "data_source": "mock",
    "radio_transcript": None,
    "complaint_detected": None,
    "corner_direction": None,
    "delta_from_optimal": None,
}

# Scenario: Normal racing — should fire safe_default
NORMAL = {**BASE}

# Scenario: SOC danger at boost zone — should fire soc_danger_alert
SOC_DANGER = {**BASE, "soc_estimated": 0.22, "corner_id": 11}

# Scenario: Lifting when it's not worth it — should fire lift_not_worth_it
LIFT_NOT_WORTH = {**BASE, "throttle": 0.18, "corner_id": 1}

# Scenario: Good recharge window — should fire optimal_recharge_window
GOOD_RECHARGE = {**BASE, "soc_estimated": 0.48, "throttle": 0.88, "corner_id": 10}

# Scenario: Safety car — should fire safety_car_recharge (priority 10)
SAFETY_CAR = {**BASE, "session_flag": "sc", "soc_estimated": 0.65}

# Scenario: Stale data — should trigger safe fallback
STALE_DATA = {**BASE, "data_age_ms": 2500}

# Scenario: TORCS source data — no DRS, SOC simulated from fuel
TORCS_STATE = {**BASE, "drs": False, "data_source": "torcs", "soc_estimated": 0.61}

# Scenario: Battery near zero
BATTERY_CRITICAL = {**BASE, "soc_estimated": 0.04, "corner_id": 12}

# Scenario: Red flag
RED_FLAG = {**BASE, "session_flag": "red", "soc_estimated": 0.55}

# Scenario: DRS in corner (bad data) — should be handled
DRS_IN_CORNER = {**BASE, "drs": True, "corner_id": 5, "lap_fraction": 0.78}

# Scenario: CUSUM-like fast depletion (energy_delta spike)
CUSUM_ALARM = {
    **BASE,
    "soc_estimated": 0.41,
    "energy_delta": -0.015,
    "corner_id": 7,
    "cusum_soc_alarm": True,
}

# Create additional variants across corners and laps to reach ~20 vectors.
EXTRA_SCENARIOS = []
for c in range(1, 9):
    EXTRA_SCENARIOS.append({**BASE, "corner_id": c, "lap": 10 + c, "lap_fraction": (c * 0.11) % 1.0})

# Export list used by unit tests
MOCK_STATE_VECTORS = [
    NORMAL,
    SOC_DANGER,
    LIFT_NOT_WORTH,
    GOOD_RECHARGE,
    SAFETY_CAR,
    STALE_DATA,
    TORCS_STATE,
    BATTERY_CRITICAL,
    CUSUM_ALARM,
    RED_FLAG,
    DRS_IN_CORNER,
] + EXTRA_SCENARIOS

if __name__ == "__main__":
    # Quick sanity run to print counts
    print(f"Loaded {len(MOCK_STATE_VECTORS)} mock state vectors")

ALL_SCENARIOS = [
    ("NORMAL",        NORMAL),
    ("SOC_DANGER",    SOC_DANGER),
    ("LIFT_NOT_WORTH",LIFT_NOT_WORTH),
    ("GOOD_RECHARGE", GOOD_RECHARGE),
    ("SAFETY_CAR",    SAFETY_CAR),
    ("STALE_DATA",    STALE_DATA),
    ("TORCS_STATE",   TORCS_STATE),
    ("CUSUM_ALARM",   CUSUM_ALARM),
]