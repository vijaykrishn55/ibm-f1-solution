import asyncio
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)

@dataclass
class RadioEvent:
    driver: str
    timestamp: float
    recording_url: str
    session_key: str

@dataclass
class TranscriptEvent:
    driver: str
    timestamp: float
    transcript: str
    audio_path: Optional[str]
    transcription_method: str
    confidence: float

class AudioDownloader:
    async def download(self, url: str) -> str:
        # In mock mode, this is skipped. In real mode, it would download.
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
        temp_file.close()
        return temp_file.name

class Transcriber:
    """
    Transcribes radio audio clips to text.

    Priority order:
      1. IBM Docling  (install: pip install docling)
      2. OpenAI Whisper  (install: pip install openai-whisper)
      3. Stub fallback — returns fixed mock text so pipeline never crashes

    Docling is the IBM hackathon-preferred tool. If neither is installed
    the pipeline still runs in mock/stub mode.
    """

    def __init__(self):
        # Detect available transcription backend at startup
        self._backend = self._detect_backend()

    @staticmethod
    def _detect_backend() -> str:
        try:
            from docling.document_converter import DocumentConverter  # noqa: F401
            return "docling"
        except ImportError:
            pass
        try:
            import whisper  # noqa: F401
            return "whisper"
        except ImportError:
            pass
        return "stub"

    async def transcribe(self, audio_path: str) -> tuple[str, str, float]:
        """Returns (transcript_text, method_name, confidence_0_to_1)."""
        loop = asyncio.get_event_loop()
        if self._backend == "docling":
            return await loop.run_in_executor(None, self._docling_transcribe, audio_path)
        elif self._backend == "whisper":
            return await loop.run_in_executor(None, self._whisper_transcribe, audio_path)
        else:
            return "mock transcription", "stub", 0.5

    @staticmethod
    def _docling_transcribe(audio_path: str) -> tuple[str, str, float]:
        try:
            from docling.document_converter import DocumentConverter
            converter = DocumentConverter()
            result = converter.convert(audio_path)
            text = result.document.export_to_markdown().strip()
            return text or "no transcription", "docling", 0.90
        except Exception as e:
            logger.warning(f"[Transcriber] Docling failed: {e} — using stub")
            return "mock transcription", "stub", 0.5

    @staticmethod
    def _whisper_transcribe(audio_path: str) -> tuple[str, str, float]:
        try:
            import whisper
            model = whisper.load_model("base")
            result = model.transcribe(audio_path)
            return result.get("text", "").strip(), "whisper", 0.80
        except Exception as e:
            logger.warning(f"[Transcriber] Whisper failed: {e} — using stub")
            return "mock transcription", "stub", 0.5


class RadioPoller:
    def __init__(self, session_key: str, driver_number: str, mock_mode: bool = False):
        self.session_key = session_key
        self.driver_number = driver_number
        self.mock_mode = mock_mode
        self.seen_urls = set()
        self._mock_data = []
        if self.mock_mode:
            self._load_mock_data()

    def _load_mock_data(self):
        try:
            # Assuming WingMan is current working directory or using absolute path based on project structure
            fixture_path = os.path.join(os.path.dirname(__file__), "..", "..", "tests", "fixtures", "team_radio.json")
            with open(fixture_path, 'r') as f:
                self._mock_data = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load mock data: {e}")

    async def poll(self):
        if self.mock_mode:
            if self._mock_data:
                item = self._mock_data.pop(0)
                if item["recording_url"] not in self.seen_urls:
                    self.seen_urls.add(item["recording_url"])
                    # Parse timestamp from ISO date if possible, or just use current time
                    try:
                        dt = datetime.fromisoformat(item["date"])
                        timestamp = dt.timestamp()
                    except:
                        timestamp = asyncio.get_event_loop().time()
                    
                    event = RadioEvent(
                        driver=item["driver_number"],
                        timestamp=timestamp,
                        recording_url=item["recording_url"],
                        session_key=self.session_key
                    )
                    return event, item.get("_mock_transcript")
            return None, None
        else:
            # Stub for real OpenF1 polling
            return None, None

class RadioIngestPipeline:
    def __init__(self, session_key: str, driver_number: str, out_queue: asyncio.Queue, mock_mode: bool = True):
        self.poller = RadioPoller(session_key, driver_number, mock_mode)
        self.downloader = AudioDownloader()
        self.transcriber = Transcriber()
        self.out_queue = out_queue
        self.mock_mode = mock_mode

    async def run(self):
        while True:
            event, mock_transcript = await self.poller.poll()
            if event:
                if self.mock_mode and mock_transcript:
                    transcript_event = TranscriptEvent(
                        driver=event.driver,
                        timestamp=event.timestamp,
                        transcript=mock_transcript,
                        audio_path=None,
                        transcription_method="mock",
                        confidence=1.0
                    )
                else:
                    audio_path = await self.downloader.download(event.recording_url)
                    transcript, method, confidence = await self.transcriber.transcribe(audio_path)
                    transcript_event = TranscriptEvent(
                        driver=event.driver,
                        timestamp=event.timestamp,
                        transcript=transcript,
                        audio_path=audio_path,
                        transcription_method=method,
                        confidence=confidence
                    )
                await self.out_queue.put(transcript_event)
            
            # In mock mode, if we exhaust the mock data, we could just break or sleep
            if self.mock_mode and not self.poller._mock_data:
                break
                
            await asyncio.sleep(30 if not self.mock_mode else 0.1)

async def main():
    q = asyncio.Queue()
    pipeline = RadioIngestPipeline("latest", "1", q, mock_mode=True)
    asyncio.create_task(pipeline.run())
    
    while True:
        try:
            event = await asyncio.wait_for(q.get(), timeout=1.0)
            print(event)
        except asyncio.TimeoutError:
            break

if __name__ == "__main__":
    asyncio.run(main())
