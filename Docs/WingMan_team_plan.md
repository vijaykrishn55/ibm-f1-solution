# WingMan — Team Build Plan
### 5 Members · 3–4 Days · Day-by-Day with Integration Checkpoints

---

## Read This First — Everyone

Before anyone writes a line, everyone reads this section together. 30 minutes. Non-negotiable.

**What WingMan is:**
A real-time AI system that monitors F1 car energy and aerodynamics and tells engineers and drivers what to do — and why — faster than any human can react.

**How it works in one sentence:**
Live car data comes in every 250ms → fast path processes it and fires an alert in under 100ms → background AI reasons over it and updates strategy → both text and audio output go to engineer and driver.

**The two paths:**
- **Fast path** — deterministic, runs every 250ms, no AI in this loop, must respond in under 100ms
- **Slow path** — AI reasoning (IBM Granite), runs in background every few laps, updates the fast path's parameters

**The golden rule:**
The fast path never waits for AI. AI runs separately and updates parameters. Fast path uses those parameters but never calls AI directly.

---

## The State Vector — Agree on This Before Day 1 Build Starts

This is the single most important thing the whole team agrees on. The state vector is the data structure that flows between every component. If one person changes it without telling others, everything breaks.

**Print this out. Stick it on the wall.**

```
State Vector — WingMan v1

{
  "timestamp":          float   # Unix time of this reading
  "driver":             string  # e.g. "VER", "HAM"
  "lap":                int     # Current lap number
  "corner_id":          int     # Which corner (1 to N for this circuit)
  "lap_fraction":       float   # 0.0 (lap start) to 1.0 (lap end)
  "speed":              float   # km/h — Kalman filtered
  "throttle":           float   # 0.0 to 1.0
  "brake":              bool    # True or False
  "drs":                bool    # DRS open = True
  "aero_state":         string  # "straight_mode" or "corner_mode"
  "soc_raw":            float   # Raw estimated SOC before filtering
  "soc_estimated":      float   # Kalman-filtered SOC (0.0 to 1.0)
  "soc_uncertainty":    float   # Kalman confidence in SOC estimate
  "energy_delta":       float   # MJ change since last reading
  "gap_ahead":          float   # Seconds to car ahead
  "session_flag":       string  # "green", "yellow", "sc", "vsc", "red"
  "data_age_ms":        int     # How old is this reading in milliseconds
  
  # Extension fields — leave as None for WingMan, filled by later modules
  "radio_transcript":       None  # GridSense fills this
  "complaint_detected":     None  # GridSense fills this
  "corner_direction":       None  # TyreWhisperer fills this
  "delta_from_optimal":     None  # GhostDelta fills this
}
```

**Rule:** Nobody adds or removes fields from this without a group decision.

---

## Team Roles

| Member | Role | Primary files |
|---|---|---|
| **Member 1** | Data Lead | `ingestion/`, `config/` |
| **Member 2** | State & Memory Lead | `state/`, `offline/feature_extract.py`, `slow_path/context_forge.py` |
| **Member 3** | Fast Path Logic Lead | `fast_path/cusum.py`, `fast_path/rules_engine.py`, `fast_path/confidence.py` |
| **Member 4** | AI & Planning Lead | `offline/build_index.py`, `fast_path/faiss_index.py`, `slow_path/mpc_planner.py`, `slow_path/granite_client.py` |
| **Member 5** | Output & UI Lead | `output/`, `ui/index.html`, Langflow pipeline |

---

## Integration Contracts — What Each Member Produces and Consumes

Before building, each member must know exactly what format they receive data in and what format they hand off.

```
Member 1 produces:   Raw state vector dict (soc_estimated = 0, unfilled)
         consumed by: Member 2 (Kalman), Member 3 (rules), Member 5 (output)

Member 2 produces:   Enriched state vector (soc_estimated filled), session memory
         consumed by: Member 3 (rules engine reads soc_estimated)
                      Member 4 (Granite reads Context Forge)

Member 3 produces:   Alert dict {rule, recommendation, reason, confidence}
         consumed by: Member 5 (output layer builds payload from this)

Member 4 produces:   FAISS lookup result {top3_scenarios, outcomes}
                     MPC plan {corner_actions, expected_soc}
                     Granite threshold updates {threshold_key: new_value}
         consumed by: Member 3 (rules engine uses FAISS result + thresholds)
                      Member 2 (Context Forge stores Granite outputs)

Member 5 produces:   Alert payload pushed over WebSocket to UI
                     Audio file path for TTS
                     Rendered two-panel UI
         consumed by: Engineer screen + driver audio
```

---

## Day 0 — Setup (2–3 hours, all members together)

Do this the evening before or first thing on Day 1 morning.

### Everyone does (30 min)
1. Create a shared GitHub repo called `WingMan`
2. One person creates the full folder structure from the build guide
3. Everyone clones it
4. Everyone creates a `dev/<your-name>` branch — never push to main until the end-of-day merge

### Everyone installs (45 min)
Run this in your terminal:
```
pip install fastf1 httpx filterpy faiss-cpu scipy scikit-learn pandas numpy websockets fastapi uvicorn pyttsx3 gTTS redis pytest pytest-asyncio
```

