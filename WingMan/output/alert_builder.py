"""Build alert payloads from recommendations"""

import uuid, time

def build_alert(driver, rec, confidence, corner, reason, fan_explanation=''):
    return {
        'alert_id': str(uuid.uuid4()),
        'timestamp': time.time(),
        'driver': driver,
        'type': 'info',
        'recommendation': rec,
        'reason': reason,
        'confidence': confidence,
        'corner': corner,
        'corners_ahead': 0,
        'fan_explanation': fan_explanation,
        'audio_text': rec,
        'module': 'wingman'
    }
