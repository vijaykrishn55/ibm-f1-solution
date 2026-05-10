"""MPC planner placeholder
Runs periodically and outputs a short horizon plan.
"""

def plan_next_5_corners(state, circuit_config):
    # TODO: implement MPC optimization
    return [{'corner': i, 'action': 'maintain'} for i in range(1,6)]
