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
        state_vector = {}
        if self.session_state and hasattr(self.session_state, 'latest') and self.session_state.latest:
            state_vector = self.session_state.latest
        elif isinstance(self.session_state, dict):
            state_vector = self.session_state

        soc_estimated = state_vector.get("soc_estimated", 1.0)
        
        alert = None
        # Rule 2: radio_energy_confirmed
        if result.complaint_type == "energy" and soc_estimated < 0.35:
            alert = {
                "alert_id": str(uuid.uuid4()),
                "timestamp": result.timestamp,
                "driver": result.driver,
                "rule": "radio_energy_confirmed",
                "recommendation": "Driver energy complaint confirmed — recharge window recommended",
                "reason": "Driver energy complaint confirmed — recharge window recommended",
                "confidence": result.confidence,
                "source": "gridsense",
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
                "source": "gridsense",
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
                "source": "gridsense",
                "fan_explanation": ""
            }

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
