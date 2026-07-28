# ─────────────────────────────────────────────────────────────────────────────
#  core/tts.py – Text-to-speech engine for Lana
#  Uses Kokoro-82M (local, GPU-accelerated) via the kokoro Python package.
#
#  Pipeline position:  Claude text  →  TTSEngine.speak()  →  Speaker
#
#  Why Kokoro instead of edge-tts:
#    edge-tts has ~1.7s unavoidable network latency (Microsoft server round-trip).
#    Kokoro runs fully locally on the RTX 5060 and generates audio in ~0.3s.
#
#  Flow:
#    1. initialize() — loads the Kokoro-82M model onto GPU (once at startup)
#    2. speak(text)  — runs inference → gets torch.Tensor audio at 24 kHz
#                    → plays directly via sounddevice (no temp file, no disk I/O)
#
#  Voice chosen: am_michael
#    Best-graded American English male voice in Kokoro-82M (B quality target,
#    H hours of training data, C+ overall grade). Neutral, professional tone
#    fitting for an AI assistant. Tied with am_fenrir and am_puck on metrics;
#    am_michael was chosen for its natural assistant-like vocal character.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import os
import sys
import threading
import time
from typing import Optional

# Ensure the project root is on sys.path so `import config` works when this
# file is run directly (e.g. `python core/tts.py`).
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import numpy as np
import sounddevice as sd
import torch
from kokoro import KPipeline
from loguru import logger

import config

# ── Constants ─────────────────────────────────────────────────────────────────

# Kokoro-82M always outputs at 24 kHz
KOKORO_SAMPLE_RATE: int = 24_000


