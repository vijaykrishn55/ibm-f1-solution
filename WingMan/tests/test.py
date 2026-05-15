import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# from slow_path.mpc_planner import plan_5_corners

# corners = [
#     {'corner_id': 4,  'net_lift_value': 0.08},
#     {'corner_id': 10, 'net_lift_value': 0.15},
#     {'corner_id': 11, 'net_lift_value': 0.03},
#     {'corner_id': 14, 'net_lift_value': 0.11},
#     {'corner_id': 1,  'net_lift_value': -0.02},
# ]

# plan = plan_5_corners(soc_now=0.45, corners=corners)

# print("Plan:", plan)
# print("All good!")

# from output.alert_builder import build_payload

# alert = {
#     'alert_id': 'abc123',
#     'rule': 'soc_danger_alert',
#     'recommendation': 'Recharge immediately — boost zone in 2 corners',
#     'reason': 'SOC at 22%',
#     'priority': 9,
#     'confidence': 0.81,
#     'source_module': 'voltedge'
# }

# state = {
#     'soc_estimated': 0.22,
#     'corner_id': 9,
#     'lap': 31,
#     'timestamp': 1234567890.0,
#     'data_source': 'mock',
#     'brake': False
# }

# payload = build_payload(alert, state)
# print(payload)
# print('All good!')


# text to audio check : 

import asyncio
from output.tts import speak

state = {'brake': False}
asyncio.run(speak('Recharge now... Turn eleven', state))
print('Audio test done')

# the braking skip:
# import asyncio
# from output.tts import speak

# state = {'brake': True}
# asyncio.run(speak('This should not play', state))
# print('Braking skip test done — no audio should have played')


# file check : 
# from slow_path.context_forge import ContextForge
# from slow_path.event_queue import EventQueue
# from slow_path.mpc_planner import plan_5_corners
# from slow_path.granite_client import GraniteClient
# from output.alert_builder import build_payload
# from output.tts import speak

# print("All Person C imports OK")