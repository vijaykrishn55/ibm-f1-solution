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

# Day 0 mock state vectors for unit testing.
NORMAL = {**BASE}
SOC_DANGER = {**BASE, "soc_estimated": 0.22, "corner_id": 11}
LIFT_NOT_WORTH = {**BASE, "throttle": 0.18, "corner_id": 1}
GOOD_RECHARGE = {**BASE, "soc_estimated": 0.48, "throttle": 0.88, "corner_id": 10}
SAFETY_CAR = {**BASE, "session_flag": "sc", "soc_estimated": 0.65}
STALE_DATA = {**BASE, "data_age_ms": 2500}
TORCS_STATE = {**BASE, "drs": False, "data_source": "torcs", "soc_estimated": 0.61}

BATTERY_ZERO = {**BASE, "soc_estimated": 0.05, "energy_delta": -0.012, "corner_id": 12}
DRS_IN_CORNER = {**BASE, "corner_id": 7, "drs": True, "throttle": 0.25, "speed": 210.0}
RED_FLAG = {**BASE, "session_flag": "red", "speed": 60.0, "throttle": 0.05}
HIGH_GAP = {**BASE, "gap_ahead": 8.2, "throttle": 0.4, "corner_id": 3}
LOW_GAP = {**BASE, "gap_ahead": 0.3, "throttle": 0.45, "corner_id": 8}
LIFT_WINDOW = {**BASE, "corner_id": 14, "throttle": 0.78, "soc_estimated": 0.55}
ENERGY_PICKUP = {**BASE, "energy_delta": 0.006, "throttle": 0.12, "corner_id": 5}
BRAKE_ZONE = {**BASE, "brake": True, "throttle": 0.0, "speed": 150.0, "corner_id": 6}
HIGH_UNCERTAINTY = {**BASE, "soc_uncertainty": 0.18, "data_age_ms": 95}
LOW_UNCERTAINTY = {**BASE, "soc_uncertainty": 0.01, "data_age_ms": 20}
DRIVER_COMPLAINT = {**BASE, "radio_transcript": "Car is very loose on the rear.", "complaint_detected": "oversteer"}
SESSION_YELLOW = {**BASE, "session_flag": "yellow", "gap_ahead": 6.5, "soc_estimated": 0.53}
FAISS_MATCH = {**BASE, "corner_id": 10, "soc_estimated": 0.48, "throttle": 0.9, "gap_ahead": 1.2}

ALL_MOCKS = [
    NORMAL,
    SOC_DANGER,
    LIFT_NOT_WORTH,
    GOOD_RECHARGE,
    SAFETY_CAR,
    STALE_DATA,
    TORCS_STATE,
    BATTERY_ZERO,
    DRS_IN_CORNER,
    RED_FLAG,
    HIGH_GAP,
    LOW_GAP,
    LIFT_WINDOW,
    ENERGY_PICKUP,
    BRAKE_ZONE,
    HIGH_UNCERTAINTY,
    LOW_UNCERTAINTY,
    DRIVER_COMPLAINT,
    SESSION_YELLOW,
    FAISS_MATCH,
]

if __name__ == '__main__':
    for idx, scenario in enumerate(ALL_MOCKS, start=1):
        print(f"SCENARIO {idx}: corner={scenario['corner_id']} soc={scenario['soc_estimated']:.2f} "
              f"session={scenario['session_flag']} throttle={scenario['throttle']} "
              f"drs={scenario['drs']} gap={scenario['gap_ahead']}")
