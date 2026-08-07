# ─────────────────────────────────────────────────────────────────────────────
#  core/voice/engines.py – Chooses local-GPU or hosted speech engines
#
#  WHY THIS EXISTS: core/stt.py imports torch and faster_whisper at module
#  level, core/tts.py imports kokoro and sounddevice, and core/wake_word.py
#  opens a PyAudio stream. On a headless Ubuntu box with no GPU and no audio
#  devices, those imports are somewhere between pointless and fatal — PortAudio
#  with no ALSA configuration can fail or block at import time, and
#  os.add_dll_directory does not exist on Linux at all.
#
#  So the local engines must not be imported unless they are actually selected.
#  Everything here is deliberately a LAZY import inside a function body, which
#  is the only thing that achieves that. Do not hoist these to module level.
#
#  Every engine returned honours the interface core/assistant.py already calls,
#  unchanged:
#      STT : load_model()   listen_and_transcribe(initial_silence_timeout, *, hotwords) -> str
#      TTS : initialize()   speak(text) -> bool   request_stop()   shutdown()
#      Wake: start()        stop()
#
#  That is the whole point. assistant.py holds ~2,800 lines of memory/plan/email
#  intercept logic and ~50 speak sites; the migration swaps what gets built, not
#  how it is called.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import os
import sys
import threading

from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import config  # noqa: E402

PROVIDER_LOCAL = "local"


def stt_is_local() -> bool:
    return config.STT_PROVIDER == PROVIDER_LOCAL


def tts_is_local() -> bool:
    return config.TTS_PROVIDER == PROVIDER_LOCAL


# ── Hosted engines ───────────────────────────────────────────────────────────
# These hold the provider adapter plus a reference to the browser audio session
# that supplies microphone frames and consumes synthesised audio. The session is
# attached at connect time and cleared on disconnect, so "no session" is the
# normal idle state, not an error.


class HostedSTTEngine:
    """
    Speech-to-text over a browser microphone.

    Returns "" when no browser is attached. That is deliberate and correct
    rather than a swallowed error: "" is exactly what core/stt.py returns for
    silence, and assistant.py's ~50 call sites already read it as "nothing was
    said", which ends the turn cleanly. With no browser connected there IS no
    speaker in the room, so silence is the honest answer. Raising here would
    take down the conversation thread instead.
    """

    def __init__(self) -> None:
        from core.voice.stt_provider import get_recognizer

        self._recognizer = get_recognizer()
        self._session = None
        self._lock = threading.Lock()
        self._warned = False

    def attach_session(self, session) -> None:
        with self._lock:
            self._session = session
            self._warned = False

    def detach_session(self) -> None:
        with self._lock:
            self._session = None

    @property
    def session(self):
        with self._lock:
            return self._session

    def load_model(self) -> None:
        """No model to load — the provider is remote. Verifies credentials."""
        try:
            self._recognizer.test_connection()
            logger.success(
                f"Speech-to-text ready via {config.STT_PROVIDER} (hosted, no GPU needed)."
            )
        except RuntimeError as exc:
            # Not fatal at startup: the assistant should still boot so the
            # dashboard and email polling work, and so the operator can see the
            # error rather than a process that died.
            logger.error(f"Speech-to-text provider is not usable: {exc}")

    def listen_and_transcribe(
        self,
        initial_silence_timeout: float = 2.0,
        *,
        hotwords: str | None = None,
    ) -> str:
        session = self.session
        if session is None:
            if not self._warned:
                logger.warning(
                    "listen_and_transcribe called with no browser audio session "
                    "attached — returning silence."
                )
                self._warned = True
            return ""
        return session.listen_and_transcribe(
            initial_silence_timeout=initial_silence_timeout, hotwords=hotwords
        )


