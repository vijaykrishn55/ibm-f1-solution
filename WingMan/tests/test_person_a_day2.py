"""Person A Day 2 tests -- run from project root: python tests/test_person_a_day2.py

Tests Day 2 additions:
  - Mock server: session_status, flag injection, speed multiplier, health, reset
  - OpenF1 stream: session_flag wiring, build_state with flag param
  - Source manager: mode switching, stats
  - FastF1 loader: corner distance mapping, import check
  - Fixture data: car_data.json, position.json, intervals.json loaded correctly
"""

import os
import sys
import json
import time
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Fixture data tests ──────────────────────────────────────────────────────

def test_fixtures_loaded():
    """Verify all three fixture files exist and contain valid JSON arrays."""
    fixtures_dir = os.path.join("tests", "fixtures")
    for name in ("car_data.json", "position.json", "intervals.json"):
        path = os.path.join(fixtures_dir, name)
        assert os.path.exists(path), f"{name} not found at {path}"
        with open(path) as f:
            data = json.load(f)
        assert isinstance(data, list), f"{name} should be a JSON array"
        assert len(data) > 0, f"{name} should not be empty"
    print("Fixtures loaded: OK")


def test_car_data_fields():
    """Verify car_data fixture has expected OpenF1 fields."""
    with open(os.path.join("tests", "fixtures", "car_data.json")) as f:
        rows = json.load(f)
    row = rows[0]
    required = ["date", "driver_number", "speed", "throttle", "brake", "drs", "distance"]
    for field in required:
        assert field in row, f"car_data missing field: {field}"
    assert row["speed"] > 0, "Speed should be positive"
    assert 0 <= row["throttle"] <= 100, "Throttle should be 0-100"
    print("car_data fields: OK")


# ── Mock server tests ────────────────────────────────────────────────────────

def test_mock_server_session_status():
    """Verify mock server has session_status endpoint."""
    from ingestion.mock_server import app, session_state
    assert app is not None
    assert "flag" in session_state
    assert session_state["flag"] == "green"
    assert "speed_mult" in session_state
    print("mock_server session_status: OK")


def test_mock_server_flag_set():
    """Verify flag injection sets session_state correctly."""
    from ingestion.mock_server import set_flag, session_state
    result = set_flag("sc")
    assert result["status"] == "ok"
    assert session_state["flag"] == "sc"

    result = set_flag("green")
    assert session_state["flag"] == "green"

    result = set_flag("invalid")
    assert "error" in result
    print("mock_server flag injection: OK")


def test_mock_server_speed_mult():
    """Verify replay speed multiplier works."""
    from ingestion.mock_server import set_speed, session_state
    result = set_speed(4.0)
    assert result["status"] == "ok"
    assert session_state["speed_mult"] == 4.0

    # Clamp test
    set_speed(0.1)
    assert session_state["speed_mult"] == 0.25   # min clamp

    set_speed(20.0)
    assert session_state["speed_mult"] == 10.0   # max clamp

    set_speed(1.0)   # reset
    print("mock_server speed multiplier: OK")


def test_mock_server_reset():
    """Verify reset clears counters and session state."""
    from ingestion.mock_server import reset, counters, session_state
    counters["car"] = 10
    session_state["flag"] = "sc"
    session_state["total_ticks"] = 99
    result = reset()
    assert result["status"] == "reset"
    assert counters["car"] == 0
    assert session_state["flag"] == "green"
    assert session_state["total_ticks"] == 0
    print("mock_server reset: OK")


def test_mock_server_health():
    """Verify health endpoint returns fixture counts."""
    from ingestion.mock_server import health
    result = health()
    assert result["status"] == "ok"
    assert result["car_rows"] > 0
    assert result["pos_rows"] > 0
    assert result["int_rows"] > 0
    print("mock_server health: OK")


def test_mock_server_lap_estimate():
    """Verify car_data endpoint injects lap_number from counter."""
    from ingestion.mock_server import get_car_data, counters, car_data_rows, reset
    reset()
    row = get_car_data()
    assert "lap_number" in row, "car_data should include lap_number"
    assert row["lap_number"] == 1  # first pass = lap 1
    print("mock_server lap estimate: OK")


