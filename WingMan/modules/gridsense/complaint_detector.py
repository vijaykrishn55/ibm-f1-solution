import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

COMPLAINT_KEYWORDS = {
    "understeer":        ["pushing", "understeering", "going wide", "turning in late", "front end", "front grip", "washes out", "not turning", "off the front", "front is gone"],
    "oversteer":         ["rear", "loose", "sliding", "spinning up", "rear is gone", "oversteering", "step out", "snap", "unstable", "nervous"],
    "vibration":         ["vibration", "shaking", "flat spot", "shake", "judder", "wobble", "blistering", "tyre shaking"],
    "energy":            ["battery", "power", "engine", "harvesting", "deployment", "energy", "no power", "power loss", "not harvesting", "power deficit", "ers", "mgu-k"],
    "tyre_overheating":  ["tyres going off", "no grip", "sliding everywhere", "degrading fast", "overheating", "blistering", "tyres are dead", "gone off", "tyres gone", "graining"],
    "visibility":        ["can't see", "visor", "sun", "dirty visor", "tear off", "can't look", "blinded", "sunlight"],
}

NEGATION_KEYWORDS = ["not", "no", "never", "fixed", "better", "improved", "okay now", "ok now", "gone away", "no longer"]

@dataclass
class ComplaintResult:
    driver: str
    timestamp: float
    transcript: str
    complaint_detected: bool
    complaint_type: str
    confidence: float
    matched_keywords: List[str] = field(default_factory=list)
    negated: bool = False
    lap_mentioned: Optional[int] = None

class ComplaintDetector:
    def __init__(self, state_vector: dict = None, context_forge=None):
        self.state_vector = state_vector if state_vector is not None else {}
        self.context_forge = context_forge

    def _write_state_vector(self, result: ComplaintResult):
        self.state_vector["radio_transcript"] = result.transcript
        self.state_vector["complaint_detected"] = result.complaint_detected
        self.state_vector["complaint_type"] = result.complaint_type

    def _write_context_forge(self, result: ComplaintResult):
        if hasattr(self, 'context_forge') and self.context_forge:
            if hasattr(self.context_forge, 'add_alert'):
                self.context_forge.add_alert({
                    "source": "gridsense",
                    "type": "radio_complaint",
                    "driver": result.driver,
                    "timestamp": result.timestamp,
                    "complaint_type": result.complaint_type,
                    "confidence": result.confidence,
                    "transcript": result.transcript,
                })

    def _extract_lap(self, transcript: str) -> Optional[int]:
        match = re.search(r'\blap\s+(\d+)\b', transcript, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None

    def _check_negation(self, transcript: str, keyword: str) -> bool:
        transcript_lower = transcript.lower()
        keyword_lower = keyword.lower()
        
        # Find index of keyword in transcript
        keyword_idx = transcript_lower.find(keyword_lower)
        if keyword_idx == -1:
            return False
            
        # Get the text before the keyword
        text_before = transcript_lower[:keyword_idx].strip()
        words_before = text_before.split()
        
        # Look at the last 5 words before the keyword
        recent_words_before = words_before[-5:] if len(words_before) >= 5 else words_before
        
        # Combine back to string to handle multi-word negations like "okay now"
        recent_text = " ".join(recent_words_before)
        
        for neg in NEGATION_KEYWORDS:
            if neg in recent_text:
                # Basic check, just seeing if the string exists in the prior 5 words
                # For exact word match, we might want regex, but simple inclusion works for now
                if re.search(r'\b' + re.escape(neg) + r'\b', recent_text):
                    return True
        return False

    def process_transcript(self, event) -> ComplaintResult:
        transcript = event.transcript.lower()
        
        matched_keywords = []
        complaint_type = "none"
        negated = False
        
        # Find matches
        all_matches = []
        for c_type, keywords in COMPLAINT_KEYWORDS.items():
            for keyword in keywords:
                # Use regex with word boundaries to avoid matching "ers" in "understeering"
                if re.search(r'\b' + re.escape(keyword) + r'\b', transcript):
                    all_matches.append((c_type, keyword))

        # Check for negations
        valid_matches = []
        negated_matches = []
        for c_type, keyword in all_matches:
            if self._check_negation(transcript, keyword):
                negated_matches.append((c_type, keyword))
            else:
                valid_matches.append((c_type, keyword))

        # Determine complaint type based on valid matches
        if valid_matches:
            complaint_detected = True
            negated = False
            
            type_counts = {}
            for c_type, keyword in valid_matches:
                type_counts[c_type] = type_counts.get(c_type, 0) + 1
                if keyword not in matched_keywords:
                    matched_keywords.append(keyword)
            
            # Add negated ones to matched_keywords too just in case they were expected
            for c_type, keyword in negated_matches:
                if keyword not in matched_keywords:
                    matched_keywords.append(keyword)
            
            # Sort by count descending
            sorted_types = sorted(type_counts.items(), key=lambda x: x[1], reverse=True)
            complaint_type = sorted_types[0][0]
            
            num_matches = len(valid_matches)
            confidence = 1.0 if num_matches >= 2 else 0.7
        else:
            complaint_detected = False
            confidence = 0.0
            if negated_matches:
                negated = True
                complaint_type = "none"
                for c_type, keyword in negated_matches:
                    if keyword not in matched_keywords:
                        matched_keywords.append(keyword)
            else:
                negated = False
                complaint_type = "none"

        lap = self._extract_lap(transcript)

        result = ComplaintResult(
            driver=event.driver,
            timestamp=event.timestamp,
            transcript=event.transcript,
            complaint_detected=complaint_detected,
            complaint_type=complaint_type if complaint_detected else ("none" if not negated else "none"),
            confidence=confidence,
            matched_keywords=matched_keywords,
            negated=negated,
            lap_mentioned=lap
        )

        self._write_state_vector(result)

        if result.complaint_detected:
            self._write_context_forge(result)

        return result

    async def run(self, in_queue: asyncio.Queue, out_queue: asyncio.Queue):
        while True:
            event = await in_queue.get()
            result = self.process_transcript(event)
            await out_queue.put(result)
            in_queue.task_done()

if __name__ == "__main__":
    from modules.gridsense.radio_ingest import TranscriptEvent
    detector = ComplaintDetector()
    event = TranscriptEvent(
        driver="1",
        timestamp=0.0,
        transcript="I'm pushing in Turn 3, going wide, understeering a lot",
        audio_path=None,
        transcription_method="mock",
        confidence=1.0
    )
    res = detector.process_transcript(event)
    print(res)
