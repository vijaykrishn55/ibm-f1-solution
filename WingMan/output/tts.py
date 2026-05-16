"""TTS: text-to-audio for driver alerts.
Skips audio during braking zones. Never interrupts current speech.

Day 2 additions:
  - generate_demo_audio(): pre-generate MP3s for the 3 demo moments
  - Fallback to print-only mode if pyttsx3 is unavailable
  - Audio queue to prevent simultaneous speech
"""

import asyncio
import os
import sys

sys.path.insert(0, ".")

# Graceful pyttsx3 import -- fallback to print-only on failure
_engine = None
try:
    import pyttsx3
    _engine = pyttsx3.init()
    _engine.setProperty("rate", 160)   # Slightly slower than default, clearer
except Exception as e:
    print(f"[TTS] pyttsx3 init failed: {e} -- using print-only mode")


_speaking = False   # simple lock to prevent overlap


async def speak(text: str, state: dict):
    """
    Speak a recommendation aloud.
    Skips if driver is currently braking.
    Skips if already speaking (no overlap).
    """
    global _speaking

    if state.get("brake") is True:
        print(f"[TTS] Skipped (braking): {text}")
        return

    if _speaking:
        print(f"[TTS] Skipped (already speaking): {text}")
        return

    if _engine is None:
        print(f"[TTS] (print-only) {text}")
        return

    _speaking = True
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _speak_sync, text)
    finally:
        _speaking = False


def _speak_sync(text: str):
    """Synchronous speech -- runs in executor to not block event loop."""
    if _engine is None:
        return
    try:
        _engine.say(text)
        _engine.runAndWait()
    except Exception as e:
        print(f"[TTS] Speak error: {e}")


def pre_generate(text: str, filename: str):
    """
    Pre-generate high quality MP3 using gTTS.
    Use for demo audio -- call offline, cache the file, play during demo.
    """
    try:
        from gtts import gTTS
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        tts = gTTS(text)
        tts.save(filename)
        print(f"[TTS] Saved: {filename}")
    except ImportError:
        print(f"[TTS] gTTS not installed -- writing text fallback to {filename}.txt")
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        with open(filename + ".txt", "w") as f:
            f.write(text)
    except Exception as e:
        print(f"[TTS] pre_generate error: {e}")


def generate_demo_audio():
    """
    Day 3 -- Pre-generate audio for the 3 demo moments.

    Demo window: Bahrain 2024, Laps 28-38
    Moment 1 (Lap 31): SOC danger at boost zone
    Moment 2 (Lap 34): Optimal recharge window
    Moment 3 (Lap 37): Lift not worth it
    """
    demo_dir = os.path.join("demo_audio")
    os.makedirs(demo_dir, exist_ok=True)

    moments = [
        {
            "file": os.path.join(demo_dir, "moment1.mp3"),
            "text": "Recharge now. Turn eleven. Battery critical in boost zone.",
        },
        {
            "file": os.path.join(demo_dir, "moment2.mp3"),
            "text": "Good recharge opportunity. Lift at Turn ten exit. Net energy gain positive.",
        },
        {
            "file": os.path.join(demo_dir, "moment3.mp3"),
            "text": "Stay on throttle through Turn four. Lift not worth it. Aero cost exceeds energy gain.",
        },
    ]

    print("[TTS] Generating demo audio for 3 moments ...")
    for moment in moments:
        pre_generate(moment["text"], moment["file"])

    print(f"[TTS] Demo audio saved to {demo_dir}/")
    return moments


# -- Standalone test --

if __name__ == "__main__":
    generate_demo_audio()
    print("\n[TTS] Demo audio generation complete")