class TTSEngine:
    """
    Converts text to speech using Kokoro-82M and plays it via sounddevice.

    Call initialize() once at startup to load the model onto GPU.
    Then call speak(text) for each utterance.

    Usage:
        engine = TTSEngine()
        engine.initialize()
        engine.speak("Good morning. How can I help you?")
        engine.shutdown()
    """

    def __init__(
        self,
        voice: str = config.TTS_VOICE,
        speed: float = config.TTS_SPEED,
    ) -> None:
        """
        Args:
            voice: Kokoro voice ID (e.g. 'am_michael').
            speed: Speech rate multiplier (1.0 = normal, 1.1 = slightly faster).
        """
        self._voice = voice
        self._speed = speed
        self._pipeline: Optional[KPipeline] = None
        self._active_device: Optional[str] = None
        # Set by request_stop() (barge-in) to cut an in-flight utterance short.
        # Only the speaking thread ever calls sd.stop(); another thread setting
        # this and touching sounddevice would race the playback poll below.
        self._stop_event: threading.Event = threading.Event()
        self._playing: bool = False

    # ── Public API ────────────────────────────────────────────────────────────

    def initialize(self) -> None:
        """
        Load the Kokoro-82M model onto GPU (or CPU as fallback).
        Call this once at startup — model weights are downloaded automatically
        from Hugging Face on the first run and cached locally after that.
        """
        # Pick the device. config.TTS_DEVICE forces the choice; "auto" (the
        # default) uses the GPU when torch sees one. Forcing "cpu" is the
        # escape hatch for a machine where loading Kokoro onto CUDA crashes
        # torch natively — an uncatchable crash the except-based fallback below
        # cannot rescue, because the process is already gone.
        if config.TTS_DEVICE == "cpu":
            target_device = "cpu"
        elif config.TTS_DEVICE == "cuda":
            target_device = "cuda"
        else:  # "auto" (or anything unrecognised — fail toward the old behaviour)
            target_device = "cuda" if torch.cuda.is_available() else "cpu"

        logger.info(
            f"Loading Kokoro-82M on {target_device.upper()} "
            f"(voice='{self._voice}', speed={self._speed})…"
        )
        t0 = time.monotonic()

        try:
            # lang_code='a' = American English
            self._pipeline = KPipeline(lang_code="a", device=target_device)
            self._active_device = target_device
            logger.success(
                f"Kokoro-82M ready on {self._active_device.upper()} "
                f"in {time.monotonic() - t0:.1f}s."
            )

        except Exception as exc:
            if target_device != "cpu":
                logger.warning(
                    f"GPU load failed ({exc}). Falling back to CPU…"
                )
                self._pipeline = KPipeline(lang_code="a", device="cpu")
                self._active_device = "cpu"
                logger.success(
                    f"Kokoro-82M ready on CPU (fallback) "
                    f"in {time.monotonic() - t0:.1f}s."
                )
            else:
                logger.error(f"Failed to load Kokoro-82M: {exc}")
                raise

    def speak(self, text: str) -> bool:
        """
        Synthesise `text` to speech and play it through the default speaker.
        Blocks until playback is complete OR until request_stop() is called
        from another thread (barge-in).

        Args:
            text: Plain text to speak. Avoid markdown or special symbols.

        Returns:
            True if the whole utterance played to the end; False if it was
            interrupted (barge-in) or a synthesis/playback error occurred.
        """
        if not text or not text.strip():
            logger.warning("speak() called with empty text — skipping.")
            return True  # nothing to interrupt

        if self._pipeline is None:
            raise RuntimeError(
                "TTSEngine not initialized. Call initialize() first."
            )

        # A stop request left over from a previous utterance must not cancel
        # this one — clear it at entry, before any audio is produced.
        self._stop_event.clear()

        logger.info(f"Speaking: '{text[:80]}{'…' if len(text) > 80 else ''}'")
        t_start = time.monotonic()

        try:
            # ── Inference ─────────────────────────────────────────────────────
            # KPipeline.__call__ returns a generator of Result dataclasses.
            # Each Result.audio is a torch.Tensor (float32, 24 kHz).
            # For most short utterances there is only one segment.
            segments: list[np.ndarray] = []

            for result in self._pipeline(
                text,
                voice=self._voice,
                speed=self._speed,
            ):
                if self._stop_event.is_set():
                    logger.info("TTS interrupted during synthesis.")
                    return False
                if result.audio is not None:
                    # Move to CPU and convert to float32 numpy for sounddevice
                    segments.append(result.audio.cpu().numpy().astype(np.float32))

            if not segments:
                logger.warning("Kokoro produced no audio for the given text.")
                return True

            audio = np.concatenate(segments) if len(segments) > 1 else segments[0]

            t_generated = time.monotonic()
            duration_s = len(audio) / KOKORO_SAMPLE_RATE
            logger.debug(
                f"Inference: {t_generated - t_start:.3f}s → "
                f"{duration_s:.1f}s of audio "
                f"({self._active_device.upper()})"
            )

            # ── Playback ──────────────────────────────────────────────────────
            # sd.wait() can't be interrupted, so poll for either completion or a
            # barge-in stop request. Only THIS thread ever calls sd.stop().
            self._playing = True
            try:
                sd.play(audio, samplerate=KOKORO_SAMPLE_RATE)
                while True:
                    if self._stop_event.is_set():
                        sd.stop()
                        logger.info("TTS interrupted during playback (barge-in).")
                        return False
                    stream = sd.get_stream()
                    if stream is None or not stream.active:
                        break  # playback finished naturally
                    time.sleep(0.05)
            finally:
                self._playing = False

            logger.debug(f"Total speak() latency: {t_generated - t_start:.3f}s")
            return True

        except Exception as exc:
            logger.error(f"TTS error: {exc}")
            sd.stop()  # ensure sounddevice isn't left in a broken state
            return False

    def request_stop(self) -> None:
        """Ask an in-flight speak() to stop as soon as possible (barge-in).

        Sets a flag only — the speaking thread notices it within one poll tick
        and calls sd.stop() itself. Deliberately does NOT touch sounddevice from
        the caller's thread: a cross-thread sd.stop() races the playback poll's
        stream access and can crash PortAudio natively (the same hazard the wake
        detector already guards with a stop lock)."""
        self._stop_event.set()

    def shutdown(self) -> None:
        """Stop any ongoing playback and release resources."""
        # Signal first so an in-flight speak() unwinds itself; only force
        # sd.stop() here when nothing is playing, to avoid the cross-thread race
        # (a live speak() will stop its own stream within one poll tick).
        self._stop_event.set()
        if not self._playing:
            sd.stop()
        self._pipeline = None
        logger.debug("TTSEngine shut down.")


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.remove()
    logger.add(
        sys.stderr,
        level="DEBUG",
        colorize=True,
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | {message}",
    )

    engine = TTSEngine()
    engine.initialize()

    test_lines = [
        "Hey, how can I help you today?",
        "The meeting is scheduled for 3 PM. Shall I send the calendar invite?",
    ]

    for line in test_lines:
        print(f"\nSpeaking: {line}")
        t = time.monotonic()
        completed = engine.speak(line)
        print(f"Done in {time.monotonic() - t:.3f}s total (completed={completed})")

    # ── Barge-in / cancellation self-test ──────────────────────────────────────
    # A background thread fires request_stop() ~0.6 s into a long utterance; the
    # speak() call must return promptly with completed=False, and the NEXT speak()
    # must run to completion (the stop flag is cleared at entry).
    import threading as _threading

    long_line = (
        "This is a deliberately long sentence that should take several seconds "
        "to say out loud, so that the barge-in stop request has time to land "
        "while the audio is still playing through the speaker."
    )
    print(f"\nSpeaking (will interrupt at ~0.6s): {long_line}")
    _threading.Timer(0.6, engine.request_stop).start()
    t = time.monotonic()
    completed = engine.speak(long_line)
    elapsed = time.monotonic() - t
    print(f"Interrupted speak returned completed={completed} after {elapsed:.2f}s")
    assert completed is False, "expected barge-in to cut the utterance short"
    assert elapsed < 3.0, f"stop took too long to take effect ({elapsed:.2f}s)"

    print("\nSpeaking (should run to completion after the interrupt):")
    completed = engine.speak("And this line should play all the way through.")
    assert completed is True, "stop flag was not cleared for the next utterance"
    print("Barge-in self-test passed.")

    engine.shutdown()
    print("\nDone.")
