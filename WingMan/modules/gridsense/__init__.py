# modules/gridsense/__init__.py
# Exposes GridSense — the facade run_torcs.py imports.
#   from modules.gridsense import GridSense
#
# GridSense.should_trigger(state, manual=False) → (bool, complaint_type, corner)
# GridSense.process(state, complaint_type, corner)  → alert dict | None

import asyncio
import time
import uuid

from modules.gridsense.shared_store      import store
from modules.gridsense.complaint_detector import ComplaintDetector, ComplaintResult
from modules.gridsense.correlator         import Correlator
from modules.gridsense.gridsense_rules    import GridSenseRules

# ── Auto-trigger tuning ──────────────────────────────────────────────────────
# TyreWhisperer sets store.asym_alarm = True when asymmetry is detected.
# GridSense auto-fires when that flag is set or when 'g' is pressed (manual).
_COOLDOWN_TICKS = 200  # minimum ticks between auto-triggers (~50 s at 4Hz)


class GridSense:
    """
    Facade that wires radio complaint detection into an alert compatible
    with the run_torcs.py broadcast pipeline.

    Usage (from run_torcs.py):
        gs = GridSense()
        trigger, ctype, corner = gs.should_trigger(state, manual=False)
        if trigger:
            asyncio.create_task(_run_gridsense(gs, state, ctype, corner))
    """

    def __init__(self):
        self._detector  = ComplaintDetector()
        self._correlator = Correlator()
        self._rules      = GridSenseRules()
        self._last_trigger_tick = -_COOLDOWN_TICKS
        self._tick = 0

    def should_trigger(self, state: dict, manual: bool = False) -> tuple[bool, str, int | None]:
        """
        Returns (should_fire, complaint_type, corner_id).
        Fires when:
          - manual=True (keyboard 'g' pressed), OR
          - TyreWhisperer set store.asym_alarm and cooldown elapsed
        """
        self._tick += 1
        corner = state.get("corner_id", None)

        # Manual trigger overrides everything
        if manual:
            self._last_trigger_tick = self._tick
            ctype = store.asym_alarm_side or "front_left"
            return True, ctype, corner

        # Auto-trigger from TyreWhisperer alarm
        if (store.asym_alarm
                and (self._tick - self._last_trigger_tick) >= _COOLDOWN_TICKS):
            store.asym_alarm = False  # consume the alarm
            self._last_trigger_tick = self._tick
            ctype = store.asym_alarm_side or "front_left"
            return True, ctype, corner

        return False, "", None

    async def process(self, state: dict,
                      complaint_type: str,
                      corner_hint: int | None = None) -> dict | None:
        """
        Runs complaint → correlate → rules and returns a broadcast-ready alert.
        complaint_type should match COMPLAINT_KEYWORDS keys or be a hint like
        'front_left' (mapped to 'understeer' internally).
        """
        # Map TyreWhisperer side → radio complaint type
        _side_map = {
            "front_left":  "understeer",
            "front_right": "oversteer",
        }
        mapped_type = _side_map.get(complaint_type, complaint_type)

        # Build a synthetic TranscriptEvent from the complaint type
        from modules.gridsense.radio_ingest import TranscriptEvent
        fake_transcript = _SYNTHETIC_TRANSCRIPTS.get(
            mapped_type,
            f"Driver reports {complaint_type.replace('_', ' ')} issue"
        )
        event = TranscriptEvent(
            driver    = state.get("driver", "TORCS_CAR_1"),
            timestamp = time.time(),
            transcript= fake_transcript,
            audio_path= None,
            transcription_method="auto",
            confidence=0.75,
        )

        result  = self._detector.process_transcript(event)
        if not result.complaint_detected:
            return None

        corr    = self._correlator.process_complaint(result)
        if corr is None:
            return None

        # Inject live SOC into the rules evaluator
        self._rules.session_state = state
        alert = self._rules.evaluate(corr)
        if alert is None:
            return None

        # Normalise keys so run_torcs.py broadcast works
        alert.setdefault("corner",          corner_hint or state.get("corner_id", 0))
        alert.setdefault("lap",             state.get("lap", 0))
        alert.setdefault("soc_estimated",   state.get("soc_estimated", 0.0))
        alert.setdefault("type",            "setup_recommendation")
        alert.setdefault("module",          "gridsense")
        alert.setdefault("audio_text",      alert.get("recommendation", "")[:80])
        alert.setdefault("fan_explanation", "")
        return alert


# Synthetic radio phrases per complaint type — used when GridSense fires from
# TyreWhisperer (no real radio clip available).
_SYNTHETIC_TRANSCRIPTS = {
    "understeer":       "The car is pushing a lot on entry, front end won't turn, going wide",
    "oversteer":        "Rear is very loose on exit, sliding and snapping, rear end gone",
    "vibration":        "Heavy vibration under braking, judder and flat spot feeling",
    "energy":           "No power on exit, something wrong with deployment, battery issue",
    "tyre_overheating": "Tyres are gone, no grip, sliding everywhere, degrading fast",
    "visibility":       "Can't see, visor is completely dirty, need a tear off",
}

# Backwards-compatible alias: expose a human-friendly class name for rollout
# RaceRadioIntelligence is the display name for GridSense
RaceRadioIntelligence = GridSense

__all__ = ["GridSense", "RaceRadioIntelligence"]
