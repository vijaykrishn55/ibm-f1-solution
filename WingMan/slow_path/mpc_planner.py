"""MPC Planner: optimal energy deployment over next 5 corners.
Runs every 5 seconds in background. Never in the fast path.
"""

import asyncio
from scipy.optimize import minimize


def plan_5_corners(soc_now: float, corners: list) -> dict:
    """
    Given current SOC and next 5 corners, return optimal lift fraction per corner.
    
    corners: list of dicts like:
        [{"corner_id": 4, "net_lift_value": 0.08}, ...]
    
    Returns:
        {corner_id: lift_fraction} e.g. {4: 0.3, 10: 0.6, ...}
    """
    if not corners:
        return {}

    n = len(corners)

    def objective(lift_fractions):
        # Minimise lift (maximise speed across corners)
        return sum(lift_fractions)

    def soc_constraint(lift_fractions):
        # SOC at end of window must be >= 0.25
        soc = soc_now
        for i, corner in enumerate(corners):
            if lift_fractions[i] > 0:
                soc += corner["net_lift_value"] * lift_fractions[i]
        return soc - 0.25

    bounds = [(0.0, 1.0)] * n
    x0 = [0.3] * n

    result = minimize(
        objective,
        x0=x0,
        method="SLSQP",
        bounds=bounds,
        constraints={"type": "ineq", "fun": soc_constraint}
    )

    return {
        corners[i]["corner_id"]: float(round(result.x[i], 2))
        for i in range(n)
    }


async def mpc_loop(get_state_fn, circuit_config: dict, interval_seconds: float = 5.0):
    """
    Background loop. Runs plan_5_corners every 5 seconds.
    get_state_fn: callable that returns the current state vector dict.
    Prints plan to terminal for now — Day 2 wires this to rules engine.
    """
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            state = get_state_fn()
            if state is None:
                continue

            soc_now = state.get("soc_estimated", 0.85)
            current_corner = state.get("corner_id", 1)

            # Build next 5 corners from circuit config
            all_corners = list(circuit_config.get("corner_thresholds", {}).keys())
            all_corners = sorted([int(c) for c in all_corners])

            # Pick next 5 after current corner (wrap around)
            idx = 0
            for i, c in enumerate(all_corners):
                if c >= current_corner:
                    idx = i
                    break

            next_5_ids = [all_corners[(idx + i) % len(all_corners)] for i in range(5)]
            next_5 = [
                {
                    "corner_id": cid,
                    "net_lift_value": circuit_config["corner_thresholds"].get(
                        str(cid), {}
                    ).get("net_lift_value", 0.0)
                }
                for cid in next_5_ids
            ]

            plan = plan_5_corners(soc_now, next_5)
            print(f"[MPC] SOC: {soc_now:.2f} | Plan: {plan}")

        except Exception as e:
            print(f"[MPC] Error: {e}")