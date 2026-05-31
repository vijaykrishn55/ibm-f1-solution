# modules/shared_store.py
# One shared object. All 4 modules read from and write to this.
# Import: from modules.shared_store import store

import json
import os
from collections import defaultdict, deque


class SessionStore:
    def __init__(self):

        # ── WingMan writes ────────────────────────────────────
        self.soc_history     = deque(maxlen=500)   # (lap, corner_id, soc_estimated)
        self.alert_history   = []                  # all WingMan alerts fired

        # ── GhostDelta writes / GridSense reads ───────────────
        # {lap_number: {distance_bucket: speed}}
        self.lap_profiles    = {}
        self.optimal_profile = {}     # best lap speed profile {dist_bucket: speed}
        self.best_lap_time   = 999.0  # seconds, updated each lap
        self.best_lap_number = None
        self.current_lap_recording = {}  # being built this lap

        # ── TyreWhisperer writes / GridSense reads ────────────
        # per corner direction: deque of front-axle asymmetry values
        self.left_asym_history  = deque(maxlen=100)
        self.right_asym_history = deque(maxlen=100)
        self.asym_alarm         = False    # True when asymmetry is growing
        self.asym_alarm_side    = None     # "front_left" or "front_right"

        # ── GridSense writes ──────────────────────────────────
        self.setup_recommendations = []

        # ── Session meta ──────────────────────────────────────
        self.current_lap   = 0
        self.circuit       = "torcs"
        self.session_type  = "race"

    # ── Helpers ───────────────────────────────────────────────

    def record_soc(self, lap: int, corner_id: int, soc: float):
        self.soc_history.append((lap, corner_id, soc))

    def record_alert(self, alert: dict):
        self.alert_history.append(alert)

    def record_lap_point(self, dist_bucket: int, speed: float):
        """Called every tick by GhostDelta to build current lap profile."""
        # Keep the highest speed seen at each distance bucket (best pass)
        existing = self.current_lap_recording.get(dist_bucket, 0)
        if speed > existing:
            self.current_lap_recording[dist_bucket] = speed

    def finish_lap(self, lap_number: int, lap_time: float):
        """Called at lap end. Stores profile, updates optimal if fastest."""
        self.lap_profiles[lap_number]  = dict(self.current_lap_recording)
        self.current_lap_recording     = {}
        self.current_lap               = lap_number + 1

        if lap_time < self.best_lap_time and lap_time > 10.0:
            self.best_lap_time   = lap_time
            self.best_lap_number = lap_number
            self.optimal_profile = dict(self.lap_profiles[lap_number])

    def get_recent_laps(self, n: int = 3) -> list:
        """Returns last N lap profile dicts for GridSense correlation."""
        keys   = sorted(self.lap_profiles.keys())[-n:]
        return [self.lap_profiles[k] for k in keys]

    def save(self, path: str = "data/session_store.json"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {
            "best_lap_time":        self.best_lap_time,
            "best_lap_number":      self.best_lap_number,
            "alert_count":          len(self.alert_history),
            "laps_recorded":        len(self.lap_profiles),
            "setup_recommendations":self.setup_recommendations,
        }
        with open(path, "w") as f:
            json.dump(payload, f, indent=2)


# Single instance — import this everywhere
store = SessionStore()
