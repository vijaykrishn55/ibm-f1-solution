"""
tests/mock_complaints.py — Task E1 (Person E)

Hand-crafted mock complaint dicts for solo testing of GridSense rules
and the correlator. No radio, no audio, no Docling required.

All 6 complaint types from the plan are covered:
  understeer, oversteer, vibration (brake_vibration),
  energy (energy_complaint), tyre_overheating, visibility
"""

import time

# ── Base TranscriptEvent-like dict ────────────────────────────────────────────
# Matches the fields the ComplaintDetector's process_transcript() reads.
# Use these with the RadioIngestPipeline mock or directly in unit tests.

_BASE_EVENT = {
    "driver": "VER",
    "timestamp": time.time(),
    "transcript": "",
    "audio_path": None,
    "transcription_method": "mock",
    "confidence": 1.0,
}

def _evt(transcript: str, driver: str = "VER") -> dict:
    """Helper: build a mock TranscriptEvent-like dict."""
    return {**_BASE_EVENT, "driver": driver, "transcript": transcript}


# ── Complaint mock events ─────────────────────────────────────────────────────

UNDERSTEER_EVENT = _evt(
    "The car is pushing a lot on entry at Turn 3 — going wide, front end won't turn"
)

OVERSTEER_EVENT = _evt(
    "I'm getting rear instability on the fast lefts — the rear is gone on exit"
)

BRAKE_VIBRATION_EVENT = _evt(
    "There is a strong vibration under braking into Turn 1, feels like a flat spot"
)

TYRE_OVERHEATING_EVENT = _evt(
    "The tyres are gone, sliding everywhere — no grip at all, degrading fast"
)

ENERGY_COMPLAINT_EVENT = _evt(
    "I have no power coming out of the hairpin — deployment is wrong, battery issue"
)

VISIBILITY_EVENT = _evt(
    "I can't see anything, the visor is completely dirty, need a tear off"
)

NO_COMPLAINT_EVENT = _evt(
    "All good, the car feels good, I'm comfortable with the balance"
)

# ── Pre-built complaint result dicts (output of ComplaintDetector) ─────────────
# These match the schema that gridsense_rules.GridSenseRules.evaluate() expects
# as input via the Correlator's CorrelationResult.

UNDERSTEER_COMPLAINT = {
    "complaint_type": "understeer",
    "transcript": UNDERSTEER_EVENT["transcript"],
    "engineering_action": "Increase front wing angle or adjust brake bias forward",
    "priority": 8,
    "confidence": 0.75,
}

OVERSTEER_COMPLAINT = {
    "complaint_type": "oversteer",
    "transcript": OVERSTEER_EVENT["transcript"],
    "engineering_action": "Reduce rear wing load or adjust differential",
    "priority": 8,
    "confidence": 0.75,
}

BRAKE_VIBRATION_COMPLAINT = {
    "complaint_type": "vibration",
    "transcript": BRAKE_VIBRATION_EVENT["transcript"],
    "engineering_action": "Check brake disc temperature — may need balance adjustment",
    "priority": 7,
    "confidence": 0.75,
}

TYRE_OVERHEATING_COMPLAINT = {
    "complaint_type": "tyre_overheating",
    "transcript": TYRE_OVERHEATING_EVENT["transcript"],
    "engineering_action": "Consider early pit window — tyre delta likely increasing",
    "priority": 9,
    "confidence": 0.75,
}

ENERGY_COMPLAINT = {
    "complaint_type": "energy",
    "transcript": ENERGY_COMPLAINT_EVENT["transcript"],
    "engineering_action": "Cross-check with VoltEdge SOC — may need deployment mode change",
    "priority": 9,
    "confidence": 0.75,
}

VISIBILITY_COMPLAINT = {
    "complaint_type": "visibility",
    "transcript": VISIBILITY_EVENT["transcript"],
    "engineering_action": "Note for next pit stop — tear-off or visor strip",
    "priority": 4,
    "confidence": 0.75,
}

NO_COMPLAINT = None

# ── All complaints list (for loop-based tests) ────────────────────────────────
ALL_COMPLAINTS = [
    UNDERSTEER_COMPLAINT,
    OVERSTEER_COMPLAINT,
    BRAKE_VIBRATION_COMPLAINT,
    TYRE_OVERHEATING_COMPLAINT,
    ENERGY_COMPLAINT,
    VISIBILITY_COMPLAINT,
]

ALL_EVENTS = [
    UNDERSTEER_EVENT,
    OVERSTEER_EVENT,
    BRAKE_VIBRATION_EVENT,
    TYRE_OVERHEATING_EVENT,
    ENERGY_COMPLAINT_EVENT,
    VISIBILITY_EVENT,
    NO_COMPLAINT_EVENT,
]


# ── Quick smoke test ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

    from modules.gridsense.complaint_detector import ComplaintDetector
    from modules.gridsense.radio_ingest import TranscriptEvent

    detector = ComplaintDetector()
    print("Mock Complaint Smoke Test")
    print("=" * 60)

    for ev_dict in ALL_EVENTS:
        event = TranscriptEvent(
            driver=ev_dict["driver"],
            timestamp=ev_dict["timestamp"],
            transcript=ev_dict["transcript"],
            audio_path=None,
            transcription_method="mock",
            confidence=1.0,
        )
        result = detector.process_transcript(event)
        status = "✅" if result.complaint_detected else "⭕"
        print(f"{status}  [{result.complaint_type:20s}] conf={result.confidence:.2f}  "
              f"keywords={result.matched_keywords[:3]}")
        print(f"     → \"{ev_dict['transcript'][:70]}...\"")
        print()
