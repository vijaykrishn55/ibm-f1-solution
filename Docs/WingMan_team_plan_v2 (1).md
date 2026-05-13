# WingMan — Parallel Team Build Plan v2
### 3 on VoltEdge · 2 on GridSense · 3–4 Days · All Parallel · TORCS Live Testing

---

## Read This First — Everyone (30 min together before Day 1)

**What WingMan is:**  
A real-time AI system that monitors F1 car energy and aerodynamics and tells engineers and drivers what to do — in under 100ms — faster than any human can react.

**The two paths:**
- **Fast path** — deterministic, runs every 250ms, no AI in the loop, must respond under 100ms
- **Slow path** — IBM Granite AI reasons in the background every few laps, updates fast path parameters

**The golden rule:**  
The fast path never waits for AI. AI runs separately. Fast path uses AI's outputs, never calls AI directly.

**How the 5 of you split:**
- **Persons A, B, C** — build VoltEdge (energy + aero intelligence core)
- **Persons D, E** — build GridSense (driver radio complaint detection) in parallel

These two tracks run at the same time. They connect on Day 2 at the integration merge.

---

## The State Vector — Read and Agree Before Anyone Writes Code

This is the single contract between all 5 members. Nobody modifies fields without a group decision.

**Print this. Stick it on the wall.**

```python
# wingman/state/schema.py — this file is Person B's to create on Day 1
# Everyone else imports from it. Never define this struct locally.

STATE_VECTOR = {
    "timestamp":          float,   # Unix time of this reading
    "driver":             str,     # e.g. "VER", "HAM", or "TORCS_CAR_1"
    "lap":                int,     # Current lap number
    "corner_id":          int,     # Which corner (1 to N for this circuit)
    "lap_fraction":       float,   # 0.0 to 1.0
    "speed":              float,   # km/h — Kalman filtered
    "throttle":           float,   # 0.0 to 1.0
    "brake":              bool,    # True or False
    "drs":                bool,    # DRS open = True (False if TORCS source)
    "aero_state":         str,     # "straight_mode" or "corner_mode"
    "soc_raw":            float,   # Raw estimated SOC before filtering
    "soc_estimated":      float,   # Kalman-filtered SOC (0.0 to 1.0)
    "soc_uncertainty":    float,   # Kalman confidence in SOC estimate
    "energy_delta":       float,   # MJ change since last reading
    "gap_ahead":          float,   # Seconds to car ahead (0.0 if unknown)
    "session_flag":       str,     # "green", "yellow", "sc", "vsc", "red"
    "data_age_ms":        int,     # How old is this reading in milliseconds
    "data_source":        str,     # "openf1", "torcs", "mock" — track the origin

    # Extension fields — filled by later modules, default None
    "radio_transcript":   None,    # GridSense fills this
    "complaint_detected": None,    # GridSense fills this
    "corner_direction":   None,    # TyreWhisperer fills this (future)
    "delta_from_optimal": None,    # GhostDelta fills this (future)
}
```

**Integration contracts — what each person receives and produces:**

```
Person A  produces: raw state vector dict (soc_estimated = 0.0, unfilled)
          → puts onto asyncio.Queue every 250ms
          consumed by: Person B (reads from queue for Kalman + fast path logic)

Person B  produces: enriched state vector (soc_estimated filled)
                    alert dict {rule, recommendation, reason, priority, confidence}
          consumed by: Person C (alert builder + output)
                       Person E (GridSense rule additions)

Person C  produces: WebSocket push (JSON alert) → UI
                    Audio file path for TTS
                    Fan explanation text (from Granite)
          consumed by: Browser UI + driver audio

Person D  produces: {radio_transcript: str, complaint_detected: str or None}
          consumed by: Person E (plugs into GridSense rules)

Person E  produces: GridSense rule outputs (same format as Person B alert dict)
                    GridSense UI panel content
          consumed by: Person C (alert builder handles both VoltEdge + GridSense alerts)
```

---

## Day 0 — Setup (2 hours, all 5 together)

Do this together the evening before or first thing Day 1 morning.

### Step 1 — Repo setup (20 min, one person shares screen)

```bash
mkdir WingMan && cd WingMan
git init
git checkout -b main
```

Create the full folder structure (one person types, everyone watches):

```
WingMan/
├── config/
│   ├── circuits/
│   │   └── bahrain.json
│   └── settings.yaml
├── ingestion/
│   ├── openf1_stream.py
│   ├── torcs_adapter.py        # NEW — TORCS live data adapter
│   ├── fastf1_loader.py
│   └── mock_server.py
├── state/
│   ├── schema.py               # STATE_VECTOR definition — everyone imports this
│   ├── kalman.py
│   ├── window.py
│   └── session_state.py
├── fast_path/
│   ├── cusum.py
│   ├── faiss_index.py
│   ├── rules_engine.py
│   └── confidence.py
├── slow_path/
│   ├── event_queue.py
│   ├── mpc_planner.py
│   ├── granite_client.py
│   └── context_forge.py
├── gridsense/
│   ├── radio_ingestion.py      # Person D
│   ├── complaint_detector.py   # Person D
│   └── gridsense_rules.py      # Person E
├── output/
│   ├── alert_builder.py
│   ├── tts.py
│   └── websocket_server.py
├── ui/
│   └── index.html
├── offline/
│   ├── feature_extract.py
│   └── build_index.py
├── tests/
│   ├── fixtures/               # Saved API responses go here
│   └── mock_state_vectors.py   # 20 hand-crafted test vectors — Day 0 task
└── requirements.txt
```

Everyone pushes the empty structure:
```bash
git add .
git commit -m "project skeleton"
git push origin main
```

Everyone clones and creates their own branch:
```bash
git checkout -b dev/<your-name>   # e.g. dev/alice, dev/bob
```

**Rule:** Never push directly to main. Merge happens at end of Day 1 and Day 2.

---

### Step 2 — Install dependencies (30 min, everyone independently)

```bash
pip install fastf1 httpx filterpy faiss-cpu scipy scikit-learn pandas numpy websockets fastapi uvicorn pyttsx3 gTTS redis pytest pytest-asyncio
```

