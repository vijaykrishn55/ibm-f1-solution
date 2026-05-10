# WingMan — Tech Stack & Algorithms Reference

---

## Data Sources

| Source | What it provides | When used |
|---|---|---|
| **OpenF1 API** | Live telemetry: speed, throttle, brake, DRS, position, intervals, team radio audio | Fast path — live session |
| **FastF1 (Python lib)** | Historical sessions: 240Hz telemetry, tyre data, sector times, weather | Offline — index build + calibration |
| **Pirelli tech bulletins** | Compound temperature windows, degradation characteristics | Offline — ingested via Docling |

---

## IBM Tools

| Tool | Role in WingMan | Depth of use |
|---|---|---|
| **IBM Granite 3.3** | Async strategy reasoning — interprets lap summaries, updates thresholds, generates fan explanations | Core reasoning engine. Never in fast path. |
| **Context Forge** | Session memory — stores lap summaries, alerts, Granite outputs across the full race weekend | Persistent state layer shared by all modules |
| **Langflow** | Visual AI workflow — shows the slow path pipeline running live during demo | Demo + fan explainer pipeline |
| **Docling** | Ingests Pirelli bulletins + driver radio audio → structured text | Offline calibration + GridSense radio transcription |

---

## Language & Runtime

| Component | Choice | Why |
|---|---|---|
| **Primary language** | Python 3.11+ | FastF1, NumPy, FAISS, SciPy all native. Async support with asyncio. |
| **Async runtime** | `asyncio` | Fast path and slow path run as concurrent coroutines. No threading complexity. |
| **Package manager** | `pip` + `requirements.txt` | Simple. No over-engineering for MVP. |

---

## Fast Path Stack

### Ingestion
| Component | Library/Tool | Notes |
|---|---|---|
| HTTP polling client | `httpx` (async) | Polls OpenF1 at 4Hz. Non-blocking. |
| WebSocket (if available) | `websockets` | Fallback to polling if WS not stable |
| Mock replay server | `FastAPI` | Serves cached OpenF1 JSON at 4Hz for offline testing |

### State Estimation
| Algorithm | Library | Purpose |
|---|---|---|
| **Kalman Filter** | `filterpy` or `NumPy` (manual) | Estimates battery SOC from noisy proxy signals (throttle, brake, speed, DRS) |
| **Extended Kalman Filter (EKF)** | `filterpy` | If SOC estimation model is non-linear (more accurate, slightly more complex) |
| **Circular buffer** | `collections.deque` | Sliding window stats per corner — O(1) insert and evict |

**Kalman state vector:** `[soc, soc_rate_of_change]`  
**Kalman measurement inputs:** throttle %, brake bool, speed (km/h), DRS state

### Change Detection
| Algorithm | Library | Purpose |
|---|---|---|
| **CUSUM (Cumulative Sum)** | Manual (20 lines) | Detects when SOC, speed, or energy delta has shifted from baseline. Reacts in 2–3 laps. |

**Why CUSUM over other detectors:**
- O(1) per update — no sliding window recomputation
- Tunable sensitivity via a single threshold parameter
- Well-understood false positive rate
- Proven in industrial monitoring, appropriate for this use case

### Scenario Retrieval
| Component | Library | Notes |
|---|---|---|
| **FAISS (Flat Index)** | `faiss-cpu` | Exact ANN search over pre-indexed historical state vectors. Returns top-3 nearest historical analogues. |
| **Feature vector** | 8 dimensions | `[soc, throttle, speed_norm, corner_id, lap_fraction, aero_state, energy_delta, gap_ahead]` |
| **Normalisation** | `scikit-learn` `MinMaxScaler` | Applied offline during index build. Parameters saved and reapplied to live data. |

**FAISS index type:** `IndexFlatL2` for MVP (exact search, no approximation error).  
Upgrade to `IndexIVFFlat` if vector count exceeds 50k.

### Decision Engine
| Component | Approach | Notes |
|---|---|---|
| **Rules Engine** | Config-driven Python dict evaluation | Rules loaded from `config/circuits/<circuit>.json`. Stateless per call. Priority-ranked. |
| **Confidence Scorer** | Weighted formula | Combines rule priority, FAISS match quality, data freshness, Kalman uncertainty |
| **Safe fallback** | Hard-coded default | Fires when confidence < 0.6 or data stale > 2s. Always recommends "maintain current mode." |

---

## Slow Path Stack

### Event Queue
| Component | Choice | Notes |
|---|---|---|
| **MVP queue** | `asyncio.Queue` | In-memory, zero setup, same interface as Redis. Swap later. |
| **Production queue** | `Redis pub/sub` | If deploying beyond laptop. Persistent, multi-consumer. |

### Planning
| Algorithm | Library | Purpose |
|---|---|---|
| **MPC (Model Predictive Control)** | `scipy.optimize.minimize` (SLSQP method) | Plans optimal energy deployment over 5-corner horizon. Runs every 5 seconds async. |

**MPC problem setup:**
- State: `[soc, lap_position]`
- Control variable: `[lift_fraction]` per corner (0 = no lift, 1 = full lift)
- Objective: maximise speed integral across 5 corners
- Constraints: SOC at end of window >= minimum required; no lift in corners where net lift value < 0
- Solver: SLSQP (Sequential Least Squares Programming) — handles constrained non-linear optimisation

