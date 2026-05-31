"""Prepare real FastF1 telemetry fixtures at ~4Hz for offline replay.

Usage:
  python scripts/prepare_real_fixtures.py --year 2024 --gp Bahrain --session Race --out-prefix real

This script downloads a FastF1 session (requires network) and writes three
JSON fixtures into `WingMan/tests/fixtures/`:
  - {prefix}_car_data.json
  - {prefix}_position.json
  - {prefix}_intervals.json

It attempts a lightweight downsampling to ~4Hz to match the offline runner.
"""
import argparse
import json
import os
import math
from pathlib import Path

DEFAULT_PREFIX = "real"
OUT_DIR = Path(__file__).resolve().parents[1] / "WingMan" / "tests" / "fixtures"


def ensure_out_dir():
    OUT_DIR.mkdir(parents=True, exist_ok=True)


def write_json(name, data):
    path = OUT_DIR / name
    with open(path, "w") as f:
        json.dump(data, f)
    print(f"Wrote {path} ({len(data)} rows)")


def downsample_df_rows(rows, step):
    # rows: list of dicts; step: integer downsample factor
    if step <= 1:
        return rows
    return [r for i, r in enumerate(rows) if i % step == 0]


def build_fixtures_from_fastf1(year, gp, session_type, drivers, prefix):
    try:
        import fastf1
    except ImportError:
        raise RuntimeError("fastf1 is not installed in the current environment")

    cache_dir = os.path.join(os.path.dirname(__file__), "..", "WingMan", ".ff1_cache")
    os.makedirs(cache_dir, exist_ok=True)
    fastf1.Cache.enable_cache(cache_dir)

    print(f"Loading FastF1 session: {year} {gp} {session_type}")
    session = fastf1.get_session(year, gp, session_type)
    session.load(telemetry=True)

    car_rows = []
    pos_rows = []
    int_rows = []

    for driver in drivers:
        driver_laps = session.laps.pick_driver(driver)
        if driver_laps.empty:
            continue
        for _, lap in driver_laps.iterrows():
            try:
                tel = lap.get_car_data().add_distance()
            except Exception:
                continue
            # approximate lap duration and compute downsample step to ~4Hz
            n = len(tel)
            if n == 0:
                continue
            lap_time_s = float(tel.index[-1] - tel.index[0]).total_seconds() if hasattr(tel.index[0], 'tz') else (n / 100.0)
            # if lap_time_s is zero-ish, use default
            est_hz = n / max(1.0, lap_time_s)
            step = max(1, int(round(est_hz / 4.0)))

            for i, (_, row) in enumerate(tel.iterrows()):
                if i % step != 0:
                    continue
                car = {
                    "speed": float(row.get("Speed", 0.0)),
                    "throttle": float(row.get("Throttle", 0.0)) / 100.0,
                    "brake": bool(row.get("Brake", False)),
                    "drs": bool(row.get("DRS", 0)),
                    "gear": int(row.get("nGear", 0)) if "nGear" in row else int(row.get("Gear", 0)) if "Gear" in row else 0,
                    "rpm": float(row.get("RPM", 0.0)) if "RPM" in row else 0.0,
                    "distance": float(row.get("Distance", 0.0)),
                    "lap_number": int(lap.LapNumber) if hasattr(lap, 'LapNumber') else 0,
                }
                car_rows.append(car)

                pos = {
                    "x": float(row.get("X", 0.0)) if "X" in row else 0.0,
                    "y": float(row.get("Y", 0.0)) if "Y" in row else 0.0,
                    "z": float(row.get("Z", 0.0)) if "Z" in row else 0.0,
                }
                pos_rows.append(pos)

                # intervals: we don't have a direct gap to leader; set 0.0 placeholder
                int_rows.append({"gap_ahead": 0.0})

    # fallback: if nothing collected, raise
    if not car_rows:
        raise RuntimeError("No telemetry rows extracted from FastF1 session")

    # write files
    ensure_out_dir()
    write_json(f"{prefix}_car_data.json", car_rows)
    write_json(f"{prefix}_position.json", pos_rows)
    write_json(f"{prefix}_intervals.json", int_rows)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--year", type=int, default=2024)
    p.add_argument("--gp", type=str, default="Bahrain")
    p.add_argument("--session", type=str, default="Race")
    p.add_argument("--drivers", type=str, default="1,11,44,16",
                   help="comma-separated driver numbers to extract")
    p.add_argument("--out-prefix", type=str, default=DEFAULT_PREFIX)
    args = p.parse_args()

    drivers = [int(x) for x in args.drivers.split(",") if x.strip()]
    try:
        build_fixtures_from_fastf1(args.year, args.gp, args.session, drivers, args.out_prefix)
    except Exception as e:
        print("Error preparing fixtures:", e)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
