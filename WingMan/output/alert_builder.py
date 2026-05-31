"""Alert Builder: packages fast path alert + state into a broadcast payload."""

import time


def build_payload(alert: dict, state: dict) -> dict:
    """
    Combines an alert dict (from rules engine or GridSense)
    with the current state vector into a WebSocket-ready payload.
    """
    src_raw = (alert.get("module") or alert.get("source_module", "voltedge") or "").lower()
    # Map legacy/internal module ids to canonical machine ids used by the UI
    _mapping = {
        "gridsense": "race_radio_intel",
        "ghostdelta": "lap_time_predictor",
        "tyrewhisperer": "tyre_health_monitor",
    }
    src_mapped = _mapping.get(src_raw, src_raw)
    return {
        "alert_id":       alert.get("alert_id", ""),
        "module":         src_mapped,
        "source_module":  src_raw,
        "rule":           alert.get("rule", "unknown"),
        "recommendation": alert.get("recommendation", "Maintain current mode"),
        "reason":         alert.get("reason", ""),
        "priority":       alert.get("priority", 1),
        "confidence":     alert.get("confidence", 0.5),
        "soc_estimated":  state.get("soc_estimated", 0.0),
        "corner_id":      state.get("corner_id", 0),
        "lap":            state.get("lap", 0),
        "speed":          state.get("speed", 0.0),
        "throttle":       state.get("throttle", 0.0),
        "brake":          state.get("brake", False),
        "timestamp":      state.get("timestamp", time.time()),
        "fan_explanation": alert.get("fan_explanation", ""),
        "ghost_data":     alert.get("ghost_data", {}),
        "data_source":    state.get("data_source", "unknown"),
    }