If `faiss-cpu` fails: try `pip install faiss-cpu --no-cache-dir`
If `filterpy` fails: try `pip install filterpy --pre`

### Everyone verifies (15 min)
Open Python and run:
```python
import fastf1
import faiss
import filterpy
import httpx
print("All good")
```
If any import fails, fix before Day 1 starts.

### Agree on two things (30 min)
1. Read the state vector section above together. Everyone confirms they understand what they receive and produce.
2. Create `config/circuits/bahrain.json` together with this skeleton:
```json
{
  "circuit": "bahrain",
  "corners": 15,
  "boost_zone_corners": [11, 12],
  "soc_danger_threshold": 0.25,
  "corner_thresholds": {
    "1":  {"net_lift_value": -0.02, "lift_energy_gain": 0.12, "lift_aero_cost_s": 0.14},
    "4":  {"net_lift_value":  0.08, "lift_energy_gain": 0.18, "lift_aero_cost_s": 0.10},
    "10": {"net_lift_value":  0.15, "lift_energy_gain": 0.22, "lift_aero_cost_s": 0.07}
  }
}
```
This file is not final — Member 2 will calibrate it offline. But everyone needs the schema now.

---

## Day 1 — Build in Isolation

Each member builds their component solo. Goal: by end of day, your component runs with mock data and produces correct-shaped output. You are not connecting to other members today.

---

### Member 1 — Data Lead

**Your goal today:** Telemetry is flowing. A state vector is being printed to the console every 250ms.

**What you are building:**
1. `ingestion/mock_server.py` — a tiny local server that replays recorded F1 data
2. `ingestion/openf1_stream.py` — a client that polls data and builds raw state vectors
3. `config/circuits/bahrain_geometry.py` — maps X/Y position to corner ID

**Step 1 — Get real cached data (1 hour)**

Go to this URL and read what endpoints return:
`https://openf1.org/#car-data`

Download a cached response for Bahrain 2024 Race by hitting these in your browser and saving the JSON:
- `https://api.openf1.org/v1/car_data?session_key=9158&driver_number=1` (Verstappen)
- `https://api.openf1.org/v1/position?session_key=9158&driver_number=1`
- `https://api.openf1.org/v1/intervals?session_key=9158&driver_number=1`

Save these as `tests/fixtures/car_data.json`, `position.json`, `intervals.json`.

**Step 2 — Build the mock server (1.5 hours)**

The mock server is a FastAPI app with one endpoint: `/v1/car_data`.
When hit, it returns the next row from your saved JSON, then the next, then the next — simulating live data at 4Hz.

It needs:
- A list of all rows loaded from the saved JSON at startup
- A counter that increments on each request
- Returns `rows[counter]` on each GET
- Resets to 0 when it reaches the end

Start your mock server with: `uvicorn ingestion.mock_server:app --port 8000`

**Step 3 — Build the polling client (1.5 hours)**

The polling client is an async loop that:
- Every 250ms: calls `http://localhost:8000/v1/car_data`
- Parses the response
- Builds a partial state vector (fill what you can from raw data)
- Calculates `data_age_ms` as current time minus `timestamp` in response
- Puts the state vector into a Python `asyncio.Queue`

Fields you can fill from raw data:
`timestamp, driver, speed, throttle, brake, drs, lap_fraction, gap_ahead, session_flag`

Fields you CANNOT fill yet (leave as 0 or None):
`soc_estimated` (Member 2), `corner_id` (Step 4), `aero_state` (derive from DRS)

**Step 4 — Corner ID mapping (45 min)**

Load FastF1 for Bahrain FP1:
```python
import fastf1
session = fastf1.get_session(2024, 'Bahrain', 'FP1')
session.load(telemetry=True)
lap = session.laps.pick_driver('VER').iloc[5]
tel = lap.get_car_data().add_distance()
# tel has columns: X, Y, Distance, Speed etc.
```

Look at the X/Y values. Create a lookup dict: which (X, Y) range corresponds to which corner. Bahrain has 15 corners. This does not need to be perfect — approximate range buckets are fine.

Store as `config/circuits/bahrain_corners.json`:
```json
{"corner_1": {"x_min": -500, "x_max": -200, "y_min": 300, "y_max": 600}, ...}
```

**End of Day 1 checkpoint — Member 1:**
Run mock server. Run polling client. Show: state vectors printing to console every 250ms with correct speed/throttle values and a valid corner_id. Share the queue module path with the team.

---

### Member 2 — State & Memory Lead

**Your goal today:** The Kalman filter takes raw proxy inputs and produces a smooth SOC estimate. Context Forge schema is defined.

**What you are building:**
1. `state/kalman.py` — battery SOC estimation
2. `state/window.py` — sliding window per corner
3. `slow_path/context_forge.py` — session memory

**Step 1 — Understand Kalman filter without the maths (30 min)**

You do not need to understand the mathematics. Here is the mental model:

