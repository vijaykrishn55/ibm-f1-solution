"""Alert Builder: packages fast path alert + state into a broadcast payload."""

import time


def build_payload(alert: dict, state: dict) -> dict:
    """
    Combines an alert dict (from rules engine or GridSense)
    with the current state vector into a WebSocket-ready payload.
    """
    return {
        "alert_id":       alert.get("alert_id", ""),
        "rule":           alert.get("rule", "unknown"),
        "recommendation": alert.get("recommendation", "Maintain current mode"),
        "reason":         alert.get("reason", ""),
        "priority":       alert.get("priority", 1),
        "confidence":     alert.get("confidence", 0.5),
        "soc_estimated":  state.get("soc_estimated", 0.0),
        "corner_id":      state.get("corner_id", 0),
        "lap":            state.get("lap", 0),
        "timestamp":      state.get("timestamp", time.time()),
        "fan_explanation": alert.get("fan_explanation", ""),
        "data_source":    state.get("data_source", "unknown"),
        "source_module":  alert.get("source_module", "voltedge"),
        "brake":          state.get("brake", False)
    }