import asyncio
import pytest
from modules.gridsense.complaint_detector import ComplaintDetector, ComplaintResult
from modules.gridsense.radio_ingest import TranscriptEvent

class TestComplaintDetector:
    def setup_method(self):
        self.detector = ComplaintDetector()

    def _make_event(self, transcript: str):
        return TranscriptEvent(
            driver="1",
            timestamp=100.0,
            transcript=transcript,
            audio_path=None,
            transcription_method="mock",
            confidence=1.0
        )

    def test_understeer_detected(self):
        event = self._make_event("I'm pushing in Turn 3, going wide")
        result = self.detector.process_transcript(event)
        assert result.complaint_detected is True
        assert result.complaint_type == "understeer"

    def test_understeer_single_keyword(self):
        event = self._make_event("The front end is poor")
        result = self.detector.process_transcript(event)
        assert result.complaint_detected is True
        assert result.complaint_type == "understeer"
        assert result.confidence == 0.7

    def test_understeer_two_keywords_high_confidence(self):
        event = self._make_event("I am pushing a lot and going wide")
        result = self.detector.process_transcript(event)
        assert result.complaint_detected is True
        assert result.complaint_type == "understeer"
        assert result.confidence == 1.0

    def test_oversteer_detected(self):
        event = self._make_event("The rear is very loose")
        result = self.detector.process_transcript(event)
        assert result.complaint_detected is True
        assert result.complaint_type == "oversteer"

    def test_oversteer_sliding(self):
        event = self._make_event("car is sliding everywhere")
        result = self.detector.process_transcript(event)
        assert result.complaint_detected is True
        assert result.complaint_type == "oversteer"

    def test_vibration_detected(self):
        event = self._make_event("Getting some vibration on the straight")
        result = self.detector.process_transcript(event)
        assert result.complaint_detected is True
        assert result.complaint_type == "vibration"

    def test_flat_spot_is_vibration(self):
        event = self._make_event("I think I have a flat spot")
        result = self.detector.process_transcript(event)
        assert result.complaint_detected is True
        assert result.complaint_type == "vibration"

    def test_energy_detected(self):
        event = self._make_event("Battery not harvesting")
        result = self.detector.process_transcript(event)
        assert result.complaint_detected is True
        assert result.complaint_type == "energy"

    def test_energy_no_power(self):
        event = self._make_event("I have no power out of the corner")
        result = self.detector.process_transcript(event)
        assert result.complaint_detected is True
        assert result.complaint_type == "energy"

    def test_negation_no_understeer(self):
        event = self._make_event("I have no understeer anymore")
        result = self.detector.process_transcript(event)
        # Note: 'understeering' is the keyword. If we test "understeer" as keyword...
        # Wait, the prompt says 'understeering' but my test says 'understeer'. Let me check keywords:
        # "understeering" is in COMPLAINT_KEYWORDS. So "no understeering anymore".
        # Let's adjust the test string to match the keyword.
        event = self._make_event("I have no understeering anymore")
        result = self.detector.process_transcript(event)
        assert result.complaint_detected is False
        assert result.negated is True

    def test_negation_not_pushing(self):
        event = self._make_event("Car is not pushing at all")
        result = self.detector.process_transcript(event)
        assert result.complaint_detected is False
        assert result.negated is True

    def test_negation_fixed(self):
        event = self._make_event("It is fixed, no sliding")
        result = self.detector.process_transcript(event)
        assert result.complaint_detected is False
        assert result.negated is True

    def test_negation_after_keyword_not_negated(self):
        event = self._make_event("I am understeering badly, it's not good")
        result = self.detector.process_transcript(event)
        assert result.complaint_detected is True
        assert result.negated is False

    def test_no_complaint_empty(self):
        event = self._make_event("")
        result = self.detector.process_transcript(event)
        assert result.complaint_detected is False

    def test_no_complaint_generic(self):
        event = self._make_event("Box this lap")
        result = self.detector.process_transcript(event)
        assert result.complaint_detected is False

    def test_no_complaint_positive(self):
        event = self._make_event("The car feels great, balance is perfect")
        result = self.detector.process_transcript(event)
        assert result.complaint_detected is False

    def test_lap_extracted(self):
        event = self._make_event("I have understeering on lap 31")
        result = self.detector.process_transcript(event)
        assert result.lap_mentioned == 31

    def test_lap_extracted_different_format(self):
        event = self._make_event("Lap 18 was terrible")
        result = self.detector.process_transcript(event)
        assert result.lap_mentioned == 18

    def test_no_lap_mentioned(self):
        event = self._make_event("No lap here")
        result = self.detector.process_transcript(event)
        assert result.lap_mentioned is None

    def test_result_has_all_fields(self):
        event = self._make_event("pushing")
        result = self.detector.process_transcript(event)
        assert hasattr(result, 'driver')
        assert hasattr(result, 'timestamp')
        assert hasattr(result, 'transcript')
        assert hasattr(result, 'complaint_detected')
        assert hasattr(result, 'complaint_type')
        assert hasattr(result, 'confidence')
        assert hasattr(result, 'matched_keywords')
        assert hasattr(result, 'negated')
        assert hasattr(result, 'lap_mentioned')

    def test_matched_keywords_populated_on_hit(self):
        event = self._make_event("pushing and sliding")
        result = self.detector.process_transcript(event)
        assert "pushing" in result.matched_keywords

    def test_matched_keywords_empty_on_miss(self):
        event = self._make_event("all good")
        result = self.detector.process_transcript(event)
        assert len(result.matched_keywords) == 0

class TestMockPipeline:
    @pytest.mark.asyncio
    async def test_pipeline(self):
        detector = ComplaintDetector()
        in_queue = asyncio.Queue()
        out_queue = asyncio.Queue()
        
        # Inject 3 TranscriptEvent objects
        events = [
            TranscriptEvent("1", 100.0, "I am pushing a lot", None, "mock", 1.0),
            TranscriptEvent("1", 101.0, "fixed the sliding", None, "mock", 1.0),
            TranscriptEvent("1", 102.0, "no power", None, "mock", 1.0)
        ]
        
        for e in events:
            await in_queue.put(e)
            
        task = asyncio.create_task(detector.run(in_queue, out_queue))
        
        # We expect 3 results
        results = []
        for _ in range(3):
            res = await asyncio.wait_for(out_queue.get(), timeout=1.0)
            results.append(res)
            out_queue.task_done()
            
        task.cancel()
        
        # Assertions
        assert results[0].complaint_detected is True
        assert results[0].complaint_type == "understeer"
        
        assert results[1].complaint_detected is False
        assert results[1].negated is True
        
        assert results[2].complaint_detected is True
        assert results[2].complaint_type == "energy"

def test_day1_checkpoint():
    detector = ComplaintDetector()
    event = TranscriptEvent(
        driver="1",
        timestamp=123.0,
        transcript="I'm pushing in Turn 3, going wide, understeering a lot",
        audio_path=None,
        transcription_method="mock",
        confidence=1.0
    )
    result = detector.process_transcript(event)
    assert result.complaint_detected is True
    assert result.complaint_type == "understeer"
    assert result.confidence >= 0.7