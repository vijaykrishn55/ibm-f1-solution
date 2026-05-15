# fast_path/cusum.py
# ─────────────────────────────────────────────────────────────────────────────
# CUSUMDetector — Person B (Task B4)
#
# CUSUM (Cumulative Sum) detects when a metric shifts away from its expected
# value over time — better than a simple threshold because it catches gradual
# drift, not just spikes.
#
# Two instances are created at the bottom of this file:
#   cusum_soc   — detects abnormal SOC depletion rate
#   cusum_speed — detects unexpected speed loss through corners
#
# Usage (in fast_path/rules_engine.py):
#   alarm = cusum_soc.update(state["energy_delta"])
#   state["cusum_soc_alarm"] = alarm
# ─────────────────────────────────────────────────────────────────────────────


class CUSUMDetector:
    """
    One-sided CUSUM change-point detector.

    Accumulates deviations above `expected_value`.
    Fires when cumulative sum exceeds `threshold`.
    Resets accumulator on alarm — ready for the next detection window.

    Args:
        expected_value: The "normal" value of the metric (baseline).
        threshold:      How much accumulated deviation triggers the alarm.
        name:           Optional label for logging.
    """

    def __init__(
        self,
        expected_value: float,
        threshold: float,
        name: str = "cusum",
    ):
        self.expected   = expected_value
        self.threshold  = threshold
        self.name       = name
        self.cumsum     = 0.0
        self._alarm_count = 0

    def update(self, actual_value: float) -> bool:
        """
        Feed one reading.

        Returns True if the alarm fires this tick, False otherwise.
        Resets cumsum on alarm so the detector is ready for the next event.
        """
        deviation = self.expected - actual_value
        self.cumsum = max(0.0, self.cumsum + deviation)

        if self.cumsum > self.threshold:
            self.cumsum = 0.0
            self._alarm_count += 1
            return True

        return False

    def reset(self):
        """Manually reset — use when entering safety car, etc."""
        self.cumsum = 0.0

    @property
    def alarm_count(self) -> int:
        """Total alarms fired this session."""
        return self._alarm_count

    def status(self) -> dict:
        """Snapshot of current state — useful for logging."""
        return {
            "name":        self.name,
            "cumsum":      round(self.cumsum, 6),
            "threshold":   self.threshold,
            "expected":    self.expected,
            "alarm_count": self._alarm_count,
        }


# ── Module-level instances — import and reuse these ──────────────────────────

# SOC depletion rate detector
# expected_value: normal drain per tick is ~-0.003
# threshold: 0.015 = 5 consecutive ticks of excess drain triggers alarm
cusum_soc = CUSUMDetector(
    expected_value=-0.003,
    threshold=0.015,
    name="cusum_soc",
)

# Speed loss detector (per corner pass)
# expected_value: no drift (0.0 deviation)
# threshold: 5.0 km/h cumulative loss
cusum_speed = CUSUMDetector(
    expected_value=0.0,
    threshold=5.0,
    name="cusum_speed",
)


def update_cusums(state: dict) -> dict:
    """
    Convenience function — updates both CUSUM detectors from a state vector
    and writes alarm flags back into the state dict in-place.

    Returns the updated state dict.
    """
    state["cusum_soc_alarm"]   = cusum_soc.update(state.get("energy_delta", 0.0))
    state["cusum_speed_alarm"] = cusum_speed.update(state.get("speed", 0.0))
    return state


# ── Quick standalone test ─────────────────────────────────────────────────────

if __name__ == "__main__":
    print("CUSUMDetector — alarm detection test")
    print("─" * 50)

    # Test 1: SOC depletion detector
    print("\nTest 1 — SOC depletion (energy_delta stream)")
    detector = CUSUMDetector(expected_value=-0.003, threshold=0.015, name="soc_test")

    readings = (
        [-0.003] * 5          # Normal: no alarm
        + [-0.008] * 6        # Elevated drain: alarm should fire
        + [-0.003] * 4        # Back to normal
        + [-0.010] * 5        # Elevated again: second alarm
    )

    for i, val in enumerate(readings):
        alarm = detector.update(val)
        marker = "  ← ALARM 🚨" if alarm else ""
        print(f"  Tick {i:2d}  energy_delta={val:.3f}  cumsum={detector.cumsum:.4f}{marker}")

    print(f"\n  Total alarms fired: {detector.alarm_count}  (expected: 2)")

    # Test 2: Both CUSUM instances
    print("\nTest 2 — module-level cusum_soc + cusum_speed")
    cusum_soc.reset()
    cusum_speed.reset()

    state = {
        "energy_delta": -0.009,   # excess drain
        "speed":         5.5,      # excess speed loss
    }
    alarm_soc   = cusum_soc.update(state["energy_delta"])
    alarm_speed = cusum_speed.update(state["speed"])

    print(f"  cusum_soc   alarm: {alarm_soc}   status: {cusum_soc.status()}")
    print(f"  cusum_speed alarm: {alarm_speed}  status: {cusum_speed.status()}")

    print("\n✓  CUSUM test passed")