# ── OpenF1 stream tests ─────────────────────────────────────────────────────

def test_build_state_with_flag():
    """Verify build_state accepts session_flag parameter (Day 2 wiring)."""
    from ingestion.openf1_stream import build_state, load_corner_map
    corner_map = load_corner_map("bahrain")

    raw = {
        "speed": 285, "throttle": 92, "brake": False, "drs": 8,
        "driver_number": 1, "lap_number": 15, "distance": 999,
        "gap_to_leader": "+1.4",
    }

    # Test with green flag
    state_green = build_state(raw, corner_map, session_flag="green")
    assert state_green["session_flag"] == "green"

    # Test with safety car flag
    state_sc = build_state(raw, corner_map, session_flag="sc")
    assert state_sc["session_flag"] == "sc"

    # Test with VSC flag
    state_vsc = build_state(raw, corner_map, session_flag="vsc")
    assert state_vsc["session_flag"] == "vsc"

    print("build_state session_flag: OK")


def test_fetch_session_status_import():
    """Verify fetch_session_status function exists and is importable."""
    from ingestion.openf1_stream import fetch_session_status
    assert callable(fetch_session_status)
    print("fetch_session_status import: OK")


# ── Source manager tests ─────────────────────────────────────────────────────

def test_source_manager_init():
    """Verify SourceManager initializes with correct modes."""
    from ingestion.source_manager import SourceManager
    q = asyncio.Queue()

    mgr = SourceManager(q, mode="openf1", circuit="bahrain")
    assert mgr.mode == "openf1"
    assert mgr.queue is q

    mgr2 = SourceManager(q, mode="both")
    assert mgr2.mode == "both"

    stats = mgr.stats()
    assert stats["mode"] == "openf1"
    assert stats["queue_size"] == 0
    print("SourceManager init: OK")


def test_source_manager_stats():
    """Verify stats dict contains expected keys."""
    from ingestion.source_manager import SourceManager
    q = asyncio.Queue()
    mgr = SourceManager(q, mode="torcs")
    stats = mgr.stats()
    expected_keys = {"mode", "active_sources", "queue_size", "n_tasks"}
    assert expected_keys.issubset(set(stats.keys()))
    print("SourceManager stats: OK")


# ── FastF1 loader tests ─────────────────────────────────────────────────────

def test_fastf1_loader_import():
    """Verify fastf1_loader is importable with expected functions."""
    from ingestion.fastf1_loader import (
        distance_to_corner_id,
        extract_states_from_session,
        export_to_csv,
        export_corner_distances,
    )
    assert callable(distance_to_corner_id)
    assert callable(extract_states_from_session)
    print("fastf1_loader import: OK")


def test_fastf1_corner_distance():
    """Verify distance_to_corner_id mapping is correct."""
    from ingestion.fastf1_loader import distance_to_corner_id, BAHRAIN_TRACK_LENGTH

    assert distance_to_corner_id(0) == 1
    assert distance_to_corner_id(BAHRAIN_TRACK_LENGTH / 2) == 8
    assert distance_to_corner_id(BAHRAIN_TRACK_LENGTH - 1) == 15

    # Wrap around
    assert distance_to_corner_id(BAHRAIN_TRACK_LENGTH + 100) == 1

    print("fastf1_loader corner distance: OK")


# ── Run all ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("Person A - Day 2 Tests")
    print("=" * 60)

    # Fixtures
    test_fixtures_loaded()
    test_car_data_fields()

    # Mock server
    test_mock_server_session_status()
    test_mock_server_flag_set()
    test_mock_server_speed_mult()
    test_mock_server_reset()
    test_mock_server_health()
    test_mock_server_lap_estimate()

    # OpenF1 stream
    test_build_state_with_flag()
    test_fetch_session_status_import()

    # Source manager
    test_source_manager_init()
    test_source_manager_stats()

    # FastF1 loader
    test_fastf1_loader_import()
    test_fastf1_corner_distance()

    print("\n" + "=" * 60)
    print("All Person A Day 2 tests passed. PASS")
    print("=" * 60)
