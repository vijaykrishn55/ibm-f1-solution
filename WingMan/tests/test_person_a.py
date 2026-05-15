"""Person A Day 1 tests — run from project root: python tests/test_person_a.py"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import json


def test_corner_mapping():
    from ingestion.openf1_stream import load_corner_map, get_corner_id
    corner_map = load_corner_map("bahrain")
    assert corner_map, "Corner map should not be empty"

    assert get_corner_id(0,    corner_map) == 1,  "Start of lap = corner 1"
    assert get_corner_id(999,  corner_map) == 4,  "Mid lap = corner 4"
    assert get_corner_id(2700, corner_map) == 11, "Boost zone = corner 11"
    assert get_corner_id(9999, corner_map) == 0,  "Out of range = 0"
    print("Corner mapping: OK")


def test_build_state():
    from ingestion.openf1_stream import build_state, load_corner_map
    corner_map = load_corner_map("bahrain")

    raw = {
        "speed": 285,
        "throttle": 92,
        "brake": False,
        "drs": 8,
        "driver_number": 1,
        "lap_number": 15,
        "distance": 999,
        "gap_to_leader": "+1.4",
    }

    state = build_state(raw, corner_map)
    assert state["speed"]    == 285.0
    assert state["throttle"] == 0.92
    assert state["brake"]    == False
    assert state["drs"]      == True
    assert state["corner_id"] == 4
    assert state["data_source"] == "openf1"
    assert state["soc_estimated"] == 0.0   # Person B fills this
    print("build_state: OK")


def test_torcs_to_state():
    from ingestion.torcs_adapter import torcs_to_state

    sensors = {
        "speedX": 55.0,      # ~198 km/h
        "accel":  0.85,
        "brake":  0.0,
        "fuel":   75.0,
        "distFromStart": 999.0,
        "opponents": [50.0, 30.0, 100.0],
        "racePos": 3,
    }

    state = torcs_to_state(sensors)
    assert state["speed"]       > 190,        "Speed should be ~198 km/h"
    assert state["throttle"]    == 0.85
    assert state["brake"]       == False
    assert state["drs"]         == False      # TORCS has no DRS
    assert state["soc_raw"]     == round(75.0 / 94.0, 3)
    assert state["corner_id"]   == 4
    assert state["gap_ahead"]   == 30.0       # nearest opponent
    assert state["data_source"] == "torcs"
    print("torcs_to_state: OK")


def test_mock_server_imports():
    from ingestion.mock_server import app, counters
    assert app is not None
    assert "car" in counters
    print("mock_server import: OK")


if __name__ == "__main__":
    test_corner_mapping()
    test_build_state()
    test_torcs_to_state()
    test_mock_server_imports()
    print("\nAll Person A tests passed.")