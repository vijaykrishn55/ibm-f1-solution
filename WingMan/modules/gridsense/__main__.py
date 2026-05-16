import asyncio
import logging

from modules.gridsense.radio_ingest import RadioIngestPipeline
from modules.gridsense.complaint_detector import ComplaintDetector
from modules.gridsense.correlator import Correlator
from modules.gridsense.gridsense_rules import GridSenseRules

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gridsense_main")

class MockSessionState:
    def __init__(self):
        self.latest = {
            "throttle": 0.2, # < 0.3 for understeer correlation
            "speed": 50.0,   # < 100
            "brake": False,
            "soc_estimated": 0.3, # < 0.35 for energy rule 2
            "historical_speed_avg": 80.0
        }

class MockContextForge:
    def add_alert(self, alert):
        logger.info(f"[ContextForge] Alert added: {alert['rule']}")

async def main():
    logger.info("Starting GridSense Pipeline in Mock Mode...")

    # Shared state
    session_state = MockSessionState()
    context_forge = MockContextForge()

    # Queues
    ingest_queue = asyncio.Queue()
    complaint_queue = asyncio.Queue()
    correlator_queue = asyncio.Queue()

    # Components
    ingest_pipeline = RadioIngestPipeline("latest", "1", ingest_queue, mock_mode=True)
    detector = ComplaintDetector(session_state.latest)
    correlator = Correlator(session_state)
    rules = GridSenseRules(session_state, context_forge)

    # Start tasks
    tasks = [
        asyncio.create_task(ingest_pipeline.run()),
        asyncio.create_task(detector.run(ingest_queue, complaint_queue)),
        asyncio.create_task(correlator.run(complaint_queue, correlator_queue)),
        asyncio.create_task(rules.run(correlator_queue))
    ]

    # Let the mock pipeline run for a bit to process the mock items
    await asyncio.sleep(2.0)
    
    # Cancel tasks
    for t in tasks:
        t.cancel()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
