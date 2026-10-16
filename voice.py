"""Voice wake-word listener using Vosk offline STT."""

from __future__ import annotations

import collections
import json
import os
import threading
import time
from typing import Any


def start_voice_listener(
    flag: Any,
    wake_phrase: str,
    stop_phrase: str,
    stop_event: threading.Event,
) -> threading.Thread | None:
    """Vosk wake-word thread; toggles `flag`. Returns the thread (or None)."""

    def _worker() -> None:
        try:
            import sounddevice as sd
            from vosk import KaldiRecognizer, Model
        except ImportError as exc:
            print(f"[voice] speech stack unavailable ({exc}) — "
                  "voice gating disabled, gestures always active.")
            return
        if not os.path.isdir(VOSK_MODEL_DIR):
            print(f"[voice] model folder '{VOSK_MODEL_DIR}' not found — "
                  f"download it from https://alphacephei.com/vosk/models and unzip "
                  f"it there. Voice gating disabled, gestures always active.")
            return
        stream = None
        try:
            model = Model(VOSK_MODEL_DIR)
            recognizer = KaldiRecognizer(model, 16000)
            audio_queue: collections.deque[bytes] = collections.deque(maxlen=10)

            def audio_callback(indata: Any, frames: int, time_info: Any, status: Any) -> None:
                if status:
                    print(f"[voice] audio callback status: {status}")
                # indata is a cffi buffer, convert to bytes
                audio_queue.append(bytes(indata))

            stream = sd.RawInputStream(
                samplerate=16000,
                blocksize=4000,
                dtype="int16",
                channels=1,
                callback=audio_callback,
            )
            stream.start()
        except Exception as exc:
            print(f"[voice] mic/model init failed ({exc}) — "
                  "voice gating disabled, gestures always active.")
            return

        print(f"[voice] listening for '{wake_phrase}' / '{stop_phrase}'")
        try:
            while not stop_event.is_set():
                try:
                    payload = audio_queue.popleft()
                except IndexError:
                    time.sleep(0.01)
                    continue

                heard = ""
                if recognizer.AcceptWaveform(payload):
                    heard = json.loads(recognizer.Result()).get("text", "")
                else:
                    heard = json.loads(recognizer.PartialResult()).get("text", "")
                if not heard:
                    continue
                if wake_phrase in heard and not flag.get():
                    flag.set(True)
                    print("Voice control: ON")
                elif stop_phrase in heard and flag.get():
                    flag.set(False)
                    print("Voice control: OFF")
        except Exception as exc:
            print(f"[voice] listener crashed ({exc!r}) — gestures stay active.")
        finally:
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass

    thread = threading.Thread(target=_worker, name="voice-listener", daemon=True)
    thread.start()
    return thread


VOSK_MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vosk-model-en-us-0.22-lgraph")
