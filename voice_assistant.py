"""Offline text-to-speech (pyttsx3 / Windows SAPI5) with a PowerShell fallback.

Speech runs on its own worker thread so the UI and camera never freeze. A fresh engine is
created for every utterance, which avoids the well-known pyttsx3 'runAndWait hangs on the
second call' problem.
"""
from __future__ import annotations

import logging
import os
import queue
import subprocess
import threading
from typing import Callable, Optional

log = logging.getLogger("assistant.voice")

ResultCallback = Callable[[bool, str], None]


class VoiceAssistant:
    def __init__(self, rate: int = 170, volume: float = 1.0, voice_name: str = "",
                 on_result: Optional[ResultCallback] = None):
        self.rate, self.volume, self.voice_name = rate, volume, voice_name
        self.on_result = on_result
        self._q: queue.Queue = queue.Queue()
        self._closed = False
        self._busy = threading.Event()
        self._thread = threading.Thread(target=self._worker, name="voice", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------ public API
    def speak(self, text: str) -> None:
        """Queue text to be spoken. Returns immediately."""
        if not self._closed:
            self._q.put(text)

    def is_busy(self) -> bool:
        return self._busy.is_set() or not self._q.empty()

    def wait_until_done(self, timeout: float = 15.0) -> None:
        end = threading.Event()
        t = threading.Timer(timeout, end.set)
        t.start()
        try:
            while self.is_busy() and not end.is_set():
                end.wait(0.1)
        finally:
            t.cancel()

    def shutdown(self, wait: float = 5.0) -> None:
        self._closed = True
        self._q.put(None)
        self._thread.join(timeout=wait)

    # ------------------------------------------------------------ internals
    def _worker(self) -> None:
        com = None
        try:                                    # COM must be initialised per thread on Windows
            import pythoncom  # type: ignore
            pythoncom.CoInitialize()
            com = pythoncom
        except Exception:
            pass
        try:
            while True:
                text = self._q.get()
                if text is None:
                    break
                self._busy.set()
                try:
                    ok, msg = self._say(text)
                finally:
                    self._busy.clear()
                if self.on_result:
                    try:
                        self.on_result(ok, msg)
                    except Exception:
                        log.exception("voice callback failed")
        finally:
            if com is not None:
                try:
                    com.CoUninitialize()
                except Exception:
                    pass

    def _say(self, text: str) -> tuple[bool, str]:
        err1 = ""
        try:
            self._say_pyttsx3(text)
            return True, "Spoken"
        except Exception as exc:                # pyttsx3 raises many different types
            err1 = f"{type(exc).__name__}: {exc}"
            log.warning("pyttsx3 failed (%s) - trying PowerShell fallback", err1)
        if os.name == "nt":
            try:
                self._say_powershell(text)
                return True, "Spoken (fallback voice engine)"
            except Exception as exc:
                log.error("PowerShell speech failed: %s", exc)
        return False, ("Audio unavailable: no working speech engine or audio output device. "
                       "Check that speakers are enabled and a Windows voice is installed "
                       f"(Settings > Time & language > Speech). Details: {err1}")

    def _say_pyttsx3(self, text: str) -> None:
        import pyttsx3
        engine = pyttsx3.init()
        try:
            engine.setProperty("rate", self.rate)
            engine.setProperty("volume", self.volume)
            voice_id = self._pick_voice(engine)
            if voice_id:
                engine.setProperty("voice", voice_id)
            engine.say(text)
            engine.runAndWait()
        finally:
            try:
                engine.stop()
            except Exception:
                pass

    def _pick_voice(self, engine) -> Optional[str]:
        try:
            voices = engine.getProperty("voices") or []
        except Exception:
            return None
        if self.voice_name:
            for v in voices:
                if self.voice_name.lower() in (v.name or "").lower():
                    return v.id
        for v in voices:                         # prefer an English voice
            blob = f"{v.name} {v.id} {getattr(v, 'languages', '')}".lower()
            if "en-us" in blob or "english" in blob or "en_us" in blob:
                return v.id
        return voices[0].id if voices else None

    def _say_powershell(self, text: str) -> None:
        safe = text.replace("'", "''")
        script = ("Add-Type -AssemblyName System.Speech; "
                  "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                  f"$s.Rate = 0; $s.Speak('{safe}')")
        flags = 0x08000000  # CREATE_NO_WINDOW
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       check=True, timeout=30, creationflags=flags,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
