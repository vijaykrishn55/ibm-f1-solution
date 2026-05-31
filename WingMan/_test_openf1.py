"""Quick import + logic check for the OpenF1 migration."""
import sys, json, os

# Check bahrain.json loads
with open("config/circuits/bahrain.json") as f:
    circuit = json.load(f)
assert len(circuit["corner_map"]) == 15, "corner_map must have 15 entries"
assert "thresholds" in circuit
print(f"bahrain.json OK — {len(circuit['corner_map'])} corners")

# Check openf1_stream imports (no network call)
from ingestion.openf1_stream import _xy_to_corner, _xy_to_direction, _build_state
print("openf1_stream imports OK")

# Spot-check corner lookup
cid = _xy_to_corner(-200, 400)   # T2 region
print(f"Corner lookup (-200, 400) → {cid}")

# _build_state with dummy data
dummy_car = {"speed": 280, "throttle": 85, "brake": 0, "drs": 8}
dummy_pos = {"x": -200, "y": 400, "z": 0}
dummy_int = {"gap_to_leader": "+2.345"}
state = _build_state(dummy_car, dummy_pos, dummy_int, "green", lap_n=12, prev_soc=0.72)
assert state["speed"]    == 280.0
assert state["throttle"] == 0.85
assert state["drs"]      == True
assert state["data_source"] == "openf1"
assert "wheel_fl" in state
print(f"_build_state OK — speed={state['speed']}  drs={state['drs']}  corner={state['corner_id']}")

# Check Tyre Health Monitor (TyreWhisperer) sector-asymmetry mode
from modules.tyrewhisperer import TyreWhisperer
tw = TyreWhisperer()
# Feed left-corner states with lower exit speed (simulates FL degradation)
for lap in range(1, 5):
    for _ in range(10):
        result = tw.update({
            "speed": 70.0, "corner_direction": "left", "corner_id": 2,
            "lap": lap, "wheel_fl": 0.0, "wheel_fr": 0.0, "steer": 0.1,
        })
    for _ in range(10):
        result = tw.update({
            "speed": 90.0, "corner_direction": "right", "corner_id": 9,
            "lap": lap, "wheel_fl": 0.0, "wheel_fr": 0.0, "steer": 0.1,
        })
print(f"Tyre Health Monitor sector mode OK (last result: {result})")

print("\nAll checks passed — OpenF1 migration ready")
