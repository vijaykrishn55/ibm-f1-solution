# WingMan — Hackathon README

## Overview

WingMan is a real-time F1 race intelligence system that ingests OpenF1 telemetry and targets low-latency (<100ms) engineer alerts, while running deeper strategic AI (IBM Granite) asynchronously. This README consolidates usage, setup, and offline/demo instructions to get you started quickly at a hackathon.

## What It Does

- Real-time fast path: Kalman filtering, CUSUM, FAISS, and rule engines for low-latency alerts and instant signals.
- Slow path: Context Forge + MPC + IBM Granite for strategic reasoning and human-friendly explanations.
- Four active modules: `WingMan` (energy/aero), `Race Radio Intelligence` (radio intelligence), `Lap Time Predictor` (lap delta), `Tyre Health Monitor` (tyre health & asymmetry).

## IBM Technologies Used

- IBM Granite — asynchronous strategy reasoning (recommended via local Ollama for offline demos).
- IBM Docling — optional radio transcription for Race Radio Intelligence (a lightweight mock transcription is included for demos when Docling is not available).
- Context Forge — session memory used by the slow path for richer, multi-turn reasoning.

## Prerequisites

Install core dependencies (example):

```bash
pip install aiohttp fastapi uvicorn pyttsx3 faiss-cpu numpy
pip install docling      # optional: Race Radio Intelligence (GridSense) transcription
pip install fastf1       # optional: prepare real fixtures
```

Ollama (recommended for offline Granite):

```bash
# Install Ollama → https://ollama.com/download
# ollama pull granite3.3:2b
```

## Quick Start — Live

1. Start the WebSocket server:

```bash
python -m output.websocket_server
```

2. Run the live ingest pipeline:

```bash
python run_openf1.py
```

3. Open the dashboard: http://localhost:8001/ui/index.html

## Quick Start — Offline Mode (Replay Fixtures)

Offline mode replays fixtures from `WingMan/tests/fixtures/` at ~4 Hz and activates all modules.

Prepare real fixtures (one-time, optional):

```bash
python scripts/prepare_real_fixtures.py --year 2024 --gp Bahrain --session Race --out-prefix real
```

Run offline replay (examples):

# Windows (cmd):
```bash
set FIXTURE_PREFIX=real
set FIXTURE_DURATION_S=180
python run_offline.py
```

# Unix/macOS:
```bash
export FIXTURE_PREFIX=real
export FIXTURE_DURATION_S=180
python run_offline.py
```

Alternative: loop a fixed number of laps using `FIXTURE_LOOP_LAPS`:

```bash
export FIXTURE_PREFIX=real
export FIXTURE_LOOP_LAPS=20
python run_offline.py
```

## Offline Details & Behavior

- Fixture files: `{prefix}_car_data.json`, `{prefix}_position.json`, `{prefix}_intervals.json` in `WingMan/tests/fixtures/`.
- Replay rate: 4 Hz (250 ms per tick).
- The runner synthesizes minimal telemetry fields (wheel speeds, steer, lap timings) when fixtures are sparse to ensure modules receive consistent, usable inputs.

## Key Files

- [WingMan/README.md](WingMan/README.md) — project overview and module descriptions
- [WingMan/TECHNICAL.md](WingMan/TECHNICAL.md) — deep technical reference
- [WingMan/OFFLINE_MODE.md](WingMan/OFFLINE_MODE.md) — offline guide and troubleshooting
- `run_offline.py` — offline runner (env vars: `FIXTURE_PREFIX`, `FIXTURE_LOOP_LAPS`, `FIXTURE_DURATION_S`)
- `scripts/prepare_real_fixtures.py` — helper to export FastF1 sessions to fixtures
- `modules/gridsense/radio_ingest.py` — Docling detection and demo transcription option

## Troubleshooting

- If alerts are not appearing in offline mode: check that fixture files exist and that `FIXTURE_PREFIX` matches the filenames; build the FAISS index (`offline/build_index.py`) if historical context is required.
- Race Radio Intelligence transcription: install `docling` to enable full transcription; a lightweight mock transcription option is available for demos when `docling` is not installed.
- Granite slow-path: run local Ollama and pull the `granite3.3:2b` model, or configure a remote Granite endpoint in `config/settings.yaml`.
- Port conflicts: `run_offline.py` binds to port `9000` by default; ensure the port is available or update the configuration.

## Next Steps (for demo)

1. Generate `real_*` fixtures with `scripts/prepare_real_fixtures.py` if you want live-session-derived data.
2. Option A (duration): run offline for `FIXTURE_DURATION_S=180` to get ~3 minutes of demo.
3. Option B (laps): set `FIXTURE_LOOP_LAPS` to cover 15–20 laps for a lap-based demo.