You have a value you want to know (battery SOC) but you can't measure it directly. You have noisy proxies (throttle, brake, speed). The Kalman filter combines your best guess from the last tick with the noisy new measurement to produce a better estimate than either alone.

The `filterpy` library handles all the maths. You provide:
- What you're estimating (SOC, SOC change rate)
- How much you trust your model (process noise)
- How noisy your measurements are (measurement noise)

**Step 2 — Build kalman.py (2 hours)**

Use `filterpy.kalman.KalmanFilter`.

Your filter has:
- State dimension: 2 (SOC, rate of SOC change)
- Measurement dimension: 1 (your proxy estimate of SOC from raw throttle/brake/speed)

**How to compute the proxy SOC estimate from raw data:**
- If throttle > 0.8 and brake == False: SOC is depleting fast → proxy_soc -= 0.003 per tick
- If brake == True: SOC is recovering → proxy_soc += 0.002 per tick
- If DRS == True: higher drag, slightly more depletion → proxy_soc -= 0.001 per tick
- Baseline depletion every tick: -0.0005

Start proxy_soc at 0.85 (car starts at ~85% SOC).
Clamp between 0.0 and 1.0 always.

Your Kalman filter takes proxy_soc as measurement input and returns:
- `soc_estimated` (smooth)
- `soc_uncertainty` (the P diagonal — how confident the filter is)

**Tuning (start with these, adjust based on how jittery the output looks):**
- Process noise (Q): 0.001 — how much SOC can actually change between ticks
- Measurement noise (R): 0.01 — how noisy your proxy calculation is

**Step 3 — Build window.py (1 hour)**

This is simpler. For each corner_id, maintain a circular buffer of the last 5 lap readings:
- Use `collections.deque(maxlen=5)`
- One deque per corner_id
- Push current soc_estimated and speed into it each tick
- Expose: mean, std, trend (last value minus first value in window)

The rules engine uses these trends to detect gradual degradation.

**Step 4 — Build context_forge.py (1 hour)**

Context Forge is WingMan's memory. It holds everything that happened this session.

Schema:
```python
session_memory = {
    "circuit": "bahrain",
    "session_type": "race",
    "driver": "VER",
    "laps": [],           # One summary dict per lap
    "alerts_fired": [],   # One entry per alert
    "granite_outputs": [], # One entry per Granite response
    "threshold_updates": [] # History of threshold changes
}
```

Lap summary (added at end of each lap):
```python
{
  "lap": int,
  "avg_soc_per_corner": dict,   # {corner_id: avg_soc}
  "alerts_this_lap": int,
  "key_decision": str           # Most significant alert this lap
}
```

Functions to expose:
- `add_lap_summary(lap_data)`
- `add_alert(alert_dict)`
- `add_granite_output(output_dict)`
- `get_last_n_laps(n)` — returns last N lap summaries for Granite
- `save_to_disk(filepath)` — JSON dump
- `load_from_disk(filepath)` — JSON load

**End of Day 1 checkpoint — Member 2:**
Show: feed 50 fake SOC proxy values into the Kalman filter, plot raw vs filtered (use `matplotlib`). The filtered line should be smooth. Show Context Forge accepting a fake lap summary and retrieving it correctly.

---

### Member 3 — Fast Path Logic Lead

**Your goal today:** CUSUM detector and Rules Engine work correctly on mock data.

**What you are building:**
1. `fast_path/cusum.py` — change-point detector
2. `fast_path/rules_engine.py` — decision logic
3. `fast_path/confidence.py` — scoring + safe fallback

**Step 1 — Understand CUSUM in plain English (20 min)**

CUSUM watches a value over time. It asks: "has this value shifted significantly from what I expected?"

It works by accumulating the difference between the actual value and the expected value. When the accumulated difference crosses a threshold, it declares a change-point.

Example: Expected SOC depletion is 0.003 per tick. If actual depletion is 0.007 every tick for 5 ticks, CUSUM accumulates (0.007 - 0.003) × 5 = 0.020 and fires an alarm.

**Step 2 — Build cusum.py (1 hour)**

Create a `CUSUMDetector` class with:
- `expected_value`: what the signal normally is (e.g., average SOC depletion rate)
- `threshold`: how much accumulation before alarm fires (tune this)
- `cumulative_sum`: running total (starts at 0)
- `method update(actual_value)` → returns True if alarm should fire, False otherwise

Logic inside update:
```
deviation = actual_value - expected_value
cumulative_sum += deviation
if cumulative_sum > threshold:
    alarm = True
    cumulative_sum = 0  # reset after firing
    return True
if cumulative_sum < 0:
    cumulative_sum = 0  # never go negative
return False
```

Create separate CUSUM instances for:
- SOC depletion rate (fires when battery draining faster than expected)
- Per-corner speed (fires when a corner is getting consistently slower)

**Step 3 — Build rules_engine.py (2 hours)**

The rules engine is the decision maker. It takes a fully enriched state vector + FAISS result (use a mock for today) and returns a recommendation.

Structure: a list of rule functions, each taking state vector as input and returning either a recommendation dict or None.

**Five core rules to implement:**