### Reasoning & Memory
| Component | Tool | Notes |
|---|---|---|
| **Granite 3.3** | IBM API | Async call. Triggered by lap events, not by time. Returns structured JSON. |
| **Context Forge** | IBM tool | Session memory. In-memory dict during session, persisted to disk every 5 laps. |
| **Threshold writer** | Atomic dict update | Granite outputs → validated → written to rules engine config. Fast path reads on next tick. |

---

## Output Stack

| Component | Library/Tool | Notes |
|---|---|---|
| **WebSocket server** | `websockets` or `FastAPI` WebSocket | Pushes alert payloads to UI. Push-only. No polling from UI. |
| **TTS (offline)** | `pyttsx3` | Zero latency, no network. Lower voice quality. |
| **TTS (demo)** | Google TTS (`gTTS`) → cached `.mp3` | Pre-generate for 10 demo scenarios. High quality. |
| **UI** | HTML + CSS + vanilla JS | Two-panel: engineer alerts (left) + fan explainer (right). No framework needed. |
| **Langflow pipeline** | IBM Langflow | Visual slow-path pipeline shown during demo |

---

## Offline / Pre-Race Stack

| Task | Tool | Notes |
|---|---|---|
| Historical data load | `fastf1` Python library | Session telemetry, tyre data, lap times |
| Feature extraction | `pandas` + `NumPy` | Extract state vectors from raw lap telemetry |
| FAISS index build | `faiss-cpu` | Indexes all extracted vectors. Saved to disk. Loaded at session start. |
| Threshold calibration | `scikit-learn` + `pandas` | Compute `net_lift_value` per corner from historical energy and lap time data |
| Normalisation params | `scikit-learn` `MinMaxScaler` | Fit on historical data. Saved as `.pkl`. Applied to live data. |
| Docling (Pirelli bulletins) | IBM Docling | Parse compound temp windows into structured JSON for calibration |

---

## Testing Stack

| Tool | Purpose |
|---|---|
| `pytest` | Unit tests per module |
| `pytest-asyncio` | Testing async fast path coroutines |
| `FastAPI` mock server | Replay historical OpenF1 data at 4Hz for integration tests |
| `time.perf_counter` | Latency profiling per stage |
| Manual edge case scripts | Battery 0%, stale data, Granite timeout, SC flag scenarios |

---

## Algorithm Summary

| Algorithm | Category | Latency | Where |
|---|---|---|---|
| Kalman Filter | State estimation | ~5ms | Fast path |
| CUSUM | Change detection | ~2ms | Fast path |
| FAISS ANN (IndexFlatL2) | Similarity search | ~20ms | Fast path |
| Rules engine | Decision logic | ~1ms | Fast path |
| Confidence scoring | Scoring | ~2ms | Fast path |
| MPC (SLSQP) | Optimal control | ~200ms | Slow path (async) |
| Granite 3.3 | LLM reasoning | 3–8s | Slow path (async) |

**Total fast path:** ~73ms P50, <100ms P95

---

## Key Dependencies (pip install)

```
fastf1              # Historical F1 telemetry
httpx               # Async HTTP client for OpenF1
filterpy            # Kalman filter
faiss-cpu           # ANN vector search
scipy               # MPC optimisation (SLSQP)
scikit-learn        # Feature normalisation
pandas              # Data manipulation
numpy               # Numerical operations
websockets          # WebSocket server
fastapi             # Mock server + WebSocket endpoint
uvicorn             # ASGI server for FastAPI
pyttsx3             # Offline TTS
gTTS                # Google TTS for demo audio
redis               # Event queue (optional, production)
pytest              # Testing
pytest-asyncio      # Async test support
```

---

## Data Flow Summary

```
[OpenF1 WebSocket]
      ↓ raw telemetry (4Hz)
[Kalman Filter] → filtered SOC + uncertainty
      ↓
[CUSUM + Sliding Window] → change flags + trend stats
      ↓
[FAISS ANN Lookup] → top-3 historical analogues
      ↓
[Rules Engine] ← circuit thresholds (from config JSON)
      ↓
[Confidence Scorer]
      ↓
[Alert Payload Builder] → WebSocket → UI (text)
                        → TTS → Audio (driver)
                        → Event Queue → Slow Path

[Slow Path — async]
[asyncio.Queue] → [MPC Planner] → [Granite 3.3] → [Context Forge]
                                                         ↓
                                              Updated thresholds
                                                         ↓
                                              Rules Engine config (next tick)
                                                         ↓
                                              Fan explanation → Langflow → UI
```

---

## Extensibility — How Future Modules Plug In

| Module | What it adds to this stack |
|---|---|
| **GridSense** | Docling radio transcription → new state vector fields → 2 new rules in rules engine |
| **GhostDelta** | Optimal lap baseline in FAISS → new `delta_from_optimal` field → qualifying-mode rules |
| **TyreWhisperer** | Corner direction flag → separate CUSUM per direction → new asymmetry alert rule |

No existing component is modified. Each module extends the state vector, adds rules, and optionally adds a new FAISS query type.
