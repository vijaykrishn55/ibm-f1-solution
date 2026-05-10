"""Context Forge: session memory manager (placeholder)
Stores lap summaries, alerts history, and strategic notes.
"""

import json

class ContextForge:
    def __init__(self, persist_path=None):
        self.persist_path = persist_path
        self.data = {'laps': [], 'alerts': [], 'notes': []}

    def add_lap_summary(self, summary):
        self.data['laps'].append(summary)

    def add_alert(self, alert):
        self.data['alerts'].append(alert)

    def save(self):
        if not self.persist_path:
            return
        with open(self.persist_path, 'w') as f:
            json.dump(self.data, f)

    def load(self):
        if not self.persist_path:
            return
        try:
            with open(self.persist_path) as f:
                self.data = json.load(f)
        except Exception:
            pass
