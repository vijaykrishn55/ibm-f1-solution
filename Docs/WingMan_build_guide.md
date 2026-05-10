# WingMan — Full Build Guide
### Real-Time F1 Energy-Aero Intelligence System
> Build order: WingMan → GridSense → GhostDelta → TyreWhisperer  
> Every decision here is made with the full system in mind.

---

## Before You Write a Single Line

Understand the two rules that govern this entire build:

**Rule 1 — Fast path never waits for AI.**  
Granite, MPC, Context Forge — none of them are in the path between telemetry arriving and an alert firing. They run in the background and update parameters. The fast path uses those parameters but never calls those services directly.

**Rule 2 — Build for the system, not just the feature.**  
WingMan's pipeline is the backbone. GridSense, GhostDelta, and TyreWhisperer are modules that plug into it. Every folder, every data contract, every config file you create here should be designed to accept a new module without restructuring.

---

## Project Structure

Design this folder structure on day one. Do not deviate from it.

```
WingMan/
│
├── config/
│   ├── circuits/          # Per-circuit threshold JSON files
│   │   └── bahrain.json   # Corner thresholds, energy maps
│   └── settings.yaml      # Global config (API keys, SLO targets, model params)
│
├── ingestion/
│   ├── openf1_stream.py   # WebSocket / polling client
│   ├── fastf1_loader.py   # Historical session loader
│   └── mock_server.py     # Replay server for offline testing
│
├── state/
│   ├── kalman.py          # Battery SOC + speed estimation
│   ├── window.py          # Sliding window stats per corner
│   └── session_state.py   # Live session object (shared across all modules)
│
├── fast_path/
│   ├── cusum.py           # Change-point detection
│   ├── faiss_index.py     # ANN lookup over historical states
│   ├── rules_engine.py    # Decision logic — reads from config/circuits/
│   └── confidence.py      # Confidence scoring + safe fallback
│
├── slow_path/
│   ├── event_queue.py     # Redis or asyncio queue
│   ├── mpc_planner.py     # 5-corner energy horizon planner
│   ├── granite_client.py  # Async Granite API wrapper
│   └── context_forge.py   # Session memory manager
│
├── output/
│   ├── alert_builder.py   # Structures the fast-path alert payload
│   ├── tts.py             # Text-to-audio conversion
│   └── websocket_server.py # Pushes alerts to UI
│
├── ui/
│   └── index.html         # Two-panel interface (engineer + fan)
│
├── offline/
│   ├── build_index.py     # FAISS index builder from FastF1 data
│   ├── calibrate.py       # Threshold calibration per circuit
│   └── feature_extract.py # State vector extractor from raw laps
│
└── tests/
    ├── replay_test.py     # Full session replay test
    ├── latency_test.py    # SLO assertions per stage
    └── edge_cases.py      # Battery 0%, stale data, aero unknown
```

**Why this matters for later modules:**  
GridSense adds `modules/gridsense/`. TyreWhisperer adds `modules/tyrewhisperer/`. They each write into `session_state.py` and read from the same rules engine. The folder structure never changes — modules just add to it.

---

## Phase 1 — Data Foundation
### Days 1–3 | Goal: Telemetry is flowing. You can see real F1 data on screen.

### Step 1.1 — Understand your data sources

Before touching code, spend 2 hours understanding exactly what OpenF1 returns and at what rate.

**OpenF1 gives you:**
- Car data: Speed, Throttle (0–100), Brake (boolean), RPM, Gear, DRS state
- Position data: X, Y, Z coordinates on circuit
- Intervals: Gap to car ahead, gap to leader
- Session status: Flag states (green, yellow, SC, VSC, red)
- Team radio: Audio file URLs with timestamps

**OpenF1 limitations to know now:**
- Polling rate is approximately 3–4 Hz. That means one data point every 250ms.
- There is no guaranteed push. You poll. Design for this.
- Latency from real-world event to API availability is 1–3 seconds in live sessions.
- Battery State of Charge (SOC) is NOT directly available. You estimate it. This is where Kalman comes in.

**FastF1 gives you:**
- Full lap-by-lap telemetry sampled at ~240Hz (much higher resolution)
- Tyre compound, stint length, tyre age per lap
- Weather data per lap
- Sector times, personal bests, position changes
- Use this exclusively for offline index building and calibration.

