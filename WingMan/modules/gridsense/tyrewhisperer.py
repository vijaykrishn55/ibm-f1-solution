# modules/tyrewhisperer.py (OpenF1 edition)
#
# Primary signal: wheel speed asymmetry (wheel_fl vs wheel_fr) — used when available.
# Fallback signal: sector-time asymmetry — used when wheel_fl/fr are 0.0 (OpenF1).
#
# Sector-time asymmetry logic:
#   Track average corner-exit speed for left-corners vs right-corners per lap.
#   If left-corner exit speed is consistently < right-corner exit speed over 3+ laps,
#   the left front is degrading (slower exit = less grip on entry).
#   Same conclusion as wheel-speed asymmetry, 1-2 laps slower to detect.

import uuid
import time
from collections import deque
from modules.shared_store import store

MIN_SPEED_KMH         = 60.0
ASYMMETRY_ALARM_DELTA = 5.0    # km/h difference in exit speed = alarm
MIN_LAPS_BEFORE_FIRE  = 3      # need at least 3 laps of sector data
FIRE_EVERY_N_CORNERS  = 8
CUSUM_THRESHOLD       = 20.0   # accumulated corner-speed deficit


class TyreWhisperer:

    def __init__(self):
        # Wheel-speed mode (TORCS)
        self._left_wheel_buf  = deque(maxlen=60)
        self._right_wheel_buf = deque(maxlen=60)

        # Sector-time mode (OpenF1) — track exit speed per direction per lap
        self._lap_left_speeds  = deque(maxlen=5)   # per-lap avg exit speed, left corners
        self._lap_right_speeds = deque(maxlen=5)   # per-lap avg exit speed, right corners
        self._this_lap_left    = []
        self._this_lap_right   = []
        self._last_lap         = 0

        # CUSUM
        self._cusum_left  = 0.0
        self._cusum_right = 0.0

        # State
        self._corner_count      = 0
        self._in_corner_prev    = False
        self._last_alert_corner = -999

    def update(self, state: dict) -> dict | None:
        speed      = state.get("speed", 0.0)
        corner_dir = state.get("corner_direction", "straight")
        corner_id  = state.get("corner_id", 0)
        lap        = state.get("lap", 0)
        wheel_fl   = state.get("wheel_fl", 0.0)
        wheel_fr   = state.get("wheel_fr", 0.0)
        steer      = abs(state.get("steer", 0.0))

        if speed < MIN_SPEED_KMH or corner_dir == "straight":
            return None

        use_wheel_speed = (wheel_fl > 0.1 or wheel_fr > 0.1)

        in_corner = steer > 0.05 or corner_id > 0
        if in_corner and not self._in_corner_prev:
            self._corner_count += 1

        self._in_corner_prev = in_corner

        # ── Mode 1: Wheel speed (TORCS) ──────────────────────────────────────
        if use_wheel_speed and in_corner:
            asymmetry = abs(wheel_fl - wheel_fr)
            if corner_dir == "left":
                self._left_wheel_buf.append(asymmetry)
                self._cusum_left = max(0.0, self._cusum_left + asymmetry - 2.0)
            else:
                self._right_wheel_buf.append(asymmetry)
                self._cusum_right = max(0.0, self._cusum_right + asymmetry - 2.0)

            return self._evaluate_wheel(corner_id)

        # ── Mode 2: Sector-time asymmetry (OpenF1) ────────────────────────────
        if in_corner:
            if corner_dir == "left":
                self._this_lap_left.append(speed)
                self._cusum_left = max(0.0, self._cusum_left - speed + 90.0)
            else:
                self._this_lap_right.append(speed)
                self._cusum_right = max(0.0, self._cusum_right - speed + 90.0)

        # Lap boundary — store per-lap averages
        if lap > self._last_lap and self._last_lap > 0:
            if self._this_lap_left:
                self._lap_left_speeds.append(sum(self._this_lap_left) / len(self._this_lap_left))
            if self._this_lap_right:
                self._lap_right_speeds.append(sum(self._this_lap_right) / len(self._this_lap_right))
            self._this_lap_left  = []
            self._this_lap_right = []

        self._last_lap = lap

        return self._evaluate_sector(corner_id, lap)

    # ── Evaluation helpers ────────────────────────────────────────────────────

    def _evaluate_wheel(self, corner_id: int) -> dict | None:
        if (self._corner_count % FIRE_EVERY_N_CORNERS != 0
                or self._corner_count == self._last_alert_corner):
            return None
        if len(self._left_wheel_buf) < 10 or len(self._right_wheel_buf) < 10:
            return None

        l = sum(self._left_wheel_buf)  / len(self._left_wheel_buf)
        r = sum(self._right_wheel_buf) / len(self._right_wheel_buf)

        alarm_side = None
        if l > r * 1.35:  alarm_side = "front_left"
        elif r > l * 1.35: alarm_side = "front_right"
        if self._cusum_left  > CUSUM_THRESHOLD: alarm_side = "front_left";  self._cusum_left  = 0.0
        if self._cusum_right > CUSUM_THRESHOLD: alarm_side = "front_right"; self._cusum_right = 0.0

        if not alarm_side:
            return None
        self._last_alert_corner = self._corner_count
        return self._build_alert(alarm_side, l, r, corner_id, "wheel_speed")

    def _evaluate_sector(self, corner_id: int, lap: int) -> dict | None:
        if lap < MIN_LAPS_BEFORE_FIRE:
            return None
        if (self._corner_count % FIRE_EVERY_N_CORNERS != 0
                or self._corner_count == self._last_alert_corner):
            return None
        if len(self._lap_left_speeds) < MIN_LAPS_BEFORE_FIRE:
            return None
        if len(self._lap_right_speeds) < MIN_LAPS_BEFORE_FIRE:
            return None

        l = sum(self._lap_left_speeds)  / len(self._lap_left_speeds)
        r = sum(self._lap_right_speeds) / len(self._lap_right_speeds)

        alarm_side = None
        if r - l > ASYMMETRY_ALARM_DELTA:   alarm_side = "front_left"   # slower in left corners
        elif l - r > ASYMMETRY_ALARM_DELTA: alarm_side = "front_right"
        if self._cusum_left  > CUSUM_THRESHOLD: alarm_side = "front_left";  self._cusum_left  = 0.0
        if self._cusum_right > CUSUM_THRESHOLD: alarm_side = "front_right"; self._cusum_right = 0.0

        if not alarm_side:
            return None
        self._last_alert_corner = self._corner_count
        return self._build_alert(alarm_side, l, r, corner_id, "sector_asymmetry")

    def _build_alert(self, alarm_side: str, l: float, r: float,
                     corner_id: int, method: str) -> dict:
        store.asym_alarm      = True
        store.asym_alarm_side = alarm_side
        store.left_asym_history.append(l)
        store.right_asym_history.append(r)

        laps = self._corner_count // 15
        rec  = (f"{alarm_side.replace('_', '-').title()} grip degrading — "
                f"left avg {l:.1f} vs right avg {r:.1f}. "
                f"~{max(2, 5 - laps)} laps before driver feels it. [{method}]")
        fan  = (f"The AI detected one front tyre gripping less through corners — "
                f"the {alarm_side.replace('_', ' ')} is wearing unevenly. "
                f"Driver hasn't felt it yet.")

        return {
            "alert_id":        str(uuid.uuid4()),
            "timestamp":       time.time(),
            "type":            "tyre_asymmetry",
            "module":          "tyrewhisperer",
            "source_module":   "tyrewhisperer",
            "priority":        "medium",
            "recommendation":  rec,
            "reason":          f"{alarm_side} asymmetry delta: {abs(l - r):.2f}",
            "confidence":      min(0.92, 0.65 + abs(l - r) / 20.0),
            "corner":          corner_id,
            "fan_explanation": fan,
            "audio_text":      f"{alarm_side.replace('_', ' ').title()} grip dropping",
        }


# Backwards-compatible alias: human-friendly display name
TyreHealthMonitor = TyreWhisperer
