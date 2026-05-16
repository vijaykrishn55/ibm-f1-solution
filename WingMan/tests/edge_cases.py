"""Edge case scenarios for Day 3 testing.

Person A owns:
  - Edge Case 2: Data gap (mock server stops, data_age_ms rises, safe_default fires)
  - Edge Case 5: TORCS disconnect (TORCS drops, stale marker, safe_default fires)

This module provides runnable edge case simulations that can be triggered
from the command line or imported by the integration test harness.
"""

import os
import sys
import time
import copy
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from state.schema import new_state, validate_state
from tests.mock_state_vectors import NORMAL, STALE_DATA


# -- Edge Case 2: Data Gap Simulation --

def simulate_data_gap(gap_seconds: float = 3.0, recovery_ticks: int = 2):
    """
    Simulate a data gap of `gap_seconds` and verify recovery.

    Produces a sequence of state vectors:
      1. Normal ticks (data_age_ms < 250)
      2. Gap period (no new data, data_age_ms climbs)
      3. Recovery ticks (data_age_ms drops back)

    Returns (states, gap_detected, recovery_tick) for assertions.
    """
    print(f"[EdgeCase2] Simulating {gap_seconds}s data gap ...")

    states = []
    gap_detected_at = None
    recovered_at    = None

    # Phase 1: 5 normal ticks
    for i in range(5):
        s = new_state(
            data_age_ms=80 + (i * 10),
            data_source="openf1",
            speed=280.0,
            throttle=0.88,
            soc_estimated=0.55,
            lap=30,
            corner_id=(i % 15) + 1,
        )
        states.append(("normal", s))

    # Phase 2: gap -- data_age_ms climbs over gap_seconds
    gap_ms = int(gap_seconds * 1000)
    for step in range(1, 4):
        age = int(step * (gap_ms / 3))
        s = new_state(
            data_age_ms=age,
            data_source="openf1",
            speed=0.0,     # no fresh reading
            throttle=0.0,
            soc_estimated=0.55,
            lap=30,
        )
        states.append(("gap", s))
        if age > 2000 and gap_detected_at is None:
            gap_detected_at = len(states) - 1

    # Phase 3: recovery -- each tick brings data_age_ms lower
    recovery_ages = [200, 50]  # tick 1: partial, tick 2: fully recovered
    for tick in range(recovery_ticks):
        age = recovery_ages[tick] if tick < len(recovery_ages) else 50
        s = new_state(
            data_age_ms=age,
            data_source="openf1",
            speed=275.0,
            throttle=0.85,
            soc_estimated=0.54,
            lap=30,
        )
        states.append(("recovery", s))
        if age < 250 and recovered_at is None:
            recovered_at = len(states) - 1

    print(f"[EdgeCase2] Generated {len(states)} states")
    print(f"[EdgeCase2] Gap detected at tick: {gap_detected_at}")
    print(f"[EdgeCase2] Recovered at tick:     {recovered_at}")

    return states, gap_detected_at, recovered_at


# -- Edge Case 5: TORCS Disconnect Simulation --

def simulate_torcs_disconnect(disconnect_after_ticks: int = 8):
    """
    Simulate TORCS running normally then disconnecting.

    Returns a list of (phase, state) tuples:
      - "live":  normal TORCS states
      - "stale": stale marker states after disconnect
      - "reconnected": fresh states after reconnection

    The fast path should fire safe_default during the "stale" phase.
    """
    print(f"[EdgeCase5] Simulating TORCS disconnect after {disconnect_after_ticks} ticks ...")

    states = []

    # Phase 1: TORCS live
    for i in range(disconnect_after_ticks):
        fuel = 80.0 - (i * 0.5)
        s = new_state(
            data_source="torcs",
            data_age_ms=0,
            speed=180.0 + (i * 5),
            throttle=0.7 + (i * 0.02),
            soc_raw=round(fuel / 94.0, 3),
            soc_estimated=0.0,
            lap=1 + (i // 4),
            corner_id=(i % 15) + 1,
        )
        states.append(("live", s))

    # Phase 2: TORCS disconnected -- stale markers
    last_read = time.time()
    for gap_step in range(4):
        age_ms = 500 + (gap_step * 1500)  # 500, 2000, 3500, 5000
        s = new_state(
            data_source="torcs",
            data_age_ms=age_ms,
            soc_estimated=0.0,
        )
        states.append(("stale", s))

    # Phase 3: TORCS reconnected
    for i in range(3):
        s = new_state(
            data_source="torcs",
            data_age_ms=0,
            speed=170.0,
            throttle=0.65,
            soc_raw=round(70.0 / 94.0, 3),
            soc_estimated=0.0,
            lap=3,
            corner_id=5,
        )
        states.append(("reconnected", s))

    print(f"[EdgeCase5] Generated {len(states)} states")
    live_count  = sum(1 for p, _ in states if p == "live")
    stale_count = sum(1 for p, _ in states if p == "stale")
    recon_count = sum(1 for p, _ in states if p == "reconnected")
    print(f"[EdgeCase5] Live: {live_count}, Stale: {stale_count}, Reconnected: {recon_count}")

    return states


# -- Self-test --

def test_edge_cases():
    """Run both edge case simulations and validate outputs."""

    # Edge Case 2
    states2, gap_at, rec_at = simulate_data_gap(3.0)
    assert gap_at is not None, "Gap should be detected"
    assert rec_at is not None, "Recovery should happen"
    assert rec_at - gap_at <= 4, "Recovery should happen within 4 ticks"
    for phase, s in states2:
        w = validate_state(s)
        assert not w, f"Phase '{phase}' has validation warnings: {w}"
    print("[EdgeCase2] PASS")

    # Edge Case 5
    states5 = simulate_torcs_disconnect(8)
    stale_states = [(p, s) for p, s in states5 if p == "stale"]
    assert len(stale_states) >= 2, "Need at least 2 stale markers"
    for _, s in stale_states:
        assert s["data_age_ms"] > 0
        assert s["data_source"] == "torcs"
    for phase, s in states5:
        w = validate_state(s)
        assert not w, f"Phase '{phase}' has validation warnings: {w}"
    print("[EdgeCase5] PASS")

    print("\nAll edge cases passed.")


if __name__ == "__main__":
    test_edge_cases()