Verify:
```python
import fastf1, faiss, filterpy, httpx, scipy, sklearn
print("All good")
```

Fix any failures before Day 1 starts.

---

### Step 3 — Create mock state vectors together (30 min, everyone)

This is Day 0's most important task. Everyone writes and agrees on `tests/mock_state_vectors.py`.

This file contains 20 hand-crafted dicts that represent real situations the system will face. Every person uses these to test their component without needing other people's code to run.

```python
# tests/mock_state_vectors.py
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

# Add 13 more scenarios covering: battery near zero, CUSUM alarm,
# red flag, DRS in corner, all corners (1-15), driver complaint
```

Everyone commits this file before going home Day 0.

---

### Step 4 — Create bahrain.json together (20 min)

```json
{
  "circuit": "bahrain",
  "corners": 15,
  "boost_zone_corners": [11, 12],
  "soc_danger_threshold": 0.25,
  "corner_thresholds": {
    "1":  {"net_lift_value": -0.02, "lift_energy_gain": 0.12, "lift_aero_cost_s": 0.14},
    "4":  {"net_lift_value":  0.08, "lift_energy_gain": 0.18, "lift_aero_cost_s": 0.10},
    "10": {"net_lift_value":  0.15, "lift_energy_gain": 0.22, "lift_aero_cost_s": 0.07},
    "11": {"net_lift_value":  0.03, "lift_energy_gain": 0.09, "lift_aero_cost_s": 0.06},
    "14": {"net_lift_value":  0.11, "lift_energy_gain": 0.19, "lift_aero_cost_s": 0.08}
  }
}
```

This is a draft — Person B will calibrate with real FastF1 data on Day 1.

---

## Day 1 — VoltEdge Track (A, B, C work in parallel)

All 3 build solo today. No one waits for anyone else.  
Everyone tests against `tests/mock_state_vectors.py`.  
End of Day 1: 30-minute merge. A + B combine first. Then C connects to B's output.

---

### Person A — Data & Ingestion

**Goal:** State vectors flowing from two sources — mock OpenF1 replay AND TORCS live simulator.

**Why two sources:** TORCS gives you a live car sending real-time signals you can interact with. OpenF1 gives you real F1 race history. Both feed the same pipeline.

---

#### Task A1 — Download and save cached OpenF1 data (1 hour)

Hit these URLs in your browser and save the responses:

```
https://api.openf1.org/v1/car_data?session_key=9158&driver_number=1
https://api.openf1.org/v1/position?session_key=9158&driver_number=1
https://api.openf1.org/v1/intervals?session_key=9158&driver_number=1
```

Save as: `tests/fixtures/car_data.json`, `position.json`, `intervals.json`

---

#### Task A2 — Build the mock replay server (1.5 hours)

File: `ingestion/mock_server.py`

This is a FastAPI app with one endpoint: `GET /v1/car_data`

How it works:
- On startup, load all rows from `tests/fixtures/car_data.json` into a list
- Keep a counter starting at 0
- On each GET request: return `rows[counter % len(rows)]`, increment counter
- Returns one row per request — simulating live data at 4Hz

Start command: `uvicorn ingestion.mock_server:app --port 8000`

Additional endpoints to add:
- `GET /v1/position` — same pattern from `position.json`
- `GET /v1/intervals` — same pattern from `intervals.json`
- `POST /v1/reset` — resets counter to 0 (useful for test reruns)

---

#### Task A3 — Build the OpenF1 polling client (1.5 hours)

File: `ingestion/openf1_stream.py`

This is an async loop that:
1. Every 250ms: hits `http://localhost:8000/v1/car_data`, `position`, and `intervals`
2. Merges the three responses into one dict
3. Builds a **partial** state vector from the merged dict
4. Puts it onto `asyncio.Queue`

Fields you fill from raw data:
```
timestamp, driver, speed, throttle, brake, drs,
lap_fraction, gap_ahead, session_flag, data_age_ms, data_source="openf1"
```

Fields you leave as defaults:
```
soc_estimated = 0.0    (Person B fills this via Kalman)
corner_id = 0          (Task A4 fills this)
aero_state = "straight_mode"  (derive: if drs=True → straight_mode, else corner_mode)
```

---

#### Task A4 — TORCS live adapter (2 hours)

**What TORCS is:** IBM's hands-on lab provides a racing car simulator at  
`https://github.com/IBM-SkillsBuild-AI-Builders-Challenge/hands-on-labs/tree/main/01_torcs_lab`

Follow the lab README to get TORCS running. Once running, it exposes car telemetry that you read and convert to our state vector format.

File: `ingestion/torcs_adapter.py`

