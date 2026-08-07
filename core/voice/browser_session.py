# ─────────────────────────────────────────────────────────────────────────────
#  core/voice/browser_session.py – One browser's microphone and speaker
#
#  THE WHOLE JOB of this file is to make a remote browser look exactly like the
#  local sound card that core/assistant.py was written against:
#
#      listen_and_transcribe(initial_silence_timeout, *, hotwords) -> str
#      speak(text) -> bool
#
#  Both BLOCK, because ~50 call sites in assistant.py depend on them blocking,
#  and speak()'s bool return drives note_interrupted_reply(). Preserving those
#  two signatures is what keeps the memory/plan/email intercept logic untouched.
#
#  THREADING. The WebSocket lives on the asyncio event loop; the conversation
#  runs on the plain `lana-conversation` thread. Everything crossing that line
#  goes one of two ways and no other:
#      conversation -> socket : asyncio.run_coroutine_threadsafe(...)
#      socket -> conversation : queue.Queue / threading.Event
#  Never touch the WebSocket object directly from the conversation thread.
#
#  WHERE ENDPOINTING LIVES: in the browser, not here. The client knows when it
#  is playing audio, has the echo-cancelled microphone, and can run cheap energy
#  detection in the capture worklet. So the browser decides "speech ended" and
#  "the user started talking over Lana", and sends end_turn / barge_in. The
#  server only enforces timeouts as a backstop. This is deliberate: the RMS
#  endpointing in core/stt.py was calibrated against raw int16 from one specific
#  microphone and is meaningless against AGC'd, noise-suppressed browser audio.
#
#  DISCONNECTS ARE THE NEW FAILURE MODE. A tab can close, a laptop can sleep,
#  wifi can drop — mid-turn, mid-send-confirmation, mid-anything. Every wait in
#  here is bounded and every path resolves to "" or False, never to a hang: the
#  dispatch loop joins the conversation thread, so one stuck wait would freeze
#  Lana permanently with no wake word left to recover her.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import asyncio
import queue
import threading
import time

from loguru import logger

from core.voice import protocol as proto

# Hard ceiling on one utterance, mirroring core/stt.py's MAX_DURATION_S. Keeps
# _capture_case's segment stitching working unchanged.
MAX_TURN_DURATION_S: float = 30.0

# How long to wait for the browser to confirm playback finished before giving
# up. Generous: it must exceed the longest utterance Lana can produce, or a long
# email read would be reported as interrupted. Playback is also bounded by the
# audio's own duration, which we know, so this is only the safety net.
PLAYBACK_ACK_GRACE_S: float = 15.0

# Sending is a network call; if it cannot complete quickly the client is gone.
SEND_TIMEOUT_S: float = 5.0