Rule 1 — `soc_danger_alert`
```
Triggers when: soc_estimated < config["soc_danger_threshold"]
               AND corner_id is in config["boost_zone_corners"]  
               AND session_flag == "green"
Returns: {
  "rule": "soc_danger_alert",
  "recommendation": "Recharge immediately — boost zone in {X} corners",
  "reason": "SOC at {soc}% — insufficient for overtake zone",
  "priority": 9
}
```

Rule 2 — `lift_not_worth_it`
```
Triggers when: throttle < 0.25 (driver is lifting)
               AND config["corner_thresholds"][corner_id]["net_lift_value"] < 0
Returns: {
  "rule": "lift_not_worth_it",
  "recommendation": "Remove lift — aero loss exceeds energy gain in this corner",
  "reason": "Net lift value negative for corner {corner_id}",
  "priority": 7
}
```

Rule 3 — `optimal_recharge_window`
```
Triggers when: config["corner_thresholds"][corner_id]["net_lift_value"] > 0.05
               AND soc_estimated < 0.6
               AND session_flag == "green"
               AND throttle > 0.7 (not already lifting)
Returns: {
  "rule": "optimal_recharge_window",
  "recommendation": "Lift in this corner — net energy gain worth the aero cost",
  "reason": "Net lift value +{value}s in corner {corner_id}",
  "priority": 6
}
```

Rule 4 — `cusum_soc_alarm`
```
Triggers when: state["cusum_soc_alarm"] == True
Returns: {
  "rule": "cusum_soc_alarm",
  "recommendation": "Energy drain rate elevated — monitor closely",
  "reason": "SOC depleting faster than historical average",
  "priority": 8
}
```

Rule 5 — `safety_car_recharge`
```
Triggers when: session_flag in ["sc", "vsc"]
               AND soc_estimated < 0.9
Returns: {
  "rule": "safety_car_recharge",
  "recommendation": "Recharge aggressively — free energy recovery window",
  "reason": "Safety car deployed — no lap time cost for lift-and-coast",
  "priority": 10
}
```

**Safe default (always last):**
```
If no rule fires OR all rules return None:
Returns: {
  "rule": "safe_default",
  "recommendation": "Maintain current mode",
  "reason": "No anomaly detected",
  "priority": 1
}
```

**Evaluation logic:**
- Evaluate all 5 rules
- Collect all that fire (return non-None)
- Return the highest priority one

**Step 4 — Build confidence.py (45 min)**

Takes a rule result + state vector + FAISS result (mock for now) and returns a confidence score (0.0 to 1.0).

```
Base score from rule priority:
  priority 10 → base 0.85
  priority 9  → base 0.80
  priority 7  → base 0.75
  priority 6  → base 0.70
  priority 1  → base 0.50

Adjustments:
  + 0.10 if FAISS top-3 all had same outcome (consistent historical match)
  - 0.15 if data_age_ms > 500
  - 0.10 if soc_uncertainty > 0.10
  - 0.20 if data_age_ms > 2000 (force safe fallback)

Final rule:
  if final_score < 0.60: override recommendation with safe_default
```

**End of Day 1 checkpoint — Member 3:**
Feed 20 hand-crafted mock state vectors (with known SOC values, corner IDs, flags) into the rules engine. Print which rule fires for each. Confirm the safe default fires when all inputs are normal. Confirm the SC rule fires when you set `session_flag = "sc"`.

---

### Member 4 — AI & Planning Lead

**Your goal today:** FAISS index is built from historical data. Granite client skeleton is ready.

**What you are building:**
1. `offline/feature_extract.py` — extracts state vectors from FastF1 historical data
2. `offline/build_index.py` — builds the FAISS index from those vectors
3. `fast_path/faiss_index.py` — live query wrapper
4. `slow_path/granite_client.py` — async Granite API wrapper

**Step 1 — Load real FastF1 data (1 hour)**

```python
import fastf1
import pandas as pd

# This downloads ~50MB of data first time. Cache enabled by default.
session = fastf1.get_session(2024, 'Bahrain', 'R')
session.load(telemetry=True, laps=True, weather=True)

# Get all laps for Verstappen
ver_laps = session.laps.pick_driver('VER')

# Get telemetry for one lap
lap = ver_laps.iloc[10]
tel = lap.get_car_data().add_distance()
print(tel.columns)  # See what's available
print(tel.head())
```

Explore the data. Key columns: `Speed`, `Throttle`, `Brake`, `DRS`, `RPM`, `nGear`, `Distance`, `X`, `Y`, `Z`.

**Step 2 — Build feature_extract.py (1.5 hours)**

For each lap in a session, for each row of telemetry, extract a feature vector:

```
feature_vector = [
  soc_proxy,          # compute same way as Member 2's proxy (throttle/brake rule)
  throttle_norm,      # throttle / 100.0
  speed_norm,         # speed / 350.0  (normalise to 0-1)
  corner_id_norm,     # corner_id / 15.0 (for Bahrain)
  lap_fraction,       # distance / total_lap_distance
  aero_state_encoded, # 1.0 if DRS open, 0.0 if closed
  energy_delta,       # difference in soc_proxy from last row
  0.5                 # gap_ahead placeholder (not in single-car telemetry)
]
```