**What TORCS gives you** (from the lab's sensor data):
```
speed_x        → speed in km/h (multiply by 3.6 if in m/s)
accel          → maps to throttle (0.0 to 1.0)
brake          → maps to brake (0.0 to 1.0, threshold 0.1 → bool)
gear           → current gear
track_pos      → position on track (-1 to 1, 0 = centre)
dist_from_start → distance raced in metres (use for lap_fraction)
fuel           → fuel level (use as SOC proxy — fuel/max_fuel)
opponents      → distances to nearby cars (nearest → gap_ahead)
```

**What TORCS does NOT give you (and how to handle it):**
```
drs            → hardcode False (TORCS has no DRS)
data_source    → set to "torcs"
session_flag   → default "green" (no flags in TORCS)
corner_id      → derive from track_pos + dist_from_start bucket (Task A5)
```

**TORCS SOC proxy:**
```python
soc_raw = state["fuel"] / MAX_FUEL   # Treat fuel level as battery level
```
This is fine for demo — the Kalman filter will smooth it the same way.

**Build the adapter as a class:**
```python
class TORCSAdapter:
    def __init__(self, host="localhost", port=3001):
        ...
    async def read_tick(self) -> dict:
        # reads one TORCS sensor frame and returns partial state vector
        ...
    async def stream(self, queue: asyncio.Queue):
        # runs forever, putting state vectors onto queue every 250ms
        ...
```

---

#### Task A5 — Corner ID mapping (45 min)

For TORCS: divide `dist_from_start` by total track length into 15 equal buckets.  
```python
corner_id = int((dist_from_start % TRACK_LENGTH) / (TRACK_LENGTH / 15)) + 1
```

For OpenF1 / Bahrain: load from FastF1:
```python
import fastf1
session = fastf1.get_session(2024, 'Bahrain', 'FP1')
session.load(telemetry=True)
lap = session.laps.pick_driver('VER').iloc[5]
tel = lap.get_car_data().add_distance()
# tel has X, Y, Distance columns — use Distance ranges to define 15 corner zones
```

Save as `config/circuits/bahrain_corners.json`:
```json
{"1": {"dist_min": 0, "dist_max": 400}, "2": {...}, ...}
```

---

**End of Day 1 — Person A checkpoint:**

Run mock server. Run polling client. Show: state vectors printing to console every 250ms  
with correct speed, throttle, brake, and a valid corner_id.  
TORCS: if TORCS is running, show TORCS state vectors also flowing.  
All fields present. soc_estimated = 0.0 is fine.

---

### Person B — Fast Path Brain

**Goal:** Kalman filter enriches state vectors. CUSUM + Rules + Confidence fire on mock data.  
Person B is the hub — A feeds into you, you feed into C.

**Work entirely from `tests/mock_state_vectors.py`. You do not need A's server running.**

---

#### Task B1 — Create `state/schema.py` (15 min)

This is your most important task. Create the canonical state vector definition.  
Import this everywhere instead of redefining the structure.

```python
# state/schema.py
import copy, time

DEFAULT_STATE = {
    "timestamp": 0.0,
    "driver": "",
    "lap": 0,
    "corner_id": 0,
    "lap_fraction": 0.0,
    "speed": 0.0,
    "throttle": 0.0,
    "brake": False,
    "drs": False,
    "aero_state": "corner_mode",
    "soc_raw": 0.85,
    "soc_estimated": 0.85,
    "soc_uncertainty": 0.05,
    "energy_delta": 0.0,
    "gap_ahead": 0.0,
    "session_flag": "green",
    "data_age_ms": 0,
    "data_source": "unknown",
    "radio_transcript": None,
    "complaint_detected": None,
    "corner_direction": None,
    "delta_from_optimal": None,
}

def new_state(**overrides) -> dict:
    s = copy.deepcopy(DEFAULT_STATE)
    s["timestamp"] = time.time()
    s.update(overrides)
    return s
```

Commit `state/schema.py` immediately and tell the team it's live.

---

#### Task B2 — Build Kalman filter (2 hours)

File: `state/kalman.py`

Class: `BatterySOCEstimator`

You have no direct SOC sensor. You estimate it from throttle, brake, speed.

**Proxy SOC calculation (run this every tick before feeding Kalman):**
```
proxy_soc starts at 0.85
each tick:
  if throttle > 0.8 and brake == False:  proxy_soc -= 0.003
  if brake == True:                       proxy_soc += 0.002
  if drs == True:                         proxy_soc -= 0.001
  always:                                 proxy_soc -= 0.0005
  clamp: proxy_soc = max(0.0, min(1.0, proxy_soc))
```

**Kalman setup using filterpy:**
```python
from filterpy.kalman import KalmanFilter
kf = KalmanFilter(dim_x=2, dim_z=1)
kf.x = [[0.85], [0.0]]    # Initial state: [soc, soc_change_rate]
kf.F = [[1, 1], [0, 1]]   # State transition: soc updates by rate each tick
kf.H = [[1, 0]]            # We measure soc directly
kf.R = [[0.01]]            # Measurement noise
kf.Q = [[0.001, 0], [0, 0.001]]  # Process noise
kf.P = [[1, 0], [0, 1]]   # Initial uncertainty
```

**Each tick:**
```python
kf.predict()
kf.update([[proxy_soc]])
soc_estimated = kf.x[0][0]
soc_uncertainty = kf.P[0][0]
```

**Output:** writes `soc_estimated` and `soc_uncertainty` back into the state vector dict.

Test: create 100 fake proxy_soc values with noise. Run through Kalman.  
Plot raw vs filtered with matplotlib. Filtered line should be noticeably smoother.

---

#### Task B3 — Build sliding window (45 min)

File: `state/window.py`

Class: `CornerWindow`

```python
from collections import defaultdict, deque

class CornerWindow:
    def __init__(self, maxlen=5):
        self.windows = defaultdict(lambda: deque(maxlen=maxlen))
    
    def push(self, corner_id: int, soc: float, speed: float):
        self.windows[corner_id].append({"soc": soc, "speed": speed})
    
    def mean_soc(self, corner_id: int) -> float: ...
    def soc_trend(self, corner_id: int) -> float:
        # last value minus first value — positive = recovering, negative = depleting
        ...
    def speed_trend(self, corner_id: int) -> float: ...
```

---

#### Task B4 — Build CUSUM detector (1 hour)

File: `fast_path/cusum.py`

```python
class CUSUMDetector:
    def __init__(self, expected_value: float, threshold: float):
        self.expected = expected_value
        self.threshold = threshold
        self.cumsum = 0.0
    
    def update(self, actual_value: float) -> bool:
        deviation = actual_value - self.expected
        self.cumsum = max(0.0, self.cumsum + deviation)
        if self.cumsum > self.threshold:
            self.cumsum = 0.0
            return True   # alarm fires
        return False
```

Create two instances:
```python
cusum_soc   = CUSUMDetector(expected_value=-0.003, threshold=0.015)  # SOC depletion rate
cusum_speed = CUSUMDetector(expected_value=0.0,    threshold=5.0)    # Speed loss per corner
```

Call `cusum_soc.update(energy_delta)` each tick. Write result to state vector  
as `state["cusum_soc_alarm"] = cusum_soc.update(...)`.

---

#### Task B5 — Build rules engine (2 hours)

File: `fast_path/rules_engine.py`

Takes enriched state vector + circuit config. Returns highest-priority rule that fires.

**Five core rules:**

```
Rule: soc_danger_alert
  When: soc_estimated < config["soc_danger_threshold"]
        AND corner_id in config["boost_zone_corners"]
        AND session_flag == "green"
  Returns: recommendation "Recharge immediately — boost zone in {X} corners"
  Priority: 9

Rule: lift_not_worth_it
  When: throttle < 0.25
        AND config corner_thresholds[corner_id].net_lift_value < 0
  Returns: recommendation "Remove lift — energy cost exceeds aero gain"
  Priority: 7

Rule: optimal_recharge_window
  When: config corner_thresholds[corner_id].net_lift_value > 0.05
        AND soc_estimated < 0.6
        AND session_flag == "green"
        AND throttle > 0.7
  Returns: recommendation "Lift here — net energy gain worth aero trade"
  Priority: 6

Rule: cusum_soc_alarm
  When: state["cusum_soc_alarm"] == True
  Returns: recommendation "Energy drain elevated — monitor closely"
  Priority: 8

Rule: safety_car_recharge
  When: session_flag in ["sc", "vsc"]
        AND soc_estimated < 0.9
  Returns: recommendation "Recharge aggressively — free recovery window"
  Priority: 10

Safe default (always last):
  When: no rule fires
  Returns: recommendation "Maintain current mode"
  Priority: 1
```

Evaluation: run all rules, collect non-None results, return highest priority.

---

#### Task B6 — Build confidence scorer (1 hour)

File: `fast_path/confidence.py`

```
Base score by priority:
  10 → 0.85,  9 → 0.80,  8 → 0.75,  7 → 0.70,  6 → 0.65,  1 → 0.50

Adjustments:
  + 0.10 if FAISS top-3 all match same outcome
  - 0.15 if data_age_ms > 500
  - 0.10 if soc_uncertainty > 0.10
  - 0.20 if data_age_ms > 2000  (force to safe default)

If final_score < 0.60: override with safe_default recommendation
```

Output alert dict:
```python
{
    "alert_id": str(uuid4()),
    "rule": str,
    "recommendation": str,
    "reason": str,
    "priority": int,
    "confidence": float,
    "soc_estimated": float,
    "corner_id": int,
    "lap": int,
    "timestamp": float,
    "fan_explanation": ""   # Empty here — Granite fills this in slow path
}
```

---

#### Task B7 — Build FAISS index (1.5 hours)

File: `offline/feature_extract.py` + `offline/build_index.py` + `fast_path/faiss_index.py`

**Step 1 — Extract historical vectors:**

```python
import fastf1
session = fastf1.get_session(2024, 'Bahrain', 'Race')
session.load(telemetry=True)
# For each lap, extract: [soc_proxy, throttle, speed_norm, corner_id_norm,
#                          lap_fraction, energy_delta, gap_ahead_norm, 0.0 (no aero)]
# Store alongside the outcome: "warning fired" or "no warning"
```

**Step 2 — Build index:**
```python
import faiss, numpy as np
index = faiss.IndexFlatL2(8)   # 8-dimensional vectors
index.add(vectors_array)       # numpy float32 array shape (N, 8)
faiss.write_index(index, "offline/bahrain_index.faiss")
```

**Step 3 — Live query wrapper:**
```python
class FAISSIndex:
    def __init__(self, index_path: str, scaler_path: str): ...
    def query(self, state_vector: dict, k=3) -> list[dict]:
        # normalise state vector → 8-dim vector → query FAISS
        # return list of top-3 matches with their outcomes
        ...
```

---

**End of Day 1 — Person B checkpoint:**

Show mock state vectors flowing through: Kalman → CUSUM → Rules → Confidence.  
Using `mock_state_vectors.py`:  
- SOC_DANGER fires `soc_danger_alert`  
- SAFETY_CAR fires `safety_car_recharge`  
- STALE_DATA triggers safe fallback  
- Print alert dict with confidence score.

---

### Person C — Slow Path & Output

**Goal:** MPC + Granite running in background. UI live. Audio works.  
Work from mock alert dicts (same schema as Person B's output). You do not need A or B running today.

---

#### Task C1 — Build Context Forge (1 hour)

File: `slow_path/context_forge.py`

Session memory manager. In-memory dict. Persisted every 5 laps.

```python
class ContextForge:
    def __init__(self):
        self.memory = {
            "circuit": "", "session_type": "", "driver": "",
            "laps": [], "alerts_fired": [], "granite_outputs": [],
            "threshold_updates": []
        }
    
    def add_lap_summary(self, lap_data: dict): ...
    def add_alert(self, alert: dict): ...
    def add_granite_output(self, output: dict): ...
    def get_last_n_laps(self, n: int) -> list: ...
    def save_to_disk(self, filepath: str): ...   # json.dump
    def load_from_disk(self, filepath: str): ... # json.load
```

Lap summary added at end of each lap:
```python
{"lap": int, "avg_soc": float, "alerts_this_lap": int, "key_decision": str}
```

---

#### Task C2 — Build the event queue (30 min)

File: `slow_path/event_queue.py`

Thin wrapper around `asyncio.Queue`:
```python
class EventQueue:
    def __init__(self):
        self.q = asyncio.Queue()
    
    async def push(self, event: dict): await self.q.put(event)
    async def pop(self) -> dict: return await self.q.get()
    def size(self) -> int: return self.q.qsize()
```

This is swappable with Redis later. Keep the interface identical.

---

#### Task C3 — Build MPC planner (2 hours)

File: `slow_path/mpc_planner.py`

Runs every 5 seconds in background via `asyncio.sleep(5)`.

**What it does:** Given current SOC and the next 5 corners, compute the optimal lift fraction for each corner to maximise speed while keeping SOC above 0.25 at the end of the 5-corner window.

```python
from scipy.optimize import minimize

def plan_5_corners(soc_now: float, corners: list[dict]) -> dict:
    # corners: list of {corner_id, net_lift_value} for next 5 corners
    
    def objective(lift_fractions):
        # Maximise speed integral = minimise lift_fraction sum
        # Higher lift → slower speed in that corner
        return sum(lift_fractions)
    
    def soc_constraint(lift_fractions):
        # SOC at end of 5 corners must be >= 0.25
        soc = soc_now
        for i, corner in enumerate(corners):
            if lift_fractions[i] > 0:
                soc += corner["net_lift_value"] * lift_fractions[i]
        return soc - 0.25
    
    bounds = [(0.0, 1.0)] * len(corners)
    result = minimize(
        objective,
        x0=[0.3] * len(corners),
        method='SLSQP',
        bounds=bounds,
        constraints={'type': 'ineq', 'fun': soc_constraint}
    )
    
    return {corners[i]["corner_id"]: round(result.x[i], 2) for i in range(len(corners))}
```

Output: dict of `{corner_id: recommended_lift_fraction}` for next 5 corners.  
Write this output to a shared dict that the rules engine reads on its next tick.

---

#### Task C4 — Build Granite client (1.5 hours)

File: `slow_path/granite_client.py`

Async wrapper. Never in the fast path. Triggered by lap completion events.

```python
import httpx

class GraniteClient:
    def __init__(self, api_key: str, model: str = "ibm-granite/granite-3.3"):
        ...
    
    async def call(self, prompt: str, timeout: float = 10.0) -> dict:
        # POST to IBM Granite API (check hackathon portal for exact endpoint)
        # Returns parsed JSON response
        # On timeout: log error, return {"error": "timeout"}
        ...
    
    async def analyse_laps(self, lap_summaries: list[dict]) -> dict:
        prompt = self._build_prompt(lap_summaries)
        response = await self.call(prompt)
        return self._parse_response(response)
    
    def _build_prompt(self, laps: list[dict]) -> str:
        # Format: "Lap {n}: avg SOC {x:.2f}, alerts fired: {y}, key decision: {z}"
        # Ask Granite to return JSON with:
        #   threshold_updates: {key: value} — any config changes
        #   fan_explanation: str — plain English for fans
        #   strategy_note: str — engineer note
        ...
    
    def _parse_response(self, response: dict) -> dict:
        # Parse Granite's text response
        # Extract JSON block safely (strip markdown fences)
        # Validate threshold_update values before returning
        ...
```

Trigger rule: call Granite at the end of every 10th lap.  
Pass last 10 lap summaries from Context Forge.

---

#### Task C5 — Build alert builder (30 min)

File: `output/alert_builder.py`

Takes alert dict from B (or GridSense from E) and packages it for output:

```python
def build_payload(alert: dict, state: dict) -> dict:
    return {
        "alert_id": alert["alert_id"],
        "rule": alert["rule"],
        "recommendation": alert["recommendation"],
        "reason": alert["reason"],
        "confidence": alert["confidence"],
        "soc_estimated": state["soc_estimated"],
        "corner_id": state["corner_id"],
        "lap": state["lap"],
        "timestamp": state["timestamp"],
        "fan_explanation": alert.get("fan_explanation", ""),
        "data_source": state["data_source"],  # "openf1" or "torcs"
        "source_module": alert.get("source_module", "voltedge")  # or "gridsense"
    }
```

---

#### Task C6 — Build WebSocket server + TTS (2 hours)

File: `output/websocket_server.py`

FastAPI WebSocket server. Clients connect and receive alert payloads pushed as JSON.

```python
from fastapi import FastAPI, WebSocket

app = FastAPI()
connected_clients = []

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    connected_clients.append(ws)
    # keep connection alive

async def broadcast(payload: dict):
    for client in connected_clients:
        await client.send_json(payload)
```

File: `output/tts.py`

```python
import pyttsx3, asyncio

engine = pyttsx3.init()

async def speak(text: str, state: dict):
    # Do NOT speak during braking zones
    if state.get("brake") == True:
        return
    # Queue audio — do not interrupt current speech
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _speak_sync, text)

def _speak_sync(text: str):
    engine.say(text)
    engine.runAndWait()

def pre_generate(text: str, filename: str):
    # Use gTTS for higher quality pre-generated audio (demo use)
    from gtts import gTTS
    tts = gTTS(text)
    tts.save(filename)
```

---

#### Task C7 — Build UI (1.5 hours)

File: `ui/index.html`

Two-panel layout. Pure HTML + CSS + vanilla JS. No framework.

**Left panel — Engineer view:**
- Live alert recommendation (large text)
- Confidence bar (CSS width from confidence value)
- SOC display (colour-coded: green > 0.7, yellow 0.4–0.7, red < 0.4)
- Last 5 alerts log (scrollable)
- Data source badge ("TORCS LIVE" or "OpenF1" or "Mock")

**Right panel — Fan view:**
- Plain English energy explanation:
  - soc > 0.7: "Battery healthy — full boost available"
  - soc 0.5–0.7: "Battery at 60% — use boost selectively"
  - soc 0.3–0.5: "Battery low — limit boost usage"
  - soc < 0.3: "Battery critical — no boost available"
- Granite fan explanation (updates after each Granite call)
- Corner map placeholder (show which corner car is at)

**WebSocket connection:**
```javascript
const ws = new WebSocket("ws://localhost:8001/ws");
ws.onmessage = (event) => {
    const payload = JSON.parse(event.data);
    updateEngineerPanel(payload);
    updateFanPanel(payload);
};
```

Alert deduplication: track last `alert_id`. Skip if same ID arrives twice.

---

**End of Day 1 — Person C checkpoint:**

Feed 10 mock alert dicts into alert_builder → broadcast via WebSocket → UI updates.  
Show TTS playing one recommendation (not during braking zone).  
Show Granite client skeleton calling the API with a fake prompt (log response).

---

## Day 1 — GridSense Track (D, E work in parallel)

GridSense detects what the driver is saying on radio and translates complaints into engineering actions.

---

### Person D — Radio Ingestion & Complaint Detection

**Goal:** Take driver radio audio → transcribe with Docling → detect complaint type → structured output.

---

#### Task D1 — Understand the data (1 hour)

GridSense works on driver radio audio clips.

**Sources:**
- OpenF1 radio endpoint: `https://api.openf1.org/v1/team_radio?session_key=9158&driver_number=1`
  Returns a list of audio clip URLs. Download 10 clips and save as `tests/fixtures/radio/`.
- For demo: pre-transcribe 5 clips manually as text fallback if Docling audio parsing is slow.

Save transcripts as:
```json
[
  {"clip_id": "r001", "text": "The car feels very loose on the rear on the fast lefts"},
  {"clip_id": "r002", "text": "I'm losing a lot of time braking into turn one"},
  ...
]
```

---

#### Task D2 — Docling transcription layer (1.5 hours)

File: `gridsense/radio_ingestion.py`

```python
class RadioIngestion:
    def __init__(self, docling_client):
        ...
    
    async def fetch_latest_clips(self, session_key: int, driver_number: int) -> list[dict]:
        # Poll OpenF1 /v1/team_radio every 30 seconds
        # Return new clips since last poll
        ...
    
    async def transcribe(self, audio_url: str) -> str:
        # Pass audio file to Docling for transcription
        # Return transcript text
        # Fallback: if Docling fails, return pre-saved transcript from fixtures
        ...
```

---

#### Task D3 — Complaint detector (2.5 hours)

File: `gridsense/complaint_detector.py`

This is the core GridSense intelligence. It reads a transcript and returns a structured complaint.

**Complaint taxonomy (these are the categories the detector must identify):**

```python
COMPLAINT_TYPES = {
    "understeer": {
        "keywords": ["pushing", "front grip", "loose on entry", "washing out", "won't turn"],
        "engineering_action": "Increase front wing angle or adjust brake bias forward",
        "priority": 8
    },
    "oversteer": {
        "keywords": ["rear loose", "snapping", "spinning", "rear end", "sliding"],
        "engineering_action": "Reduce rear wing load or adjust differential",
        "priority": 8
    },
    "brake_vibration": {
        "keywords": ["vibrating", "shaking under braking", "vibration", "judder"],
        "engineering_action": "Check brake disc temperature — may need balance adjustment",
        "priority": 7
    },
    "tyre_overheating": {
        "keywords": ["tyres going off", "no grip", "sliding everywhere", "degrading fast"],
        "engineering_action": "Consider early pit window — tyre delta likely increasing",
        "priority": 9
    },
    "energy_complaint": {
        "keywords": ["no power", "losing power", "battery", "deployment", "harvesting"],
        "engineering_action": "Cross-check with VoltEdge SOC — may need deployment mode change",
        "priority": 9
    },
    "visibility": {
        "keywords": ["can't see", "visor", "sun", "dirty visor"],
        "engineering_action": "Note for next pit stop — tear-off or visor strip",
        "priority": 4
    }
}
```

**Detector logic:**

```python
class ComplaintDetector:
    def detect(self, transcript: str) -> dict or None:
        transcript_lower = transcript.lower()
        for complaint_type, info in COMPLAINT_TYPES.items():
            for keyword in info["keywords"]:
                if keyword in transcript_lower:
                    return {
                        "complaint_type": complaint_type,
                        "transcript": transcript,
                        "engineering_action": info["engineering_action"],
                        "priority": info["priority"],
                        "confidence": 0.75  # keyword match is medium confidence
                    }
        return None   # No complaint detected
```

Later upgrade: replace keyword matching with a Granite classification call for higher accuracy.

**Test your detector with all 10 saved transcripts. Confirm correct classifications.**

---

**End of Day 1 — Person D checkpoint:**

Show: 5 transcripts going through detector. Print structured complaint dict for each.  
Show at least 3 different complaint types correctly identified.

---

### Person E — GridSense Integration & Rules

**Goal:** Wire complaint detections into the state vector and rules engine. Build GridSense UI panel.  
Person E works from Person D's mock complaint dicts. No need for D's code to run.

---

#### Task E1 — Mock complaint outputs for solo testing (30 min)

Create `tests/mock_complaints.py`:

```python
UNDERSTEER_COMPLAINT = {
    "complaint_type": "understeer",
    "transcript": "The car is pushing a lot on entry, losing time",
    "engineering_action": "Increase front wing angle or adjust brake bias forward",
    "priority": 8,
    "confidence": 0.75
}

TYRE_OVERHEATING = {
    "complaint_type": "tyre_overheating",
    "transcript": "Tyres are gone, sliding everywhere",
    "engineering_action": "Consider early pit window — tyre delta likely increasing",
    "priority": 9,
    "confidence": 0.75
}

ENERGY_COMPLAINT = {
    "complaint_type": "energy_complaint",
    "transcript": "No power coming out of the hairpin, something wrong with deployment",
    "engineering_action": "Cross-check with VoltEdge SOC — may need deployment mode change",
    "priority": 9,
    "confidence": 0.75
}

NO_COMPLAINT = None
```

---

#### Task E2 — State vector extension (30 min)

When a complaint is detected, write it to the state vector:
```python
state["radio_transcript"] = complaint["transcript"]
state["complaint_detected"] = complaint["complaint_type"]
```

Build a function that merges a complaint into a state vector:
```python
def enrich_state_with_complaint(state: dict, complaint: dict or None) -> dict:
    if complaint is None:
        state["radio_transcript"] = None
        state["complaint_detected"] = None
    else:
        state["radio_transcript"] = complaint["transcript"]
        state["complaint_detected"] = complaint["complaint_type"]
    return state
```

---

#### Task E3 — GridSense rules (2 hours)

File: `gridsense/gridsense_rules.py`

These rules produce the **same alert dict format** as VoltEdge rules. This is critical — Person C's alert builder handles both without knowing the difference.

```python
def evaluate_gridsense(state: dict, complaint: dict or None) -> dict or None:
    if complaint is None:
        return None
    
    return {
        "alert_id": str(uuid4()),
        "rule": f"gridsense_{complaint['complaint_type']}",
        "recommendation": complaint["engineering_action"],
        "reason": f"Driver radio: \"{complaint['transcript'][:60]}...\"",
        "priority": complaint["priority"],
        "confidence": complaint["confidence"],
        "soc_estimated": state["soc_estimated"],
        "corner_id": state["corner_id"],
        "lap": state["lap"],
        "timestamp": state["timestamp"],
        "fan_explanation": "",
        "source_module": "gridsense"
    }
```

**Special rule — energy complaint cross-check:**
```
If complaint_type == "energy_complaint":
  Cross-check with state["soc_estimated"]
  If soc_estimated < 0.4: elevate priority to 10, update reason to include SOC value
  If soc_estimated > 0.6: note possible sensor anomaly, reduce confidence to 0.5
```

---

#### Task E4 — GridSense UI panel (1.5 hours)

Add a third panel to `ui/index.html` — or extend the fan panel on the right.

GridSense panel shows:
- Latest driver complaint (large text): "Driver reported: TYRE OVERHEATING"
- Engineering recommendation: smaller text below
- Confidence bar
- Last 3 radio transcripts (scrollable, newest at top)
- Complaint type badge (colour-coded: red for high priority, amber for medium)

WebSocket handler update (tell Person C to include this):
```javascript
function updateGridSensePanel(payload) {
    if (payload.source_module === "gridsense") {
        document.getElementById("complaint-type").textContent = payload.rule;
        document.getElementById("grid-recommendation").textContent = payload.recommendation;
        // update confidence bar
        // update transcript log
    }
}
```

---

**End of Day 1 — Person E checkpoint:**

Feed all mock complaints through gridsense_rules. Print alert dicts.  
Confirm `source_module: "gridsense"` is present.  
Confirm `energy_complaint` cross-check logic working.  
Show GridSense panel HTML rendering correctly with mock data (open in browser with static JSON).

---

## Day 1 End — Integration Merge (30 min, all 5 together)

### VoltEdge merge (A + B + C):

1. Person A starts mock server: `uvicorn ingestion.mock_server:app --port 8000`
2. Person B connects polling client to A's queue, runs fast path pipeline
3. Person C connects WebSocket server, opens UI in browser
4. Run for 5 minutes. Watch for alerts in UI.

**Merge checklist:**
- [ ] State vectors flowing from A → B (soc_estimated being filled, not 0.0)
- [ ] At least one non-safe-default alert firing in 5 minutes
- [ ] Alert visible in UI within 250ms of firing
- [ ] Confidence score visible in UI
- [ ] Audio plays for one alert (not during braking)

### GridSense merge (D + E):

1. Person D runs complaint detector on 5 saved transcripts
2. Person E's gridsense_rules takes D's output → produces alert dicts
3. Person E shows alert dicts in GridSense UI panel (static for now)

**GridSense merge checklist:**
- [ ] 5 transcripts → 5 complaint dicts (at least 3 different types)
- [ ] All complaint dicts have correct schema (same as VoltEdge alert format)
- [ ] GridSense panel renders in browser

**Cross-team check:**
Person C shows Person E the WebSocket payload format.  
Person E confirms GridSense alert dict matches — no schema differences.

---

## Day 2 — Full Pipeline + Live TORCS Testing

### Morning — Connect everything (3 hours, all tracks)

#### VoltEdge (A, B, C):

1. Person B connects FAISS lookup into the rules pipeline (was mocked on Day 1)
2. Person C connects Granite client — trigger first real Granite call at lap 10 end
3. Person A brings TORCS online:
   - Follow IBM lab README to start TORCS
   - Run `ingestion/torcs_adapter.py` — confirm TORCS state vectors flowing
   - Switch pipeline source to TORCS, test for 10 minutes

**TORCS test: what to watch for:**
- Data source badge in UI shows "TORCS LIVE"
- Rules engine firing on TORCS data (CUSUM, SOC rules)
- Speed and throttle values look like a real car (not zero, not constant)
- TTS speaking alerts while TORCS car is running

4. Person B: run both sources simultaneously (OpenF1 mock + TORCS) — test source switching

#### GridSense (D, E):

1. Person D connects Docling to real audio clips (not fallback transcripts)
2. Person E wires GridSense alerts into Person C's WebSocket broadcast
3. Run: trigger a radio clip → complaint detected → alert in UI

**GridSense → VoltEdge connection (Person C + E, 1 hour):**

Person C's `fast_path/pipeline.py` needs to accept alerts from two sources:
- VoltEdge alert queue (Person B's output)
- GridSense alert queue (Person E's output)

Both go through the same alert_builder → WebSocket → UI.

Update pipeline to merge both queues:
```python
async def main_loop():
    async for alert in merge_queues(voltedge_queue, gridsense_queue):
        payload = build_payload(alert, current_state)
        await broadcast(payload)
        await tts.speak(alert["recommendation"], current_state)
```

---

### Afternoon — Full Replay Test (2 hours, all 5)

Run the Bahrain 2024 Race, Laps 1–30, through the system.

**Setup:**
- Person A: start mock server with full session data at 4x speed
- Person C: start WebSocket server + UI
- Person B: run `fast_path/pipeline.py`

**Watch for:**
- Alerts firing — log to terminal and file
- No unhandled exceptions (any crash is a P0 fix right now)
- Lap 10 end: Granite called, fan explanation updates in UI
- FAISS retrievals happening (log the top-3 retrieved scenarios)
- Context Forge: lap summaries accumulating correctly

**If TORCS is running:** have TORCS driving simultaneously alongside the mock replay. Compare alerts — both should be producing meaningful outputs.

---

### Day 2 End — Integration Checklist

All 5 sign off before Day 3:

- [ ] VoltEdge fast path: state vectors → alert → UI in under 100ms (measured)
- [ ] GridSense: radio → complaint → alert → UI in same pipeline
- [ ] TORCS: live data flowing into same pipeline as mock data
- [ ] Granite: called at lap 10 without blocking fast path
- [ ] Context Forge: 30 lap summaries saved
- [ ] Fan panel: Granite explanation visible after lap 10 call
- [ ] GridSense panel: complaint type and recommendation visible
- [ ] FAISS: query returning top-3 matches (not empty)
- [ ] TTS: audio plays, skips braking zones

**If anything is blocked:** defer to Day 3 morning. Core fast path + UI must be green.

---

## Day 3 — Edge Cases, Demo Prep, Polish

No new features. Fix, test, rehearse.

### Morning — Edge Cases (2 hours)

Each person runs and observes their component's failure modes:

**Edge Case 1 — SOC near zero (Person B)**  
Set `soc_estimated = 0.04`. Inject into pipeline.  
Expected: soc_danger_alert fires, priority 9, audio queues.

**Edge Case 2 — Data gap (Person A)**  
Stop mock server for 3 seconds. Restart.  
Expected: `data_age_ms` rises, safe_default fires, system recovers in 2 ticks.

**Edge Case 3 — Safety car (Person B)**  
Set `session_flag = "sc"` for 5 vectors.  
Expected: `safety_car_recharge` fires (priority 10), all other rules suppressed.

**Edge Case 4 — Granite timeout (Person C)**  
Set Granite timeout to 0.1s (force timeout).  
Expected: fast path continues unaffected. Error logged. Thresholds unchanged.

**Edge Case 5 — TORCS disconnect (Person A)**  
Kill TORCS while running.  
Expected: `data_age_ms` rises, safe_default fires, system logs "TORCS disconnected".

**Edge Case 6 — Ambiguous radio (Person D)**  
Feed a radio clip that could be two complaint types.  
Expected: detector picks highest confidence match, logs uncertainty.

**Edge Case 7 — GridSense + VoltEdge same priority (Person E)**  
Inject a GridSense alert (priority 9) while VoltEdge also has a priority 9 alert.  
Expected: both fire in sequence, UI shows both, TTS queues (not simultaneous).

---

### Afternoon — Demo Prep (2 hours)

**Demo window: Bahrain 2024 Race, Laps 28–38**

This window has high energy pressure. Run through the system.  
Identify your 3 demo moments and note exact lap + corner + recommendation text + confidence.

**Pre-generate demo audio (Person C):**  
Run `output/tts.py generate_demo_audio()` for each alert in your 3 demo moments.  
Save as `demo_audio/moment1.mp3`, `moment2.mp3`, `moment3.mp3`.  
During demo: play cached files — zero latency, best quality.

**Demo script (practise twice minimum):**

```
1. "WingMan is a real-time intelligence layer for the 2026 season's hardest problem —
    coupling energy and aerodynamics in under 100 milliseconds."

2. Start mock replay at Lap 28. Show live data flowing into the engineer panel.
   "Every 250 milliseconds, we receive car telemetry."

3. Lap 31 — alert fires. 
   "73 milliseconds from telemetry to recommendation."
   Point to confidence score. Read the recommendation.

4. Play audio: "Recharge now — Turn eleven."
   "The driver hears this. No headset lag."

5. Switch to GridSense panel.
   "The driver reports a handling problem on the radio."
   Trigger saved radio clip → complaint detected → engineering action in UI.
   "No engineer needed to transcribe. GridSense converts speech to action."

6. Switch to Langflow. Show slow-path pipeline.
   "Meanwhile, IBM Granite is reasoning over 30 laps in the background."

7. Fan panel updates with Granite explanation.
   "Same insight — explained for the fan watching at home."

8. Show TORCS tab (if TORCS is running).
   "And this works on live car data too — not just historical replay."

9. "IBM Granite, Context Forge, Langflow, Docling — one stack. The whole race weekend."
```

---

## Day 4 (Buffer) — Use Only If Needed

Use only for:
- Fixing bugs found in Day 3 edge case tests
- Polishing UI (colours, layout, mobile responsiveness)
- Rehearsing the demo 3 more times
- Adding TORCS as a toggle in the UI (switch live source)

**Do not add new features on Day 4.**

---

## Definition of Done — All 5 Sign Off

**VoltEdge:**
- [ ] Bahrain 2024 Race, 30-lap replay runs without exception
- [ ] P95 fast path latency < 100ms (measured with `time.perf_counter`)
- [ ] At least 3 distinct rules fire during replay (not just safe_default)
- [ ] Safe fallback fires when data goes stale
- [ ] Granite called at lap 10 without blocking fast path
- [ ] FAISS returning top-3 matches (not empty)
- [ ] Audio fires correctly, skips braking zones
- [ ] Context Forge has all 30+ lap summaries
- [ ] TORCS adapter runs and produces valid state vectors

**GridSense:**
- [ ] At least 5 complaint types correctly identified in test transcripts
- [ ] GridSense alerts flow through same WebSocket as VoltEdge
- [ ] GridSense UI panel visible and updating
- [ ] energy_complaint cross-check with SOC working

**Demo:**
- [ ] UI updates in real time, all panels active (Engineer + Fan + GridSense)
- [ ] Langflow pipeline visible and running during demo
- [ ] Demo rehearsed at least twice end-to-end (all 5 present)
- [ ] All 5 members can explain their component in 2 sentences
- [ ] Demo audio pre-generated and cached

---

## Quick Reference

| If stuck on... | Ask |
|---|---|
| OpenF1 data not returning | Person A |
| TORCS adapter not connecting | Person A |
| SOC estimate looks wrong | Person B |
| Wrong rule firing | Person B |
| FAISS query crashing | Person B |
| Granite API auth error | Person C |
| UI not updating | Person C |
| TTS not playing | Person C |
| Radio clip not downloading | Person D |
| Docling transcription failing | Person D |
| GridSense rule firing wrong | Person E |
| WebSocket schema mismatch | Person C + E together |
| asyncio event loop error | Any — almost always a missing `await` |

---

## Resources

| Resource | URL | Used by |
|---|---|---|
| IBM TORCS Lab | https://github.com/IBM-SkillsBuild-AI-Builders-Challenge/hands-on-labs/tree/main/01_torcs_lab | Person A |
| OpenF1 API docs | https://openf1.org | Persons A, D |
| FastF1 docs | https://docs.fastf1.dev | Persons A, B |
| filterpy Kalman guide | https://filterpy.readthedocs.io | Person B |
| FAISS getting started | https://github.com/facebookresearch/faiss/wiki/Getting-started | Person B |
| SciPy SLSQP | https://docs.scipy.org/doc/scipy/reference/optimize.minimize-slsqp.html | Person C |
| FastAPI WebSocket | https://fastapi.tiangolo.com/advanced/websockets | Person C |
| gTTS docs | https://gtts.readthedocs.io | Person C |
| IBM Docling | IBM hackathon portal | Person D |
| IBM Granite API | IBM hackathon portal | Person C |
| IBM Langflow | IBM hackathon portal | Person C |
| IBM Context Forge | IBM hackathon portal | Person C |
