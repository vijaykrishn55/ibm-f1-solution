"""Context Forge: session memory manager.
Stores lap summaries, alerts, Granite outputs, and threshold update history.
Auto-saves every 5 laps to persist_path if set.
"""

import json
import time
import os


class ContextForge:
    def __init__(self, persist_path=None, circuit="", session_type="race", driver=""):
        self.persist_path = persist_path
        self.data = {
            "circuit": circuit,
            "session_type": session_type,
            "driver": driver,
            "created_at": time.time(),
            "laps": [],
            "alerts_fired": [],
            "granite_outputs": [],
            "threshold_updates": []
        }

    # --- Write methods ---

    def add_lap_summary(self, lap_data: dict):
        required = ["lap", "avg_soc", "alerts_this_lap", "key_decision"]
        for field in required:
            if field not in lap_data:
                raise ValueError(f"add_lap_summary: missing required field '{field}'")
        lap_data["recorded_at"] = time.time()
        self.data["laps"].append(lap_data)
        if len(self.data["laps"]) % 5 == 0:
            self.save()

    def add_alert(self, alert: dict):
        alert["stored_at"] = time.time()
        self.data["alerts_fired"].append(alert)

    def add_granite_output(self, output: dict):
        output["stored_at"] = time.time()
        self.data["granite_outputs"].append(output)

    def add_threshold_update(self, update: dict):
        update["applied_at"] = time.time()
        self.data["threshold_updates"].append(update)

    # --- Read methods ---

    def get_last_n_laps(self, n: int) -> list:
        return self.data["laps"][-n:]

    def get_lap(self, lap_number: int) -> dict | None:
        for lap in self.data["laps"]:
            if lap["lap"] == lap_number:
                return lap
        return None

    def get_alerts_for_lap(self, lap_number: int) -> list:
        return [a for a in self.data["alerts_fired"] if a.get("lap") == lap_number]

    def total_laps_completed(self) -> int:
        return len(self.data["laps"])

    def total_alerts_fired(self) -> int:
        return len(self.data["alerts_fired"])

    # --- Persistence ---

    def save(self):
        if not self.persist_path:
            return
        os.makedirs(os.path.dirname(self.persist_path), exist_ok=True)
        with open(self.persist_path, "w") as f:
            json.dump(self.data, f, indent=2)

    def load(self):
        if not self.persist_path:
            return
        try:
            with open(self.persist_path) as f:
                self.data = json.load(f)
        except Exception:
            pass

    def reset(self):
        """Clear memory between sessions."""
        self.data["laps"] = []
        self.data["alerts_fired"] = []
        self.data["granite_outputs"] = []
        self.data["threshold_updates"] = []