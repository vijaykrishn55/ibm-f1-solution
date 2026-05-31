import asyncio
import logging
import uuid
from typing import Optional

try:
    from output.websocket_server import broadcast
except ImportError:
    # Handle gracefully if not found
    async def broadcast(alert):
        pass

# The prompt says: "from slow_path.context_forge import add_alert"
# but context_forge.py has a ContextForge class with add_alert method.
# We will try to import it, and use if available, or use a provided instance.
try:
    from slow_path.context_forge import add_alert as _add_alert_fn
except ImportError:
    _add_alert_fn = None

from modules.gridsense.correlator import CorrelationResult

logger = logging.getLogger(__name__)

class GridSenseRules:
    def __init__(self, session_state=None, context_forge=None):
        self.session_state = session_state
        self.context_forge = context_forge

    def evaluate(self, result: CorrelationResult) -> Optional[dict]:
        soc = 1.0
        try:
            from state.session_state import session_state
            throttle = session_state.get("throttle", 0.5)
            speed = session_state.get("speed", 200)
            brake = session_state.get("brake", False)
            soc = session_state.get("soc_estimated", 0.5)
        except Exception as e:
            # Fallback to mock dictionary so demo can still run
            if self.session_state and hasattr(self.session_state, 'latest') and self.session_state.latest:
                state_vector = self.session_state.latest
            elif isinstance(self.session_state, dict):
                state_vector = self.session_state
            else:
                state_vector = {}
            soc = state_vector.get("soc_estimated", 1.0)
            throttle = state_vector.get("throttle", 0.5)
            speed = state_vector.get("speed", 200)
            brake = state_vector.get("brake", False)

        alert = None
        # Rule 2: radio_energy_confirmed
        if result.complaint_type == "energy" and soc < 0.35:
            priority = 10 if soc < 0.4 else 9   # Plan: elevate if SOC < 0.4
            confidence = result.confidence if soc < 0.6 else 0.5   # Plan: reduce if SOC > 0.6 (anomaly)
            alert = {
                "alert_id": str(uuid.uuid4()),
                "timestamp": result.timestamp,
                "driver": result.driver,
                "rule": "radio_energy_confirmed",
                "recommendation": f"Driver energy complaint confirmed (SOC {soc:.0%}) — recharge window recommended",
                "reason": f"Driver energy complaint confirmed (SOC {soc:.0%}) — recharge window recommended",
                "confidence": confidence,
                "priority": priority,
                "source_module": "gridsense",
                "fan_explanation": ""
            }
        # Rule 1: radio_corroborated_setup
        elif result.correlation_confirmed and result.confidence > 0.7:
            alert = {
                "alert_id": str(uuid.uuid4()),
                "timestamp": result.timestamp,
                "driver": result.driver,
                "rule": "radio_corroborated_setup",
                "recommendation": f"Driver complaint matches telemetry — {result.suggested_setup_adjustment}",
                "reason": f"Driver complaint matches telemetry — {result.suggested_setup_adjustment}",
                "confidence": result.confidence,
                "priority": 8,
                "source_module": "gridsense",
                "fan_explanation": ""
            }
        # Rule 3: radio_no_correlation
        elif not result.correlation_confirmed:
            alert = {
                "alert_id": str(uuid.uuid4()),
                "timestamp": result.timestamp,
                "driver": result.driver,
                "rule": "radio_no_correlation",
                "recommendation": "Driver complaint noted — no telemetry confirmation yet",
                "reason": "Driver complaint noted — no telemetry confirmation yet",
                "confidence": result.confidence,
                "priority": 4,
                "source_module": "gridsense",
                "fan_explanation": ""
            }

        if alert:
            try:
                from output.websocket_server import broadcast
                import asyncio
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(broadcast(alert))
                except RuntimeError:
                    pass
            except Exception as e:
                pass
            
            # Requested terminal output
            print(f"[gridsense] Alert broadcast: {alert['rule']} confidence={alert['confidence']}")

        return alert

    async def run(self, in_queue: asyncio.Queue):
        while True:
            result = await in_queue.get()
            try:
                alert = self.evaluate(result)
                if alert:
                    # Broadcast
                    await broadcast(alert)
                    
                    # Add to context forge if available
                    if self.context_forge and hasattr(self.context_forge, 'add_alert'):
                        try:
                            self.context_forge.add_alert(alert)
                        except Exception as e:
                            logger.error(f"Error adding alert to context_forge: {e}")
                    elif _add_alert_fn:
                        try:
                            _add_alert_fn(alert)
                        except Exception as e:
                            logger.error(f"Error calling add_alert fn: {e}")
            except Exception as e:
                logger.error(f"Error evaluating rule: {e}")
            finally:
                in_queue.task_done()