### Step 1.2 — Define the State Vector

This is the most important design decision in the entire project. The state vector is the data structure that flows through every stage of the fast path. It must be defined once, here, and never changed without updating everything downstream.

**WingMan State Vector v1:**

```
{
  "timestamp": float,          # Unix timestamp of this reading
  "driver": str,               # Driver code e.g. "VER"
  "lap": int,                  # Current lap number
  "corner_id": int,            # Which corner (1–N for the circuit)
  "lap_fraction": float,       # 0.0 to 1.0, position in lap
  "speed": float,              # km/h, Kalman-filtered
  "throttle": float,           # 0.0–1.0, raw
  "brake": bool,               # True/False
  "drs": bool,                 # DRS open/closed
  "aero_state": str,           # "straight_mode" or "corner_mode"
  "soc_estimated": float,      # 0.0–1.0, Kalman output (not raw)
  "soc_raw": float,            # Proxy value before filtering
  "energy_delta": float,       # MJ change since last reading
  "gap_ahead": float,          # Seconds to car ahead
  "session_flag": str,         # "green", "yellow", "sc", "vsc"
  "data_age_ms": int           # How old is this reading in ms
}
```

**Extension hooks already built in:**  
GridSense will add `"radio_transcript": str` and `"complaint_detected": bool`.  
TyreWhisperer will add `"corner_direction": str` and `"entry_understeer_delta": float`.  
GhostDelta will add `"optimal_speed_reference": float` and `"delta_from_optimal": float`.  
Design the vector as a dict so adding keys never breaks existing consumers.

### Step 1.3 — Build the OpenF1 ingestion client

The ingestion client does one job: poll OpenF1 endpoints at 4Hz and populate the state vector. It runs as a continuous async loop.

**What it must handle:**
- Successful response → parse and push to state queue
- HTTP error → log, skip this tick, do not crash
- Response older than 2 seconds (stale) → set `data_age_ms` accordingly and push anyway (stale handling is downstream)
- Session not started yet → poll at 1Hz until session flag goes green, then switch to 4Hz

**Endpoints to poll in priority order:**
1. `/v1/car_data` — throttle, speed, brake, DRS
2. `/v1/position` — X, Y, Z (for corner_id calculation)
3. `/v1/intervals` — gap_ahead
4. `/v1/session_status` — flag state

**Corner ID calculation:**  
Map the X/Y position to a corner number using a pre-built circuit geometry file. For Bahrain, this is a simple lookup: divide the circuit into segments, assign each segment a corner ID. Store this in `config/circuits/bahrain.json`.

### Step 1.4 — Build the FastF1 historical loader

The historical loader is only used offline (index building and calibration). It does not run during a live session.

**What it loads:**
- Select 3–5 sessions from the same circuit (e.g., Bahrain FP1, FP2, FP3, Qualifying, Race from 2023 and 2024)
- For each session, for each driver, for each lap: extract a state vector in the same schema as Step 1.2
- Save extracted vectors to disk as a flat file (Parquet or CSV) for the index builder

**Validation before moving to Phase 2:**
- Run the ingestion client against OpenF1's live demo endpoint or a cached session response
- Print the state vector to console every 250ms
- Confirm all fields are populated (SOC will be 0 until Kalman is built — that is fine)
- Confirm corner_id is being assigned correctly by checking that the sequence is 1, 2, 3... through a lap

---

## Phase 2 — State Estimation
### Days 4–6 | Goal: SOC is estimated accurately. Noise is filtered. State vector is trustworthy.

### Step 2.1 — Why you need a Kalman filter

OpenF1 does not give you battery SOC directly. You derive it from observable proxies: throttle application (energy draw), brake application (energy recovery), DRS state (drag load), speed (power required).

The problem is these proxies are noisy. A raw calculation gives you a jagged SOC estimate that fires false alerts constantly.

The Kalman filter takes the noisy proxy estimate and produces a smooth, probabilistically optimal estimate. It also gives you a confidence interval — you know not just the estimated SOC but how certain you are about it.

### Step 2.2 — Kalman filter design

**State to estimate:** `[soc, soc_rate_of_change]`  
This is a 2-dimensional state. SOC right now, and how fast it is changing.

**Measurement inputs:**
- Throttle percentage → energy draw rate
- Brake boolean → energy recovery rate
- Speed → background power consumption
- DRS state → additional drag load

