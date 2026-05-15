"""Mock Server: replays saved OpenF1 JSON at 4Hz for offline testing.

Day 2 additions:
  - GET /v1/session_status — returns current session flag state
  - POST /v1/flag/{flag}   — set session flag (green/yellow/sc/vsc/red)
  - GET /v1/speed          — replay speed control query
  - POST /v1/speed/{mult}  — set replay speed multiplier (1x, 2x, 4x)
  - GET /v1/stats          — current replay stats (ticks, lap estimate)
"""

import json
import os
import time
from fastapi import FastAPI

app = FastAPI(title="WingMan Mock OpenF1 Server")

# --- Load fixture data at startup ---

def load_fixture(filename: str) -> list:
    path = os.path.join("tests", "fixtures", filename)
    if not os.path.exists(path):
        print(f"[MockServer] Warning: {path} not found, using empty list")
        return []
    with open(path) as f:
        data = json.load(f)
    return data if isinstance(data, list) else [data]


car_data_rows    = load_fixture("car_data.json")
position_rows    = load_fixture("position.json")
intervals_rows   = load_fixture("intervals.json")

counters = {"car": 0, "pos": 0, "int": 0}

# --- Session state (Day 2) ---

session_state = {
    "flag":           "green",       # green | yellow | sc | vsc | red
    "speed_mult":     1.0,           # replay speed multiplier
    "started_at":     time.time(),
    "total_ticks":    0,
}


# --- Endpoints ---

@app.get("/v1/car_data")
def get_car_data():
    if not car_data_rows:
        return {}
    row = car_data_rows[counters["car"] % len(car_data_rows)]
    counters["car"] += 1
    session_state["total_ticks"] += 1

    # Inject a lap_number estimate from counter position in the fixture
    lap_estimate = counters["car"] // max(len(car_data_rows), 1) + 1
    enriched = {**row, "lap_number": lap_estimate}
    return enriched


@app.get("/v1/position")
def get_position():
    if not position_rows:
        return {}
    row = position_rows[counters["pos"] % len(position_rows)]
    counters["pos"] += 1
    return row


@app.get("/v1/intervals")
def get_intervals():
    if not intervals_rows:
        return {}
    row = intervals_rows[counters["int"] % len(intervals_rows)]
    counters["int"] += 1
    return row


@app.get("/v1/session_status")
def get_session_status():
    """Returns current session flag state — wired into openf1_stream on Day 2."""
    return {
        "flag":         session_state["flag"],
        "speed_mult":   session_state["speed_mult"],
        "total_ticks":  session_state["total_ticks"],
        "lap_estimate": counters["car"] // max(len(car_data_rows), 1) + 1,
    }


@app.post("/v1/flag/{flag}")
def set_flag(flag: str):
    """Simulate session flag changes for testing (sc, vsc, yellow, red, green)."""
    valid = {"green", "yellow", "sc", "vsc", "red"}
    if flag not in valid:
        return {"error": f"Invalid flag '{flag}', must be one of {valid}"}
    session_state["flag"] = flag
    print(f"[MockServer] Session flag -> {flag}")
    return {"status": "ok", "flag": flag}


@app.get("/v1/speed")
def get_speed():
    """Query current replay speed multiplier."""
    return {"speed_mult": session_state["speed_mult"]}


@app.post("/v1/speed/{mult}")
def set_speed(mult: float):
    """Set replay speed multiplier (e.g. 4.0 for 4x speed replay test)."""
    session_state["speed_mult"] = max(0.25, min(mult, 10.0))
    print(f"[MockServer] Replay speed -> {session_state['speed_mult']}x")
    return {"status": "ok", "speed_mult": session_state["speed_mult"]}


@app.post("/v1/reset")
def reset():
    counters["car"] = 0
    counters["pos"]  = 0
    counters["int"]  = 0
    session_state["flag"]        = "green"
    session_state["total_ticks"] = 0
    session_state["started_at"]  = time.time()
    print("[MockServer] Counters and session state reset")
    return {"status": "reset"}


@app.get("/v1/health")
def health():
    """Health check endpoint for integration tests."""
    return {
        "status":      "ok",
        "car_rows":    len(car_data_rows),
        "pos_rows":    len(position_rows),
        "int_rows":    len(intervals_rows),
        "total_ticks": session_state["total_ticks"],
    }