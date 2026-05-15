"""TTS: text-to-audio for driver alerts.
Skips audio during braking zones. Never interrupts current speech.
"""

import asyncio
import pyttsx3
from gtts import gTTS


# Single engine instance — don't recreate per call
_engine = pyttsx3.init()
_engine.setProperty("rate", 160)   # Slightly slower than default, clearer


async def speak(text: str, state: dict):
    """
    Speak a recommendation aloud.
    Skips if driver is currently braking.
    """
    if state.get("brake") is True:
        print(f"[TTS] Skipped (braking): {text}")
        return

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _speak_sync, text)


def _speak_sync(text: str):
    _engine.say(text)
    _engine.runAndWait()


def pre_generate(text: str, filename: str):
    """
    Pre-generate high quality MP3 using gTTS.
    Use for demo audio — call offline, cache the file, play during demo.
    """
    tts = gTTS(text)
    tts.save(filename)
    print(f"[TTS] Saved: {filename}")