**Tuning parameters (set these empirically):**
- Process noise: how much can SOC change between ticks? Start conservative.
- Measurement noise: how noisy are the proxy signals? OpenF1 throttle is reasonably clean.

**Calibration approach:**  
Use FastF1 historical data where you can back-calculate actual energy usage from lap performance. Run your Kalman estimate against the historical session and measure how far off it is from the back-calculated ground truth. Tune process and measurement noise until the error is below 5% SOC.

### Step 2.3 — Sliding window statistics

Alongside the Kalman filter, maintain a sliding window over the last N readings per corner. This gives you trend information, not just instantaneous state.

**Windows to maintain:**
- Per corner, last 5 laps: average energy delta, average entry speed
- Session-wide: SOC at lap start (to track stint depletion trend)
- Last 10 readings: rolling standard deviation of SOC (detects unusual patterns)

**Implementation note:**  
Use a circular buffer per corner. Pre-allocate fixed size. This keeps memory flat and access O(1). Do not use a list with append and slice — that causes garbage collection spikes during live sessions.

### Step 2.4 — Stale data handling

If the last reading is more than 2 seconds old, the state vector is stale. Do not stop the fast path — stale data is better than no data for the rules engine. But flag it.

**Stale data protocol:**
- Set `data_age_ms` to the actual age
- Rules engine checks this field before firing any alert
- If data is stale: rules engine is allowed to fire only "safe" recommendations (do not change anything, maintain current mode)
- Log every stale event with timestamp for post-session review

**Validation before moving to Phase 3:**
- Feed recorded OpenF1 data through the Kalman filter
- Plot raw SOC proxy vs Kalman-filtered SOC over a full lap
- The filtered line should be smooth with less than 3% oscillation
- Validate that the sliding window correctly resets between stints (after a pit stop, lap counter resets)

---

## Phase 3 — Fast Path
### Days 7–12 | Goal: Telemetry in, alert out, under 100ms, deterministically.

This is the core of WingMan. Everything before this was preparation. Everything after this is enhancement.

### Step 3.1 — Offline: Build the FAISS index

Before the fast path can run, you need the historical scenario index. This is built once offline and loaded at session start.

**Feature vector for indexing (subset of state vector):**
```
[soc_estimated, throttle, speed_normalised, corner_id_encoded,
 lap_fraction, aero_state_encoded, energy_delta, gap_ahead_clamped]
```
8 dimensions. Normalise every feature to [0, 1] range using values from historical data. Store the normalisation parameters — you will apply the same normalisation to live data during lookup.

**What to store alongside each vector:**
- What action was taken next (lift, boost, maintain)
- What happened to lap time in the following 3 corners
- What the energy state was 5 corners later
- Circuit and session ID for filtering

**Index type:** FAISS flat index (IndexFlatL2) for MVP. Exact nearest neighbour. Fast enough for < 10k vectors on any laptop. If you load 5 sessions × 20 cars × 70 laps × ~50 telemetry points per lap = ~350k vectors, you may need IndexIVFFlat with 100 clusters. Profile this on your hardware on day 7.

**Output of a lookup:** Top-3 nearest historical states + their outcomes. The rules engine uses this as supporting evidence, not as the sole decision.

### Step 3.2 — Offline: Calibrate circuit thresholds

For each corner on each circuit, calibrate the following thresholds from historical data:

- `lift_energy_gain[corner_id]`: Average MJ recovered when lifting in this corner
- `lift_aero_loss_time[corner_id]`: Average lap time cost of lifting (from aero loss) in this corner
- `net_lift_value[corner_id]`: gain minus cost. Positive = lifting is worth it. Negative = do not lift.
- `soc_danger_threshold`: The SOC level below which the overtake zone cannot be used
- `boost_zone_corners`: List of corners immediately before DRS zones

Store all of this in `config/circuits/bahrain.json`. This is the file Granite updates during slow path operation. The rules engine reads it at runtime.

**This is the energy-aero coupling answer.** You have now quantified, per corner, whether lifting to recharge is worth it on this circuit. No team has built this from data. This is your differentiator.

### Step 3.3 — Build CUSUM change-point detector

