"""
test_drive.py — Minimal standalone TORCS driver.

Bypasses ALL WingMan pipeline code. Just drives the car using the exact
same logic as snakeoil3_gym's drive_example() which is proven to work.

Usage (from WingMan/ directory, with TORCS running on blue screen):
    python test_drive.py

Expected output: car moves forward, shifts gears, navigates Alpine-2.
If the car moves → TORCS connection is fine, issue is in run_torcs.py.
If the car doesn't move → TORCS setup issue.
"""
import sys
import os
import math
import time

# ── Add gym_torcs to path ─────────────────────────────────────────────────────
TORCS_PATH = r"E:\ibm\ibm f1 solution\gym_torcs"
sys.path.insert(0, TORCS_PATH)

PI = math.pi


def drive(C):
    """
    Exact copy of drive_example() from snakeoil3_gym.py.
    Proven to work. Do NOT change coefficients.
    """
    S, R = C.S.d, C.R.d
    target_speed = 300

    # Steer To Corner (gain 15/pi — DO NOT reduce, car won't turn)
    R['steer'] = S.get('angle', 0) * 15 / PI
    # Steer To Centre
    R['steer'] -= S.get('trackPos', 0) * 0.10

    # Throttle Control
    speed = S.get('speedX', 0)
    if speed < target_speed - (R['steer'] * 50):
        R['accel'] += 0.01
    else:
        R['accel'] -= 0.01
    if speed < 10:
        R['accel'] += 1 / (speed + 0.1)

    # Traction Control System — prevents rear-wheel spin-outs
    wheel = S.get('wheelSpinVel', [0, 0, 0, 0])
    if len(wheel) >= 4:
        if (wheel[2] + wheel[3]) - (wheel[0] + wheel[1]) > 5:
            R['accel'] -= 0.2

    # Automatic Transmission (speedX is in km/h)
    R['gear'] = 1
    if speed > 50:  R['gear'] = 2
    if speed > 80:  R['gear'] = 3
    if speed > 110: R['gear'] = 4
    if speed > 140: R['gear'] = 5
    if speed > 170: R['gear'] = 6


def main():
    import snakeoil3_gym as snakeoil

    print("=" * 55)
    print("  WingMan — Standalone TORCS Drive Test")
    print("  (No pipeline, no asyncio — pure drive_example)")
    print("=" * 55)
    print(f"  Connecting to TORCS on port 3001 ...")
    print("  Press Ctrl+C to stop\n")

    C = snakeoil.Client(p=3001)
    print(f"  Connected! maxSteps={C.maxSteps}\n")

    tick = 0
    t_start = time.time()

    try:
        for step in range(C.maxSteps, 0, -1):
            C.get_servers_input()
            drive(C)
            C.respond_to_server()
            tick += 1

            # Print status every 50 ticks (~every 1-2 seconds)
            if tick % 50 == 0:
                S = C.S.d
                R = C.R.d
                elapsed = time.time() - t_start
                speed   = S.get('speedX',  0)
                gear    = R.get('gear',    1)
                steer   = R.get('steer',   0)
                accel   = R.get('accel',   0)
                tp      = S.get('trackPos', 0)
                angle   = S.get('angle',   0)
                print(
                    f"  t={elapsed:5.1f}s  tick={tick:4d}  "
                    f"speed={speed:6.1f} km/h  gear={gear}  "
                    f"steer={steer:+.3f}  accel={accel:.2f}  "
                    f"trackPos={tp:+.2f}  angle={angle:+.3f}"
                )

    except KeyboardInterrupt:
        print("\n  Stopped by Ctrl+C.")
    finally:
        C.shutdown()
        elapsed = time.time() - t_start
        print(f"\n  Completed {tick} ticks in {elapsed:.1f}s ({tick/max(elapsed,1):.0f} Hz)")


if __name__ == "__main__":
    main()