Also store alongside each vector:
- What throttle was on the NEXT 5 rows (outcome: did driver lift or push?)
- The sector time of this lap
- Lap number

Save all feature vectors + metadata to disk as a CSV: `offline/bahrain_features.csv`

**Important:** Also save the min/max values used for normalisation to `offline/bahrain_scaler.json`. You will apply the same normalisation to live data. Without this, your live queries will not match the index.

**Step 3 — Build build_index.py (1 hour)**

```python
import faiss
import numpy as np
import pandas as pd

# Load your feature CSV
df = pd.read_csv('offline/bahrain_features.csv')
vectors = df[['soc_proxy','throttle_norm','speed_norm','corner_id_norm',
              'lap_fraction','aero_state_encoded','energy_delta','gap_ahead']].values.astype('float32')

# Build index
dimension = 8  # number of features
index = faiss.IndexFlatL2(dimension)
index.add(vectors)

# Save to disk
faiss.write_index(index, 'offline/bahrain.index')
print(f"Index built with {index.ntotal} vectors")
```

If ntotal < 1000: you haven't loaded enough laps. Load at least 3 sessions (FP1, FP2, Race).

**Step 4 — Build faiss_index.py (45 min)**

This is the live query wrapper. It:
- Loads `bahrain.index` and `bahrain_scaler.json` at startup
- Exposes a single function `query(state_vector_dict)` that:
  - Extracts the 8 features from the state vector
  - Normalises them using the saved scaler
  - Queries the index for top-3 nearest neighbours
  - Returns their metadata (what actions were taken, what happened)

**Step 5 — Build granite_client.py skeleton (45 min)**

The Granite client is an async wrapper around the IBM Granite API. Today: just the skeleton and a test call.

```python
import httpx
import asyncio

async def call_granite(prompt: str, system_prompt: str) -> dict:
    """
    Calls IBM Granite 3.3 asynchronously.
    Returns parsed JSON response.
    Never called from the fast path.
    """
    # IBM Granite API endpoint — fill in from IBM docs
    url = "https://..."
    
    payload = {
        "model": "granite-3-3",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 500
    }
    
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(url, json=payload, headers={"Authorization": f"Bearer {API_KEY}"})
        return response.json()
```

Write the system prompt template for WingMan's strategy call:
```
You are a Formula 1 race strategist AI. 
You receive lap summaries and current telemetry context.
You identify energy management patterns and suggest threshold adjustments.
Always respond in valid JSON with keys: 
  "threshold_updates" (dict), "strategy_notes" (string), "fan_explanation" (string).
Never suggest threshold values outside safe bounds.
soc_danger_threshold must stay between 0.15 and 0.45.
```