CUSUM (Cumulative Sum) detects when a signal has shifted from its baseline. You use it to detect:
- SOC dropping faster than expected (energy problem)
- Speed falling in a specific corner across laps (grip or mechanical issue)
- Energy delta per lap increasing (driver recovering less than usual — DRS issue, driving style shift)

**How CUSUM works here:**
- Maintain a running cumulative sum of the deviation from expected value
- When the cumulative sum crosses a threshold → change-point detected → alert triggered
- Reset the cumulative sum after detection

**Tune the threshold** using FastF1 replay data. The goal: detect real changes within 3 laps. False positive rate under 5% per session.

**CUSUM feeds the rules engine.** It does not directly fire alerts. It sets a flag in the state: `"cusum_soc_alarm": true`. The rules engine decides what to do with that flag.

### Step 3.4 — Build the Rules Engine

The rules engine is the final decision maker in the fast path. It reads the current state vector, the FAISS lookup result, and the calibrated thresholds, and returns a recommendation.

**Rules engine design principles:**
- Stateless per call. Everything it needs comes in as input. No hidden state.
- Config-driven. Rules are read from `config/circuits/bahrain.json`, not hard-coded.
- Every rule has a priority (1–10). Higher priority wins if two rules conflict.
- Every rule outputs a recommendation + confidence score + reason string.

**Core rules for WingMan (define these first):**

```
RULE: soc_danger_alert
IF soc_estimated < soc_danger_threshold
AND upcoming_corner in boost_zone_corners
THEN recommend: "Recharge now — insufficient energy for boost zone"
Priority: 9

RULE: lift_not_worth_it
IF net_lift_value[corner_id] < 0
AND driver_attempting_lift (throttle < 0.2)
THEN recommend: "Remove lift in this corner — aero loss exceeds energy gain"
Priority: 7

RULE: optimal_recharge_window
IF net_lift_value[corner_id] > 0.05
AND soc_estimated < 0.6
AND session_flag == "green"
THEN recommend: "Lift in this corner — net energy gain with acceptable aero cost"
Priority: 6

RULE: safe_default
IF no other rule fires OR confidence < 0.6
THEN recommend: "Maintain current mode"
Priority: 1
```

**The safe default rule is critical.** The system must always have an answer. When uncertain, it recommends doing nothing. No alert is better than a wrong alert during a race.

### Step 3.5 — Confidence scoring

Every recommendation gets a confidence score before it leaves the fast path.

**Score calculation:**
- Base score from rule priority: high-priority rule → starts at 0.8
- FAISS boost: if top-3 nearest neighbours all had the same outcome → +0.15
- Penalty: if data_age_ms > 500 → -0.2
- Penalty: if CUSUM recently reset (signal unstable) → -0.1
- Penalty: if soc_estimated uncertainty (from Kalman covariance) > 10% → -0.1

**If final score < 0.6:** Output the safe default recommendation regardless of what other rules fired.

**The confidence score is shown in the UI.** Engineers learn to trust the system by seeing how confident it is. A 0.92 alert on lap 30 means something different from a 0.61 alert on lap 1.

### Step 3.6 — End-to-end fast path assembly

Wire the stages together into a single async function that runs every 250ms (every OpenF1 poll tick).

**The sequence per tick:**
1. Ingest: receive new telemetry, build raw state vector
2. Kalman update: pass raw SOC proxy → get filtered SOC + uncertainty
3. Window update: push new reading into circular buffers
4. CUSUM update: update change detectors, set alarm flags
5. FAISS lookup: query nearest historical scenarios (async, but fast)
6. Rules evaluation: evaluate all rules against current state + FAISS results
7. Confidence scoring: score the top recommendation
8. Output: if recommendation differs from last output OR confidence changed significantly → push alert to output layer

**Validation before moving to Phase 4:**
- Run full replay of Bahrain 2024 Race through the fast path
- Measure P95 latency from step 1 to step 8. Assert < 100ms.
- Manually inspect 10 alert events. Do they make sense given the race context?
- Confirm safe fallback fires when data goes stale (simulate by pausing the mock server)

---

## Phase 4 — Slow Path
### Days 13–15 | Goal: Granite is reasoning asynchronously. Rules thresholds improve during the session.

### Step 4.1 — Event queue

The event queue decouples the fast path from the slow path. The fast path pushes events in. The slow path consumes them at its own pace. The fast path never blocks waiting for the slow path.

