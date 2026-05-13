import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from slow_path.context_forge import ContextForge

cf = ContextForge(persist_path='tests/fixtures/test_session.json', circuit='bahrain', driver='VER')

# Add fake laps
for i in range(1, 13):
    cf.add_lap_summary({'lap': i, 'avg_soc': round(0.85 - i*0.02, 2), 'alerts_this_lap': i%3, 'key_decision': 'safe_default'})

# Add fake alerts
cf.add_alert({'rule': 'soc_danger_alert', 'lap': 7, 'confidence': 0.81})
cf.add_alert({'rule': 'safety_car_recharge', 'lap': 11, 'confidence': 0.90})

# Check reads
print('Laps stored:', cf.total_laps_completed())
print('Alerts stored:', cf.total_alerts_fired())
print('Last 3 laps:', [l['lap'] for l in cf.get_last_n_laps(3)])
print('Lap 7 soc:', cf.get_lap(7)['avg_soc'])
print('Alerts on lap 7:', cf.get_alerts_for_lap(7))

# Check save
cf.save()
print('Saved to file: OK')

# Check reload
cf2 = ContextForge(persist_path='tests/fixtures/test_session.json')
cf2.load()
print('Reloaded laps:', cf2.total_laps_completed())
print('All good!')