class HostedTTSEngine:
    """
    Text-to-speech streamed to a browser.

    speak() returns True if the utterance played to completion and False if it
    was cut short — the same contract core/tts.py has, because that return
    drives note_interrupted_reply() in assistant.py.
    """

    def __init__(self) -> None:
        from core.voice.tts_provider import get_synthesizer

        self._synth = get_synthesizer()
        self._session = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

    def attach_session(self, session) -> None:
        with self._lock:
            self._session = session

    def detach_session(self) -> None:
        with self._lock:
            self._session = None

    @property
    def session(self):
        with self._lock:
            return self._session

    def initialize(self) -> None:
        try:
            self._synth.test_connection()
            logger.success(
                f"Text-to-speech ready via {config.TTS_PROVIDER} (hosted, no GPU needed)."
            )
        except RuntimeError as exc:
            logger.error(f"Text-to-speech provider is not usable: {exc}")

    def speak(self, text: str) -> bool:
        if not text or not text.strip():
            return True
        session = self.session
        if session is None:
            # Nobody is listening. Report "completed" rather than "interrupted":
            # an interrupted reply gets tagged in the LLM history, and nothing
            # was interrupted — there was simply no audience.
            logger.debug("speak() with no audio session attached — dropping audio.")
            return True
        self._stop_event.clear()
        return session.speak(text, self._synth, self._stop_event)

    def request_stop(self) -> None:
        self._stop_event.set()
        session = self.session
        if session is not None:
            session.flush_playback()

    def shutdown(self) -> None:
        self._stop_event.set()
        self.detach_session()


class NullWakeDetector:
    """
    Stands in for the wake word when turns start from a browser gesture.

    core/assistant.py starts and stops a detector at several points in the
    conversation lifecycle, and a second one around every utterance for
    barge-in. Rather than thread a conditional through all of that, this
    satisfies the interface and does nothing.

    Browser barge-in does not need this: the microphone stays open during
    playback and speech onset is detected on the incoming frames, so any speech
    interrupts — not just the wake phrase.
    """

    def __init__(self, *args, **kwargs) -> None:
        self._callback = kwargs.get("callback") or (args[0] if args else None)

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None


# ── Factories ────────────────────────────────────────────────────────────────


def build_stt_engine():
    """Local Whisper if STT_PROVIDER=local, otherwise the hosted engine."""
    if stt_is_local():
        # Lazy: importing this pulls torch + faster_whisper + the Windows CUDA
        # DLL shim. None of that may run on the server.
        from core.stt import STTEngine

        logger.info("Speech-to-text: local faster-whisper.")
        return STTEngine(
            model_size=config.WHISPER_MODEL_SIZE,
            device=config.WHISPER_DEVICE,
            compute_type=config.WHISPER_COMPUTE_TYPE,
            language=config.WHISPER_LANGUAGE,
            mic_device_index=config.MIC_DEVICE_INDEX,
        )
    return HostedSTTEngine()


def build_tts_engine():
    """Local Kokoro if TTS_PROVIDER=local, otherwise the hosted engine."""
    if tts_is_local():
        # Lazy: importing this pulls kokoro + torch + sounddevice, and
        # sounddevice binds PortAudio, which has no devices on a headless box.
        from core.tts import TTSEngine

        logger.info("Text-to-speech: local Kokoro.")
        return TTSEngine(voice=config.TTS_VOICE, speed=config.TTS_SPEED)
    return HostedTTSEngine()


def build_wake_detector(**kwargs):
    """
    Real openWakeWord detector only when the microphone is local.

    With the microphone in a browser there are only bad options: ship the model
    to the tab, or stream room audio to the server 24/7. Continuous audio from a
    clinic leaving the building is not defensible for a medical product, so v1
    starts turns from an explicit gesture instead.
    """
    if stt_is_local():
        # Lazy: importing this pulls openwakeword + onnxruntime + pyaudio.
        from core.wake_word import WakeWordDetector

        return WakeWordDetector(**kwargs)
    return NullWakeDetector(**kwargs)