**Events to push:**
- Every lap completion: lap summary (average SOC per corner, lap time, key decisions made)
- Every alert that fired: what rule, what corner, what confidence
- Every CUSUM change-point detection

**For MVP:** Use Python's `asyncio.Queue`. It's in-memory, zero setup, zero dependencies. You can swap it for Redis in production. The interface is identical.

**Queue design:** The slow path consumer reads from the queue in a loop. It batches events if multiple arrive quickly (process the last 5 events together rather than one by one). This prevents the slow path from falling behind during a high-event moment (safety car, energy management crisis).

### Step 4.2 — MPC Planner

The MPC (Model Predictive Control) planner runs every 5 seconds. It looks 5 corners ahead and calculates the optimal energy deployment plan for that window.

**Inputs:**
- Current SOC estimate
- Current lap position
- Next 5 corners: their `net_lift_value`, whether any are boost zones
- Minimum SOC required at end of the window (ensure enough for the next DRS zone)

**What it optimises:**
- Maximise total speed across the 5 corners
- Subject to: SOC at end >= minimum required
- Subject to: No lift in corners where `net_lift_value < 0`

**Output:**
- For each of the next 5 corners: recommended action (lift / maintain / boost)
- Expected SOC at each corner
- Estimated lap time impact of the plan

**This output is pushed to the rules engine as an updated parameter file.** Not every tick — only when the plan changes significantly. The rules engine reads the new plan on its next tick.

### Step 4.3 — Granite integration

Granite runs asynchronously. It is triggered by specific events, not by the passage of time.

**Trigger events for a Granite call:**
- Lap 1 complete (build initial session context)
- Every 10 laps (update strategy reasoning)
- CUSUM fires a major alarm (ask Granite for strategic interpretation)
- Driver stint end / pit stop (full strategy re-evaluation)

**What you send to Granite:**
- Last N lap summaries (from Context Forge)
- Current circuit thresholds
- MPC planner recommendation for next 10 laps
- Current position and gap to competitors

**What you ask Granite for:**
- Should any circuit thresholds be adjusted based on what's happened in this session?
- Is there a strategic pattern (competitor behaviour, weather, tyre degradation) the fast path should know about?
- Generate the fan explanation for the last major decision

**What Granite returns (structured JSON):**
- Updated threshold adjustments (if any) with confidence
- Strategy notes for Context Forge
- Fan explanation paragraph

**Critical:** Granite's threshold adjustments are not applied blindly. They go through a validation check: the adjustment must be within a pre-defined safe range (e.g., Granite cannot set `soc_danger_threshold` below 0.1 — that would be unsafe). If valid, write to the config file. Fast path picks it up on next tick.

### Step 4.4 — Context Forge

Context Forge is the memory of the session. It accumulates knowledge across the full race weekend.

**What it stores:**
- Lap-by-lap summaries: key decisions, energy performance, lap time
- Alert history: what fired, when, confidence, driver response
- Granite reasoning outputs: strategic notes, threshold changes
- Session metadata: circuit, weather, compounds, driver stint plan