**End of Day 1 checkpoint — Member 4:**
Show: FAISS index built with at least 5,000 vectors. Query it with a random state vector and print the top-3 results. Show Granite client returning a valid response to a test prompt (even if the content isn't perfect yet).

---

### Member 5 — Output & UI Lead

**Your goal today:** The UI is running locally. WebSocket pushes test alerts to it. Audio plays.

**What you are building:**
1. `output/alert_builder.py` — structures the alert payload
2. `output/websocket_server.py` — pushes alerts to UI
3. `output/tts.py` — text-to-audio
4. `ui/index.html` — two-panel interface

**Step 1 — Build alert_builder.py (45 min)**

Takes a rule result + state vector and produces the final alert payload dict:

```python
def build_alert(rule_result: dict, state: dict, fan_explanation: str = "") -> dict:
    return {
        "alert_id": generate_uuid(),     # Use uuid.uuid4()
        "timestamp": state["timestamp"],
        "driver": state["driver"],
        "type": map_rule_to_type(rule_result["rule"]),  # see below
        "recommendation": rule_result["recommendation"],
        "reason": rule_result["reason"],
        "confidence": rule_result["confidence"],
        "corner": state["corner_id"],
        "corners_ahead": compute_corners_ahead(state),  # distance to next boost zone
        "fan_explanation": fan_explanation,   # empty until Granite provides it
        "audio_text": shorten_for_audio(rule_result["recommendation"]),  # max 8 words
        "module": "wingman"
    }
```

Rule-to-type mapping:
```
"soc_danger_alert"     → "energy_warning"
"lift_not_worth_it"    → "lift_override"
"optimal_recharge"     → "recharge_opportunity"
"cusum_soc_alarm"      → "drain_alert"
"safety_car_recharge"  → "sc_opportunity"
"safe_default"         → "status_ok"
```

**`shorten_for_audio` rules:**
- Max 8 words
- No conditionals ("if", "when", "consider")
- No numbers > 2 digits
- Format: `"{Action} — {location}"` e.g. `"Recharge now — Turn 11"`

**Step 2 — Build websocket_server.py (1 hour)**

Use FastAPI's WebSocket support:

```python
from fastapi import FastAPI, WebSocket
import asyncio
import json

app = FastAPI()
connected_clients = []

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.append(websocket)
    try:
        while True:
            await websocket.receive_text()  # keep connection alive
    except:
        connected_clients.remove(websocket)

async def broadcast_alert(alert: dict):
    message = json.dumps(alert)
    for client in connected_clients:
        try:
            await client.send_text(message)
        except:
            connected_clients.remove(client)
```

The fast path calls `broadcast_alert(alert_payload)` every time a new alert fires.

**Step 3 — Build tts.py (45 min)**

Two modes:

Mode 1 — Live (uses pyttsx3, offline, instant):
```python
import pyttsx3
engine = pyttsx3.init()
engine.setProperty('rate', 160)  # Speaking speed
engine.setProperty('volume', 0.9)

def speak(text: str):
    # Only fires if not in braking zone
    engine.say(text)
    engine.runAndWait()
```

Mode 2 — Demo (pre-generated high quality, uses gTTS):
```python
from gtts import gTTS
import os

def generate_audio(text: str, filename: str):
    tts = gTTS(text=text, lang='en', slow=False)
    tts.save(f"output/audio_cache/{filename}.mp3")
```

Pre-generate audio for these 6 common alert phrases before the demo:
1. "Recharge now — Turn eleven"
2. "Remove lift — aero loss too high"
3. "Boost available — use now"
4. "Battery critical — lift and coast"
5. "Safety car — recharge aggressively"
6. "Energy nominal — maintain mode"

**Braking zone check** (attach to speak function):
Before playing audio, check if `state["brake"] == True`. If yes, queue the audio with `asyncio.sleep(1.5)` and try again. Never interrupt a braking event.

**Step 4 — Build ui/index.html (2 hours)**

Two-panel layout. Left: Engineer view. Right: Fan view.

Engineer panel shows:
- Large recommendation text (most recent alert)
- Confidence bar (green > 0.8, amber 0.6–0.8, red < 0.6)
- Alert type badge
- Corner number where it applies
- Last 3 alerts list (timestamp + short recommendation)
- SOC indicator (a simple number that updates)

Fan panel shows:
- Fan explanation text (plain language from Granite)
- Simple "What's happening" sentence
- Energy state in plain words: "Battery at 60% — one boost remaining"
- "Watch for" tip: next expected decision point

WebSocket connection in the UI:
```javascript
const ws = new WebSocket("ws://localhost:8001/ws");
ws.onmessage = function(event) {
    const alert = JSON.parse(event.data);
    updateEngineerPanel(alert);
    updateFanPanel(alert);
};
```

**Styling guidance:**
- Dark background (#0a0a0a) — F1 command center feel
- Teal accent (#00d4aa) for fast path alerts
- Amber (#f59e0b) for energy warnings
- Red (#ef4444) for critical
- Font: monospace for data values, sans-serif for explanations

**End of Day 1 checkpoint — Member 5:**
Show: WebSocket server running. Open UI in browser. Send a hardcoded test alert dict from the terminal. Confirm the UI updates in real time. Play one TTS audio file.

---

## Day 1 End — Integration Merge

At end of Day 1 (target: 6pm), all members push to their branch. Team lead merges all into a `day1-integration` branch.

**Day 1 integration test (30 min, everyone):**
1. Member 1 starts mock server
2. Member 5 starts WebSocket server and opens UI
3. Member 1 manually puts a test state vector into an `asyncio.Queue`
4. Member 3 reads it and runs the rules engine manually
5. Member 5's alert_builder wraps the result
6. Member 5 broadcasts it to the UI

If the UI shows the alert and audio plays — Day 1 is a success.

**What is NOT expected on Day 1:**
- Kalman filter feeding into the rules engine (Day 2)
- FAISS results influencing rules (Day 2)
- Granite calls (Day 2)
- Continuous loop running (Day 2)

---

## Day 2 — Connect the Pipeline

Each member connects their component to the others. Goal: by end of day, the full pipeline runs continuously on mock data — telemetry in, alert out, audio plays, UI updates.

---

### Member 1 — Data Lead

**Your goal today:** Ingestion feeds a live queue that the whole team reads from.

Tasks:
1. Modify `openf1_stream.py` to put enriched state vectors into a shared `asyncio.Queue` that other modules import from `state/session_state.py`
2. Add the Kalman call: after building the raw state vector, call Member 2's `kalman.update(proxy_soc)` and populate `soc_estimated` and `soc_uncertainty` before putting it in the queue
3. Add corner ID mapping using your Bahrain geometry config
4. Add `aero_state` derivation: if DRS is open → "straight_mode", else → "corner_mode"
5. Test: run mock server + ingestion client together, confirm queue fills with fully enriched state vectors every 250ms

---

### Member 2 — State & Memory Lead

**Your goal today:** Kalman is live in the pipeline. Context Forge saves end-of-lap summaries.

Tasks:
1. Expose `KalmanSOC.update(proxy_soc)` as importable function from `state/kalman.py`
2. Verify Member 1 is calling it correctly — check that soc_estimated is smooth in the queue output
3. Connect `window.py`: after Kalman update, push soc_estimated and speed to the per-corner sliding window
4. Add lap-end detection to `session_state.py`: when `lap_fraction` crosses 0.98 → trigger lap summary save to Context Forge
5. Build `offline/calibrate.py`: loads FastF1 Bahrain FP1 + FP2 data, computes actual net_lift_value per corner using lap time deltas, saves updated `config/circuits/bahrain.json`

Calibration approach:
- For each lap, for each corner: did the driver lift (throttle < 0.25)?
- If yes: what was the lap time for that corner vs laps where they didn't lift?
- net_lift_value = (avg time when NOT lifting) - (avg time when lifting) in seconds
- Negative = lifting costs more than it saves in this corner

---

### Member 3 — Fast Path Logic Lead

**Your goal today:** Rules engine reads from the live queue and fires alerts through confidence scorer.

Tasks:
1. Create `fast_path/pipeline.py` — the main async loop that:
   - Reads from the shared queue
   - Calls Member 2's CUSUM detectors and adds `cusum_soc_alarm` to state vector
   - Calls Member 4's `faiss_index.query(state)` (pass mock result today if FAISS not ready)
   - Runs all rules
   - Calls confidence scorer
   - Passes result to Member 5's alert builder
2. Add CUSUM call: import Member 2's CUSUM detectors, call `update(soc_estimated)` each tick
3. Wire FAISS: import `fast_path/faiss_index.query()`. If it throws an error, fall back to empty result and note in confidence scorer
4. Wire to output: call `alert_builder.build_alert()` with the rule result, push to WebSocket

---

### Member 4 — AI & Planning Lead

**Your goal today:** FAISS is live in the fast path. MPC planner runs async. Granite makes its first real call.

Tasks:
1. Connect `faiss_index.py` to the fast path: expose `query(state_dict)` that returns top-3 results. Verify it returns in under 25ms.
2. Build `slow_path/mpc_planner.py`:
   - Input: current SOC, next 5 corners with their net_lift_values, minimum required SOC at end
   - Use `scipy.optimize.minimize` with method `SLSQP`
   - Objective: maximise sum of speeds across 5 corners (proxy: 1 - lift_fraction for each corner)
   - Constraint: SOC at end of 5 corners >= 0.25
   - Output: dict of {corner_id: recommended_action} for next 5 corners
   - Run this every 5 seconds using `asyncio.sleep(5)` in a background task
3. Build `slow_path/event_queue.py`: wrap Python `asyncio.Queue` with push/pop interface
4. Connect Granite: set up the first real async Granite call triggered by lap 10 completion
   - Pull last 10 lap summaries from Context Forge
   - Format them as the prompt (one sentence per lap: "Lap N: avg SOC {x}, alerts fired: {y}")
   - Call Granite, parse JSON response
   - Extract `threshold_updates` and write to `config/circuits/bahrain.json`
   - Extract `fan_explanation` and pass to Member 5's broadcast

---

### Member 5 — Output & UI Lead

**Your goal today:** UI updates in real time. Fan explanation updates from Granite. Audio timing is correct.

Tasks:
1. Enhance the UI: add the SOC trend line (last 5 laps, simple canvas line chart using vanilla JS)
2. Add alert deduplication: if the same `alert_id` arrives twice, ignore the second
3. Add fan explanation updates: when a Granite fan_explanation arrives (alert.fan_explanation != ""), update the fan panel
4. Add energy state plain text: translate soc_estimated into a plain sentence:
   - soc > 0.7: "Battery healthy — full boost available"
   - soc 0.5–0.7: "Battery at 60% — use boost selectively"
   - soc 0.3–0.5: "Battery low — limit boost usage"
   - soc < 0.3: "Battery critical — no boost available"
5. Test audio timing: run a fake braking sequence (brake=True for 2 seconds) and confirm audio queues, not interrupts

---

## Day 2 End — Integration Merge

**Day 2 integration test (45 min, everyone):**
1. Start mock server (Member 1)
2. Start WebSocket server (Member 5)
3. Open UI in browser (Member 5)
4. Run the full pipeline: `python fast_path/pipeline.py`
5. Watch for 10 minutes (equivalent to several laps of data)

**Checklist:**
- [ ] State vectors flowing with soc_estimated populated (not 0.0)
- [ ] Rules engine firing at least one non-safe-default alert
- [ ] Alert appears in UI within 250ms of firing
- [ ] Confidence bar visible and updating
- [ ] Audio plays for at least one alert (braking zone check working)
- [ ] At lap 10, Granite called — check terminal for Granite response logged
- [ ] Fan panel shows Granite explanation after lap 10

**If the full loop does not close by Day 2 end, defer Granite to Day 3 and mark it as a known item. Core fast path must work.**

---

## Day 3 — Testing, Edge Cases, Demo Prep

All members work together today. No new features. Fix, polish, test.

### Morning — Replay Test (2 hours)

Run the full Bahrain 2024 Race session through the system using the mock server.

**Test the complete 53-lap replay:**

```
Start mock server with full Bahrain 2024 Race data
Run pipeline at 4x speed
Log everything to a file
```

**After replay, check logs for:**
- Total alerts fired (expect 50–200 across 53 laps)
- Rules breakdown (what % of alerts were each rule type)
- Any unhandled exceptions (fix immediately)
- Average fast path latency (run `tests/latency_test.py`)
- CUSUM false positive rate (alerts that fired but had no follow-up confirmation)
- Context Forge: 53 lap summaries saved correctly

### Afternoon — Edge Cases (1.5 hours)

Test each scenario. One member runs, others observe:

**Edge Case 1 — Battery near zero (Member 3 runs)**
Manually set `soc_estimated = 0.05` in a state vector. Inject into queue.
Expected: `soc_danger_alert` fires, confidence high, audio queues.

**Edge Case 2 — Data gap (Member 1 runs)**
Stop mock server for 3 seconds. Restart.
Expected: `data_age_ms` rises, safe fallback fires, system recovers within 2 ticks.

**Edge Case 3 — Safety car (Member 3 runs)**
Set `session_flag = "sc"` for 5 state vectors.
Expected: `safety_car_recharge` fires with priority 10. All other rules suppressed.

**Edge Case 4 — Granite timeout (Member 4 runs)**
Set Granite client timeout to 0.1s (force timeout).
Expected: fast path continues unaffected. Error logged. Thresholds remain unchanged.

**Edge Case 5 — FAISS empty result (Member 4 runs)**
Query with a state vector far from any indexed vector.
Expected: FAISS returns empty. Confidence penalty applied. Rules engine still fires from thresholds alone.

### Late Afternoon — Demo Prep (1.5 hours)

**Demo session: Bahrain 2024 Race, laps 28–38**

This 10-lap window has high energy management pressure. Run it through the system and record what happens.

**Identify your 3 demo moments:**
1. An energy warning alert firing (soc_danger or cusum alarm)
2. A lift decision alert (lift_not_worth_it or optimal_recharge)
3. The fan explanation updating after Granite's lap 30 call

For each moment: note the lap number, corner, exact recommendation text, confidence score.

**Pre-generate demo audio:**
Run Member 5's `generate_audio()` for each alert that fires in your 3 demo moments. Cache the MP3 files. During the demo, play cached files — zero latency, best quality.

**Demo script (practice this twice):**

1. "WingMan solves the #1 unsolved problem in the 2026 season — energy-aero coupling."
2. Start replay at lap 28. Show live data flowing into the UI.
3. Lap 31: alert fires. Point to recommendation. Point to confidence. "73 milliseconds from telemetry to alert."
4. Play audio: "Recharge now — Turn eleven."
5. Switch to Langflow. Show pipeline. "Granite is reasoning in the background."
6. Fan panel updates. Read it out. "Same insight — for the fan watching at home."
7. "IBM Granite, Context Forge, Langflow, Docling. One stack. One system. The whole race weekend."

---

## Day 4 (Buffer) — If Needed

Use Day 4 only for:
- Fixing bugs found in Day 3 replay test
- Polishing UI (colours, layout)
- Adding GridSense skeleton (radio_transcript field, not full implementation)
- Rehearsing the demo 3 more times

Do not add new features on Day 4.

---

## Definition of Done — Team Version

All 5 members sign off on each item before the hackathon submission:

- [ ] Full 53-lap Bahrain replay runs without exception
- [ ] P95 fast path latency < 100ms (latency_test.py passes)
- [ ] At least 3 distinct rules fire during replay
- [ ] Safe fallback fires when data goes stale (edge case 2 passes)
- [ ] Granite called asynchronously — terminal shows call at lap 10 without blocking fast path
- [ ] Context Forge has all 53 lap summaries
- [ ] Audio fires correctly, skips braking zones
- [ ] UI updates in real time, both panels active
- [ ] Langflow pipeline visible and running
- [ ] Fan explanation visible in UI after Granite response
- [ ] Demo rehearsed at least 3 times end-to-end
- [ ] All 5 team members can explain what their component does in 2 sentences

---

## Quick Reference — Who To Ask

| If stuck on... | Ask |
|---|---|
| OpenF1 data not returning | Member 1 |
| SOC estimate looks wrong | Member 2 |
| Wrong rule firing | Member 3 |
| FAISS query crashing | Member 4 |
| UI not updating | Member 5 |
| Granite API auth error | Member 4 |
| asyncio event loop error | Any — common, usually a missing `await` |
| Corner IDs wrong | Member 1 |

---

## Resources — Bookmark These

| Resource | URL | Used by |
|---|---|---|
| OpenF1 API docs | https://openf1.org | Member 1 |
| FastF1 docs | https://docs.fastf1.dev | Members 1, 2, 4 |
| filterpy Kalman guide | https://filterpy.readthedocs.io | Member 2 |
| FAISS getting started | https://github.com/facebookresearch/faiss/wiki/Getting-started | Member 4 |
| FastAPI WebSocket | https://fastapi.tiangolo.com/advanced/websockets | Member 5 |
| SciPy SLSQP | https://docs.scipy.org/doc/scipy/reference/optimize.minimize-slsqp.html | Member 4 |
| IBM Granite API | IBM hackathon portal docs | Member 4 |
| gTTS docs | https://gtts.readthedocs.io | Member 5 |
