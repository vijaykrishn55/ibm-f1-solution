# state/window.py
# ─────────────────────────────────────────────────────────────────────────────
# CornerWindow — Person B (Task B3)
#
# Maintains a per-corner sliding window of recent SOC and speed readings.
# Used by the rules engine to detect trends (is SOC improving or worsening
# through this corner over the last N laps?).
#
# Usage:
#   window = CornerWindow(maxlen=5)
#   window.push(corner_id=11, soc=0.42, speed=215.0)
#   trend = window.soc_trend(corner_id=11)   # > 0 = recovering, < 0 = depleting
# ─────────────────────────────────────────────────────────────────────────────

from collections import defaultdict, deque
from statistics import mean


class CornerWindow:
    """
    Sliding window statistics per corner.

    Each corner keeps the last `maxlen` readings of SOC and speed.
    Trend = last_value - first_value (over the window).
    """

    def __init__(self, maxlen: int = 5):
        self.maxlen = maxlen
        # {corner_id: deque([{"soc": float, "speed": float}, ...])}
        self.windows: dict[int, deque] = defaultdict(
            lambda: deque(maxlen=maxlen)
        )

    # ── Write ────────────────────────────────────────────────────────────────

    def push(self, corner_id: int, soc: float, speed: float) -> None:
        """Add one reading for a corner."""
        self.windows[corner_id].append({"soc": soc, "speed": speed})

    def push_from_state(self, state: dict) -> None:
        """Convenience: push directly from a state vector dict."""
        self.push(
            corner_id=state["corner_id"],
            soc=state["soc_estimated"],
            speed=state["speed"],
        )

    # ── Read ─────────────────────────────────────────────────────────────────

    def mean_soc(self, corner_id: int) -> float:
        """Mean SOC over the window for this corner. 0.0 if no data."""
        w = self.windows.get(corner_id)
        if not w:
            return 0.0
        return mean(r["soc"] for r in w)

    def mean_speed(self, corner_id: int) -> float:
        """Mean speed over the window for this corner. 0.0 if no data."""
        w = self.windows.get(corner_id)
        if not w:
            return 0.0
        return mean(r["speed"] for r in w)

    def soc_trend(self, corner_id: int) -> float:
        """
        SOC trend for this corner.
        Positive → SOC recovering over recent passes.
        Negative → SOC depleting over recent passes.
        0.0 if fewer than 2 readings.
        """
        w = self.windows.get(corner_id)
        if not w or len(w) < 2:
            return 0.0
        return w[-1]["soc"] - w[0]["soc"]

    def speed_trend(self, corner_id: int) -> float:
        """
        Speed trend for this corner.
        Positive → getting faster.
        Negative → losing time through this corner.
        """
        w = self.windows.get(corner_id)
        if not w or len(w) < 2:
            return 0.0
        return w[-1]["speed"] - w[0]["speed"]

    def window_size(self, corner_id: int) -> int:
        """Number of readings currently stored for this corner."""
        return len(self.windows.get(corner_id, []))

    def all_corner_ids(self) -> list[int]:
        """Return all corner IDs that have at least one reading."""
        return [cid for cid, w in self.windows.items() if len(w) > 0]

    def summary(self) -> dict:
        """Return a dict summary of all corners — useful for logging."""
        return {
            cid: {
                "readings":    self.window_size(cid),
                "mean_soc":    round(self.mean_soc(cid), 4),
                "soc_trend":   round(self.soc_trend(cid), 4),
                "mean_speed":  round(self.mean_speed(cid), 2),
                "speed_trend": round(self.speed_trend(cid), 2),
            }
            for cid in sorted(self.all_corner_ids())
        }


# ── Quick standalone test ─────────────────────────────────────────────────────

if __name__ == "__main__":
    print("CornerWindow — sliding window test")
    print("─" * 50)

    window = CornerWindow(maxlen=5)

    # Simulate 8 passes through corners 4, 10, 11
    test_data = [
        # (corner_id, soc, speed)
        (4,  0.72, 220.0),
        (10, 0.65, 195.0),
        (11, 0.60, 285.0),
        (4,  0.70, 218.0),
        (10, 0.62, 193.0),
        (11, 0.57, 282.0),
        (4,  0.68, 215.0),
        (10, 0.61, 191.0),
        (11, 0.55, 280.0),
    ]

    for cid, soc, speed in test_data:
        window.push(cid, soc, speed)

    print("\nWindow summary:")
    for cid, stats in window.summary().items():
        print(f"  Corner {cid:2d}: {stats}")

    print(f"\nCorner 4  SOC trend:   {window.soc_trend(4):.4f}  (negative = depleting)")
    print(f"Corner 11 speed trend: {window.speed_trend(11):.2f} km/h")
    print(f"Corner 10 mean SOC:    {window.mean_soc(10):.4f}")

    print("\n✓  Window test passed")
