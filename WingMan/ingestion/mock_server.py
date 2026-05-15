"""Mock Server: replays saved OpenF1 JSON at 4Hz for offline testing."""

import json
import os
from fastapi import FastAPI

app = FastAPI()

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


# --- Endpoints ---

@app.get("/v1/car_data")
def get_car_data():
    if not car_data_rows:
        return {}
    row = car_data_rows[counters["car"] % len(car_data_rows)]
    counters["car"] += 1
    return row


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


@app.post("/v1/reset")
def reset():
    counters["car"] = 0
    counters["pos"]  = 0
    counters["int"]  = 0
    return {"status": "reset"}