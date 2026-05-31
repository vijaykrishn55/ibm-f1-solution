# modules/ghostdelta.py
# Tracks every lap. Compares it to the best lap seen this session.
# At each lap end fires: "Lap N was X.Xs off optimal. Most time lost: corner Y."
#
# Called every tick from run_torcs.py:
#   alert = ghostdelta.update(state)
#   if alert: await broadcast(alert)

import uuid
import time
from modules.shared_store import store


# ── Tuning ─────────────────────────────────────────────────────────────────────
DIST_BUCKET_SIZE  = 100    # metres — group track into 100m segments
MIN_LAP_TIME_S    = 20.0   # ignore laps shorter than this (incomplete laps)
MAX_LAP_TIME_S    = 600.0  # ignore laps longer than this (paused/stuck)
MIN_SPEED_TO_RECORD = 20.0 # don't record when essentially stationary


class GhostDelta:

    def __init__(self, track_length_m: float = 3773.0):
        """
        track_length_m: total track length. Default is TORCS Alpine-2.
        For Bahrain (FastF1): use 5412.0
        """
        self.track_length    = track_length_m
        self._prev_lap_time  = 0.0   # last tick's curLapTime
        self._prev_lap_num   = 0
        self._lap_start_time = None

    def update(self, state: dict) -> dict | None:
        """
        Call every tick. Returns alert dict at lap end, else None.
        """
        lap_time_current = state.get("lap_time_current", 0.0)
        last_lap_time    = state.get("last_lap_time", 0.0)
        dist_from_start  = state.get("lap_fraction", 0.0) * self.track_length
        speed            = state.get("speed", 0.0)
        lap_num          = state.get("lap", 0)

        # Record current position into shared store
        if speed >= MIN_SPEED_TO_RECORD:
            bucket = int(dist_from_start / DIST_BUCKET_SIZE) * DIST_BUCKET_SIZE
            store.record_lap_point(bucket, speed)

        # Detect lap completion: curLapTime resets to near 0
        lap_just_ended = (
            self._prev_lap_time > 5.0
            and lap_time_current < 2.0
            and last_lap_time > MIN_LAP_TIME_S
            and last_lap_time < MAX_LAP_TIME_S
        )

        self._prev_lap_time = lap_time_current

        if not lap_just_ended:
            return None

        # ── Lap just ended ────────────────────────────────────────────────────
        completed_lap = lap_num - 1 if lap_num > 0 else 0
        store.finish_lap(completed_lap, last_lap_time)

        # Need at least one reference lap to compare against
        if store.best_lap_number is None or store.best_lap_number == completed_lap:
            return self._build_first_lap_alert(completed_lap, last_lap_time)

        # ── Compare current lap to optimal ────────────────────────────────────
        current_profile  = store.lap_profiles.get(completed_lap, {})
        optimal_profile  = store.optimal_profile

        if not current_profile or not optimal_profile:
            return None

        corner_deltas   = {}
        total_delta     = 0.0

        # Get all distance buckets present in both profiles
        common_buckets = sorted(set(current_profile.keys()) & set(optimal_profile.keys()))

        for bucket in common_buckets:
            curr_speed = current_profile[bucket]
            opt_speed  = optimal_profile[bucket]
            speed_diff = opt_speed - curr_speed   # positive = current was slower

            corner_id = self._bucket_to_corner(bucket)
            corner_deltas[corner_id] = round(corner_deltas.get(corner_id, 0.0) + speed_diff, 3)

            if speed_diff > 1.0:   # only count meaningful losses (> 1 km/h)
                # Approximate time loss: distance / avg_speed difference
                avg_speed_ms = max(opt_speed, 1.0) / 3.6
                time_lost    = (speed_diff / 3.6) * (DIST_BUCKET_SIZE / avg_speed_ms) * 0.5
                total_delta += time_lost

        # Find the most notable corner, even if the lap is nearly clean.
        worst_corner = max(corner_deltas, key=lambda k: abs(corner_deltas[k])) if corner_deltas else None
        worst_delta  = corner_deltas.get(worst_corner, 0.0)

        lap_time_delta = last_lap_time - store.best_lap_time

        # Build recommendation
        if total_delta < 0.1:
            recommendation = (
                f"Lap {completed_lap}: {last_lap_time:.2f}s — "
                f"clean lap, within 0.1s of optimal."
            )
            fan_text = (
                f"Nearly perfect lap — the car was within a tenth of a second "
                f"of its best time this session."
            )
            confidence = 0.90
        else:
            recommendation = (
                f"Lap {completed_lap}: {last_lap_time:.2f}s "
                f"(+{lap_time_delta:.2f}s vs best lap {store.best_lap_number}). "
                f"Biggest loss: corner {worst_corner} — {worst_delta:.2f}s."
            )
            fan_text = (
                f"This lap was {abs(lap_time_delta):.1f} seconds slower than the "
                f"car's best. The biggest chunk of time was lost in corner "
                f"{worst_corner}, where the car was carrying less speed."
            )
            confidence = min(0.92, 0.70 + min(total_delta / 2.0, 0.22))

        alert = {
            "alert_id":        str(uuid.uuid4()),
            "timestamp":       time.time(),
            "type":            "lap_delta",
            "module":          "ghostdelta",
            "recommendation":  recommendation,
            "reason":          f"Lap time: {last_lap_time:.2f}s | Best: {store.best_lap_time:.2f}s",
            "confidence":      confidence,
            "corner":          worst_corner or 0,
            "fan_explanation": fan_text,
            "audio_text":      f"Lap {completed_lap}: {lap_time_delta:+.1f} seconds",
            "ghost_data": {
                "lap":           completed_lap,
                "lap_time":      last_lap_time,
                "best_lap_time": store.best_lap_time,
                "delta":         round(lap_time_delta, 3),
                "corner_deltas": {str(k): round(v, 3) for k, v in corner_deltas.items()},
                "worst_corner":  worst_corner,
                "worst_delta":   round(worst_delta, 3),
            }
        }

        return alert

    def _build_first_lap_alert(self, lap_num: int, lap_time: float) -> dict:
        """First lap — no comparison yet, just record it."""
        return {
            "alert_id":        str(uuid.uuid4()),
            "timestamp":       time.time(),
            "type":            "lap_delta",
            "module":          "ghostdelta",
            "recommendation":  f"Lap {lap_num}: {lap_time:.2f}s — reference lap set.",
            "reason":          "First complete lap — establishing optimal baseline.",
            "confidence":      1.0,
            "corner":          0,
            "fan_explanation": (
                f"Lap {lap_num} completed in {lap_time:.1f} seconds. "
                f"This becomes the reference for all future laps."
            ),
            "audio_text":      f"Reference lap set: {lap_time:.1f} seconds",
            "ghost_data": {
                "lap":           lap_num,
                "lap_time":      lap_time,
                "best_lap_time": lap_time,
                "delta":         0.0,
                "corner_deltas": {},
                "worst_corner":  None,
                "worst_delta":   0.0,
            }
        }

    def _bucket_to_corner(self, bucket_m: float) -> int:
        """Maps a distance bucket to an approximate corner number (1–15)."""
        fraction   = bucket_m / self.track_length
        corner_num = int(fraction * 15) + 1
        return max(1, min(15, corner_num))


# Backwards-compatible alias: human-friendly display name
LapTimePredictor = GhostDelta