class BrowserAudioSession:
    """One connected browser. Not reusable — build a new one per connection."""

    def __init__(
        self,
        websocket,
        loop: asyncio.AbstractEventLoop,
        on_start_turn=None,
    ) -> None:
        self._ws = websocket
        self._loop = loop
        # Called when the user gestures to talk. This is the browser's
        # replacement for the wake word: with no detector running, NOTHING else
        # ever sets the assistant's wake event, so without this the dispatch
        # loop waits forever and pressing the button does nothing at all.
        self._on_start_turn = on_start_turn

        # Socket -> conversation.
        self._audio: queue.Queue[bytes | None] = queue.Queue()
        self._end_of_turn = threading.Event()
        self._playback_finished = threading.Event()
        self._barge_in = threading.Event()
        self._closed = threading.Event()

        # The client reports its real capture rate in `hello`; never assume.
        self._uplink_rate = proto.UPLINK_SAMPLE_RATE

        self._recognizer = None  # injected by the endpoint
        self._listening = False

    # ── wiring ───────────────────────────────────────────────────────────────

    def set_recognizer(self, recognizer) -> None:
        self._recognizer = recognizer

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def close(self) -> None:
        """Idempotent. Releases every blocked wait so no thread is stranded."""
        self._closed.set()
        self._end_of_turn.set()
        self._playback_finished.set()
        self._audio.put(None)

    # ── called from the asyncio side ─────────────────────────────────────────

    def on_audio_frame(self, pcm: bytes) -> None:
        if self._listening and not self._closed.is_set():
            self._audio.put(pcm)

    def on_control(self, message: dict) -> None:
        kind = message.get("type")
        if kind == proto.MSG_HELLO:
            rate = message.get("sample_rate")
            if isinstance(rate, int) and rate > 0:
                self._uplink_rate = rate
                logger.info(f"Browser audio session: uplink at {rate} Hz.")
        elif kind == proto.MSG_START_TURN:
            # Runs on the asyncio thread, so the callback must not block — it
            # only sets a flag and an event, exactly like the wake-word
            # callback it replaces.
            if self._on_start_turn is not None:
                try:
                    self._on_start_turn()
                except Exception as exc:
                    logger.error(f"start_turn handler failed: {type(exc).__name__}")
        elif kind == proto.MSG_END_TURN:
            self._end_of_turn.set()
        elif kind == proto.MSG_PLAYBACK_FINISHED:
            self._playback_finished.set()
        elif kind == proto.MSG_PLAYBACK_ABORTED:
            self._barge_in.set()
            self._playback_finished.set()

    # ── conversation -> socket ───────────────────────────────────────────────

    def _send_control(self, kind: str, **fields) -> bool:
        if self._closed.is_set():
            return False
        payload = {"type": kind}
        payload.update(fields)
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._ws.send_json(payload), self._loop
            )
            future.result(timeout=SEND_TIMEOUT_S)
            return True
        except Exception:
            logger.warning(f"Browser audio session: failed to send {kind}; closing.")
            self.close()
            return False

    def _send_audio(self, pcm: bytes) -> bool:
        if self._closed.is_set():
            return False
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._ws.send_bytes(pcm), self._loop
            )
            future.result(timeout=SEND_TIMEOUT_S)
            return True
        except Exception:
            logger.warning("Browser audio session: audio send failed; closing.")
            self.close()
            return False

    # ── the STTEngine-shaped half ────────────────────────────────────────────

    def listen_and_transcribe(
        self,
        initial_silence_timeout: float = 2.0,
        *,
        hotwords: str | None = None,
    ) -> str:
        """
        Blocks until the browser reports the turn ended, then transcribes.

        Returns "" for silence, a closed socket, or any failure — the same
        contract core/stt.py has, which assistant.py reads as "nothing was
        said" and uses to end the turn cleanly.
        """
        if self._closed.is_set() or self._recognizer is None:
            return ""

        # Drain anything left over from a previous turn so stale frames cannot
        # be transcribed as this turn's answer.
        self._drain_audio()
        self._end_of_turn.clear()
        self._barge_in.clear()

        self._listening = True
        try:
            if not self._send_control(proto.MSG_LISTEN_START):
                return ""

            frames: list[bytes] = []
            deadline = time.monotonic() + MAX_TURN_DURATION_S
            # Before any audio arrives, the wait is bounded by the caller's
            # timeout — that is exactly what initial_silence_timeout means:
            # how long to wait for speech to START, not how long to record.
            first_frame_deadline = time.monotonic() + initial_silence_timeout

            while True:
                if self._closed.is_set():
                    return ""
                if self._end_of_turn.is_set():
                    break

                now = time.monotonic()
                if now >= deadline:
                    logger.info("Browser turn hit the 30s cap; cutting.")
                    break
                if not frames and now >= first_frame_deadline:
                    # Nobody spoke.
                    break

                try:
                    chunk = self._audio.get(timeout=0.1)
                except queue.Empty:
                    continue
                if chunk is None:  # close() sentinel
                    return ""
                frames.append(chunk)

            self._send_control(proto.MSG_LISTEN_STOP)
        finally:
            self._listening = False

        if not frames:
            return ""

        pcm = b"".join(frames)
        try:
            return self._recognizer.transcribe(
                pcm, sample_rate=self._uplink_rate, hotwords=hotwords
            )
        except RuntimeError as exc:
            # A dead provider must not masquerade as silence forever, but it
            # also must not crash the conversation thread. Log loudly, end the
            # turn quietly.
            logger.error(f"Transcription failed: {exc}")
            return ""

    def _drain_audio(self) -> None:
        while True:
            try:
                self._audio.get_nowait()
            except queue.Empty:
                return

    # ── the TTSEngine-shaped half ────────────────────────────────────────────

    def speak(self, text: str, synthesizer, stop_event: threading.Event) -> bool:
        """
        Stream synthesised audio to the browser and block until it has played.

        Returns True if the whole utterance was heard, False if it was cut
        short by barge-in, a stop request, or a lost connection.
        """
        if self._closed.is_set():
            return False

        self._playback_finished.clear()
        self._barge_in.clear()

        rate = synthesizer.sample_rate()
        if not self._send_control(proto.MSG_SPEAK_BEGIN, sample_rate=rate):
            return False

        streamed = 0
        interrupted = False
        stream = synthesizer.stream(text)
        try:
            for chunk in stream:
                if stop_event.is_set() or self._barge_in.is_set() or self._closed.is_set():
                    interrupted = True
                    break
                if not self._send_audio(chunk):
                    return False
                streamed += len(chunk)
        except RuntimeError as exc:
            logger.error(f"Synthesis failed mid-utterance: {exc}")
            interrupted = True
        finally:
            # Closing the generator aborts the provider stream — this is what
            # stops us paying for audio nobody will hear after a barge-in.
            stream.close()

        if interrupted:
            self.flush_playback()
            return False

        if not self._send_control(proto.MSG_SPEAK_END):
            return False

        # Wait for the browser to confirm the buffer drained. Bounded by the
        # audio's own duration plus grace, so a silent client cannot wedge the
        # conversation thread.
        audio_seconds = streamed / proto.SAMPLE_WIDTH_BYTES / max(rate, 1)
        if not self._playback_finished.wait(audio_seconds + PLAYBACK_ACK_GRACE_S):
            logger.warning("No playback confirmation from the browser; assuming lost.")
            return False

        # A barge-in can land in the gap between the last chunk and the ack.
        return not (self._barge_in.is_set() or stop_event.is_set())

    def flush_playback(self) -> None:
        """Drop everything queued in the browser, now. This is barge-in."""
        self._send_control(proto.MSG_FLUSH)