**Storage design:**
- In-memory Python dict during the session (fast access)
- Append to disk every 5 laps (durability — if the laptop crashes, you don't lose everything)
- Loaded at session start from previous session file if one exists

**Why this matters for GridSense:**  
When GridSense is added, it will write radio transcripts and complaints into Context Forge. The Granite call for WingMan will then automatically receive driver feedback as additional context — making the threshold reasoning smarter. This is why you design Context Forge now with a flexible schema.

**Validation before moving to Phase 5:**
- Trigger a Granite call manually with a sample lap summary
- Confirm the response parses correctly to the expected JSON schema
- Confirm that a threshold adjustment from Granite updates the rules engine on the next fast path tick
- Confirm Context Forge survives a session restart (saves to disk, loads on restart)

---

## Phase 5 — Output Layer
### Days 16–17 | Goal: Engineer sees the alert. Fan hears the explanation. Both in under 1 second from alert generation.

### Step 5.1 — Alert payload structure

The fast path produces a recommendation. The output layer turns that into a structured alert payload for the UI.

**Alert payload:**
```
{
  "alert_id": str,              # Unique ID for deduplication
  "timestamp": float,
  "driver": str,
  "type": str,                  # "energy_warning" / "lift_recommendation" / "boost_ready"
  "recommendation": str,        # Short action text for engineer
  "reason": str,                # Why this recommendation
  "confidence": float,          # 0.0–1.0
  "corner": int,                # Where this applies
  "corners_ahead": int,         # How soon this is relevant
  "fan_explanation": str,       # Plain language (from slow path Granite)
  "audio_text": str,            # What TTS should say (shorter than recommendation)
  "module": str                 # "WingMan" — for future multi-module routing
}
```

**The `module` field is for the full system.**  
When GridSense sends an alert, `module` = "gridsense". The UI routes it to the correct panel. One alert schema, multiple modules.

### Step 5.2 — Text-to-audio

The audio output is for the driver. It must be:
- Short: maximum 8 words. Drivers cannot process more at race speed.
- Unambiguous: no conditionals. "Recharge now — Turn 8" not "Consider recharging if SOC is low."
- Timed: only fire audio when the driver has time to act (not in the middle of a braking zone)

**Corner timing logic:**  
Before firing audio, check if the driver is currently in a braking zone (brake = true, speed rapidly decreasing). If yes, queue the audio for the next straight. Do not interrupt a braking event.

**TTS choice for MVP:** Python's `pyttsx3` (offline, zero latency, no API call) or Google TTS (better voice quality, requires network). For demo purposes, use Google TTS pre-generated for your 5–10 demo scenarios and cache the audio files. Play cached files in the demo — zero latency, high quality.

### Step 5.3 — WebSocket server and UI

The UI is a two-panel display. Left panel: engineer view. Right panel: fan view.

**Engineer panel shows:**
- Current recommendation (large, colour-coded by type)
- Confidence bar
- SOC trend over last 5 laps (simple line)
- Last 3 alerts with timestamps

**Fan panel shows:**
- Plain language explanation of the last action
- Current energy state in plain words ("Battery at 60% — one boost left this lap")
- What to watch for next

**WebSocket design:**  
The server pushes a new alert payload every time one is generated by the fast path. The UI receives it and updates. No polling from the UI side — push only.

**Alert deduplication:**  
Use `alert_id`. If the UI receives the same alert twice (network glitch), it checks the ID and ignores the duplicate. This prevents double audio firing.

---

## Phase 6 — Integration and Testing
### Days 18–19 | Goal: The full system runs end-to-end on historical data without crashing. Latency SLOs are met.

### Step 6.1 — Full replay test

**Test session:** Bahrain 2024 Race (53 laps, full energy management pressure).

**How to run it:**
- Start the mock server with the Bahrain race data
- Start the full pipeline (ingestion → fast path → slow path → output)
- Let it run at 4x speed (1 hour of race in 15 minutes)
- Log every alert, every Granite call, every threshold update

**What to check:**
- No unhandled exceptions across the full 53 laps
- Fast path SLO: P95 < 100ms. Measure this.
- Slow path: Granite called at the right trigger events (every 10 laps)
- Context Forge: 53 lap summaries stored correctly
- Audio: no audio fires during simulated braking zones
- Safe fallback: fires at least once (force a stale data event to confirm)

### Step 6.2 — Latency profiling

Run the fast path 1000 times with recorded data. Collect timing per stage.

**Assert the following:**
- P50 (median) < 60ms
- P95 < 100ms
- P99 < 200ms (occasional GC pause is acceptable)
- No single stage exceeds its allocated budget by more than 2x

If FAISS is slow: reduce index size or switch from L2 to cosine similarity (slightly faster for normalised vectors).  
If rules engine is slow: check for unnecessary repeated dict lookups. Cache the circuit config at startup.  
If Kalman is slow: profile the NumPy operations. Usually fast. If slow, something is being recomputed that should be pre-computed.

### Step 6.3 — Edge case testing

Test these scenarios explicitly:

**Scenario 1: Battery drops to near zero mid-lap**  
Expected: energy_warning fires with high confidence. Audio queued for next straight. Safe fallback does NOT fire (this is a real alert, not an uncertain one).

**Scenario 2: Data gap of 3 seconds (network interruption)**  
Expected: data_age_ms increases. After 2 seconds, all fast path recommendations switch to safe fallback. When data resumes, system recovers within 2 ticks (500ms).

**Scenario 3: Granite API timeout**  
Expected: slow path logs the timeout. Fast path continues unaffected. Thresholds remain at last known good values.

**Scenario 4: Safety car deployed (session_flag = "sc")**  
Expected: All energy recommendations pause. Rules engine in SC mode: recommend recharge aggressively (free energy recovery, no performance cost). Resume normal rules when green flag resumes.

**Scenario 5: First lap (no FAISS history yet)**  
Expected: FAISS lookup returns empty. Confidence penalty applied. Rules still fire from calibrated thresholds. System functional from lap 1.

---

## Phase 7 — Demo Preparation
### Day 20 | Goal: A 5-minute demo that makes judges forget every other team.

### Step 7.1 — Choose your demo session

Use Bahrain 2024 Race, laps 28–35. This window has:
- Active energy management pressure
- A period where multiple cars are managing deployment
- Clear example of a lift-and-coast decision point
- Enough context for a meaningful fan explanation

### Step 7.2 — The demo script

**Minute 1 — The problem statement**  
Show a clip of driver radio from 2026 testing complaining about energy management. Say: "This is the #1 unsolved problem in the 2026 season. WingMan solves it."

**Minute 2 — Live fast path**  
Start the replay at lap 28. Show the engineer panel. On lap 31, a lift_not_worth_it alert fires. Point to the screen: "The driver is lifting in Turn 5. WingMan detects the aero loss exceeds the energy gain. It recommends removing the lift. Confidence 87%."

**Minute 3 — The audio**  
Play the audio output: "Remove lift — Turn 5." Say: "8 words. Driver hears it and acts. No engineer intervention needed for the routine decision."

**Minute 4 — Langflow and the slow path**  
Switch to Langflow. Show the pipeline running. Granite just processed lap 30. Show the fan explanation: "The car's wing was closing in the wrong corner — losing grip to save energy that wasn't worth saving. AI caught it." Say: "This is the same insight, for the fan in the grandstand."

**Minute 5 — The pitch**  
"This is built on IBM Granite, Context Forge, and Langflow. It reacts in 73 milliseconds. It gets smarter every lap. And it's solving a problem that every team on the 2026 grid is struggling with right now."

### Step 7.3 — Pre-cache for the demo

Before the demo:
- Generate Granite fan explanations for laps 28–35 and cache them
- Pre-generate audio files for the 8 alert types you expect to fire
- Test the full demo flow 5 times end-to-end
- Have a fallback: a screen recording of the system running perfectly, if anything breaks live

---

## Extensibility Map — What Changes When You Add Each Module

When you build GridSense next:
- Add `ingestion/docling_radio.py` — radio audio → transcript
- Add `modules/gridsense/` — complaint detection + telemetry correlation
- Add `"radio_transcript"` and `"complaint_detected"` to the state vector
- Add 2 new rules to the rules engine: `radio_complaint_correlated`, `setup_recommendation`
- Context Forge already stores session state — GridSense adds to the same object
- UI left panel adds a third card: GridSense setup recommendation

When you build GhostDelta:
- Add `modules/ghostdelta/` — optimal lap baseline tracker
- Add `"optimal_speed_reference"` and `"delta_from_optimal"` to state vector
- Activate only during qualifying sessions (check session type on startup)
- Output goes to a third panel in the UI

When you build TyreWhisperer:
- Add `modules/tyrewhisperer/` — corner direction asymmetry scanner
- Add `"corner_direction"` to state vector (derived from X/Y geometry)
- Add CUSUM per corner direction (separate detectors for left vs right)
- Output folds into the engineer panel as a new alert type

**The rule:** Each new module adds to the pipeline. It does not modify existing stages.

---

## Definition of Done — WingMan MVP

Before you call WingMan complete and move to GridSense, all of these must be true:

- [ ] Full Bahrain race replay runs without exception
- [ ] P95 fast path latency < 100ms (measured, not estimated)
- [ ] At least 3 distinct rule types fire during the replay
- [ ] Safe fallback fires correctly on stale data injection
- [ ] Granite called asynchronously — fast path never waits for it
- [ ] Context Forge has 53 lap summaries after full race replay
- [ ] Audio fires correctly, skips braking zones
- [ ] Two-panel UI updates in real time from WebSocket
- [ ] Langflow pipeline visible and running during demo
- [ ] State vector has extension fields ready (radio_transcript etc. as null)
- [ ] Demo script tested 3 times end-to-end

When all boxes are checked: GridSense build begins.
