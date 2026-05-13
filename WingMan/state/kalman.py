# state/kalman.py
# ─────────────────────────────────────────────────────────────────────────────
# BatterySOCEstimator — Person B (Task B2)
#
# SOC (State of Charge) is NOT directly available from OpenF1 or TORCS.
# We estimate it via a proxy derived from throttle/brake/DRS, then smooth
# the noisy proxy using a Kalman filter.
#
# Usage:
#   estimator = BatterySOCEstimator()
#   soc_est, soc_unc = estimator.update(state_vector)
#   state_vector["soc_estimated"] = soc_est
#   state_vector["soc_uncertainty"] = soc_unc
# ─────────────────────────────────────────────────────────────────────────────

from filterpy.kalman import KalmanFilter
import numpy as np


class BatterySOCEstimator:
    """
    Two-state Kalman filter for battery SOC estimation.

    State vector x = [soc, soc_change_rate]
    Measurement  z = [proxy_soc]  (derived from throttle / brake / DRS)
    """

    def __init__(self, initial_soc: float = 0.85):
        self._proxy_soc = initial_soc          # running proxy accumulator
        self._prev_timestamp: float | None = None

        # ── Kalman filter setup ────────────────────────────────────────────
        kf = KalmanFilter(dim_x=2, dim_z=1)

        # Initial state: [soc, soc_change_rate]
        kf.x = np.array([[initial_soc], [0.0]])

        # State transition: soc(t+1) = soc(t) + rate(t),  rate stays same
        kf.F = np.array([[1.0, 1.0],
                         [0.0, 1.0]])

        # Observation: we measure soc directly
        kf.H = np.array([[1.0, 0.0]])

        # Measurement noise (proxy_soc is noisy)
        kf.R = np.array([[0.01]])

        # Process noise (small — SOC changes slowly)
        kf.Q = np.array([[0.001, 0.0],
                         [0.0,   0.001]])

        # Initial uncertainty
        kf.P = np.array([[1.0, 0.0],
                         [0.0, 1.0]])

        self.kf = kf

    # ── Proxy SOC ────────────────────────────────────────────────────────────

    def _update_proxy(self, state: dict) -> float:
        """
        Derive a raw SOC estimate from car telemetry.

        Rules (per tick ~250 ms):
          - Heavy throttle + no braking  → deplete  -0.003
          - Braking                       → regen    +0.002
          - DRS open                      → slight drain -0.001
          - Always                        → baseline drain -0.0005
          - TORCS source: use fuel-based proxy directly if available
        """
        # If TORCS provides fuel level, use it directly as the proxy
        if state.get("data_source") == "torcs" and state.get("soc_raw", 0.0) > 0.0:
            self._proxy_soc = state["soc_raw"]
            return self._proxy_soc

        # Otherwise derive from telemetry
        throttle = state.get("throttle", 0.0)
        brake    = state.get("brake",    False)
        drs      = state.get("drs",      False)

        if throttle > 0.8 and not brake:
            self._proxy_soc -= 0.003
        if brake:
            self._proxy_soc += 0.002
        if drs:
            self._proxy_soc -= 0.001

        # Baseline drain every tick
        self._proxy_soc -= 0.0005

        # Clamp to [0, 1]
        self._proxy_soc = max(0.0, min(1.0, self._proxy_soc))
        return self._proxy_soc

    # ── Main update ──────────────────────────────────────────────────────────

    def update(self, state: dict) -> tuple[float, float]:
        """
        Call once per telemetry tick.

        Args:
            state: current state vector dict (read-only here)

        Returns:
            (soc_estimated, soc_uncertainty)
            Also writes soc_estimated / soc_uncertainty into state in-place.
        """
        proxy = self._update_proxy(state)

        self.kf.predict()
        self.kf.update([[proxy]])

        soc_estimated  = float(np.clip(self.kf.x[0][0], 0.0, 1.0))
        soc_uncertainty = float(self.kf.P[0][0])

        # Write back into state vector
        state["soc_raw"]         = proxy
        state["soc_estimated"]   = soc_estimated
        state["soc_uncertainty"] = soc_uncertainty

        return soc_estimated, soc_uncertainty

    def reset(self, soc: float = 0.85):
        """Reset estimator — use when switching sessions or drivers."""
        self._proxy_soc = soc
        self._prev_timestamp = None
        self.kf.x = np.array([[soc], [0.0]])
        self.kf.P = np.array([[1.0, 0.0], [0.0, 1.0]])


# ── Quick standalone test ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import time

    print("BatterySOCEstimator — noise smoothing test")
    print("─" * 50)

    estimator = BatterySOCEstimator(initial_soc=0.85)
    rng = __import__("random").Random(42)

    raw_vals, filtered_vals = [], []

    for i in range(100):
        # Simulate varying throttle with added noise
        throttle = 0.9 if i % 3 != 0 else 0.2
        brake    = (i % 7 == 0)
        noise    = rng.gauss(0, 0.02)

        state = {
            "throttle":    throttle,
            "brake":       brake,
            "drs":         False,
            "data_source": "mock",
            "soc_raw":     0.0,
        }

        soc_est, soc_unc = estimator.update(state)
        raw_vals.append(state["soc_raw"])
        filtered_vals.append(soc_est)

        if i % 10 == 0:
            print(f"Tick {i:3d}  proxy={state['soc_raw']:.4f}  "
                  f"filtered={soc_est:.4f}  uncertainty={soc_unc:.5f}")

    print("\nFinal SOC:", round(filtered_vals[-1], 4))
    print("Raw range:     ", round(min(raw_vals), 4), "→", round(max(raw_vals), 4))
    print("Filtered range:", round(min(filtered_vals), 4), "→", round(max(filtered_vals), 4))
    print("✓  Filtered range should be narrower than raw range (smoother)")

    # Optional: plot if matplotlib available
    try:
        import matplotlib.pyplot as plt
        plt.figure(figsize=(12, 4))
        plt.plot(raw_vals,      label="Raw proxy SOC",      alpha=0.5, linewidth=1)
        plt.plot(filtered_vals, label="Kalman filtered SOC", linewidth=2)
        plt.axhline(0.25, color="red",    linestyle="--", label="Danger threshold")
        plt.axhline(0.60, color="orange", linestyle="--", label="Recharge target")
        plt.legend()
        plt.title("Kalman SOC Filter — Raw vs Filtered")
        plt.xlabel("Tick")
        plt.ylabel("SOC")
        plt.tight_layout()
        plt.savefig("kalman_test.png")
        print("Plot saved to kalman_test.png")
    except ImportError:
        print("(matplotlib not available — skipping plot)")
