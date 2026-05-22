"""
ingestion/control_mapper.py
─────────────────────────────────────────────────────────────────────────────
Alert-to-Control Mapper — Layer 2 of the TORCS integration.

Translates WingMan fast-path alerts into physical throttle modifiers that
the autopilot (in torcs_adapter.py) applies each tick.

Each WingMan rule maps to a specific accel_modifier:
  - optimal_recharge_window  → 0.80  (lift 20% — net_lift_value > 0)
  - lift_not_worth_it        → 1.00  (hold full throttle — don't lift)
  - soc_danger_alert         → 0.65  (aggressive recharge before boost zone)
  - safety_car_recharge      → 0.30  (max recharge — free window)
  - cusum_soc_alarm          → 0.85  (early warning, gentle lift)
  - stale_data_fallback      → 1.00  (safe mode — don't act on bad data)
  - safe_default             → 1.00  (no change — autopilot runs normally)

Thread-safe via a simple float that the autopilot reads each tick.
─────────────────────────────────────────────────────────────────────────────
"""

import threading
import time


# ── Rule → accel modifier mapping ────────────────────────────────────────────

ACCEL_MODIFIERS = {
    "optimal_recharge_window": 0.80,   # Lift 20% — energy gain worth aero cost
    "lift_not_worth_it":       1.00,   # Hold full throttle — don't lift here
    "soc_danger_alert":        0.65,   # Aggressive recharge before boost zone
    "safety_car_recharge":     0.30,   # Max recharge — "highest value window"
    "cusum_soc_alarm":         0.85,   # Early warning, gentle intervention
    "stale_data_fallback":     1.00,   # Safe mode — don't trust alerts
    "safe_default":            1.00,   # No change — autopilot runs normally
}

# Modifier decays back toward 1.0 after this many seconds with no new alert
DECAY_TIMEOUT_S = 3.0


class ControlMapper:
    """
    Holds a shared accel_modifier that the autopilot reads each tick.

    - apply(alert)     — called by the alert consumer when a new alert fires
    - get_modifier()   — called by the autopilot each tick to get current modifier
    - last_alert_info  — dict with info about the last applied alert (for logging)
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._accel_modifier = 1.0
        self._last_apply_time = time.time()
        self._last_rule = "safe_default"
        self._last_confidence = 0.0
        self._last_corner_id = 0

    def apply(self, alert: dict) -> None:
        """
        Read a WingMan alert and update the accel_modifier accordingly.
        Called from the alert consumer task.
        """
        rule = alert.get("rule", "safe_default")
        modifier = ACCEL_MODIFIERS.get(rule, 1.0)

        with self._lock:
            self._accel_modifier = modifier
            self._last_apply_time = time.time()
            self._last_rule = rule
            self._last_confidence = alert.get("confidence", 0.0)
            self._last_corner_id = alert.get("corner_id", 0)

    def get_modifier(self) -> float:
        """
        Returns the current accel modifier for the autopilot.
        Decays back toward 1.0 if no alert has been applied recently.
        """
        with self._lock:
            age = time.time() - self._last_apply_time
            if age > DECAY_TIMEOUT_S:
                # Smooth decay: linearly blend back toward 1.0
                decay_progress = min(1.0, (age - DECAY_TIMEOUT_S) / DECAY_TIMEOUT_S)
                return self._accel_modifier + (1.0 - self._accel_modifier) * decay_progress
            return self._accel_modifier

    @property
    def last_alert_info(self) -> dict:
        """Info about the last applied alert, for logging."""
        with self._lock:
            return {
                "rule":       self._last_rule,
                "modifier":   self._accel_modifier,
                "confidence": self._last_confidence,
                "corner_id":  self._last_corner_id,
                "age_s":      round(time.time() - self._last_apply_time, 1),
            }


# ── Module-level singleton — shared between adapter and alert consumer ───────

_mapper = ControlMapper()


def apply(alert: dict) -> None:
    """Apply an alert to update the throttle modifier."""
    _mapper.apply(alert)


def get_modifier() -> float:
    """Get the current accel modifier for the autopilot."""
    return _mapper.get_modifier()


def last_alert_info() -> dict:
    """Get info about the last applied alert."""
    return _mapper.last_alert_info
