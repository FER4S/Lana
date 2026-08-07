# ─────────────────────────────────────────────────────────────────────────────
#  core/voice/protocol.py – The browser <-> server audio contract
#
#  Deliberately a separate, tiny module with no imports: both the server
#  (api/audio_ws.py) and the drive scripts depend on these names, and a typo in
#  a message type is the kind of bug that shows up as "audio silently does
#  nothing" rather than as an exception.
#
#  TWO CHANNELS ON ONE SOCKET. WebSocket distinguishes text and binary frames
#  natively, so:
#      binary frames -> raw PCM audio, both directions
#      text frames   -> JSON control messages, both directions
#  No length prefixes, no magic bytes, no framing of our own.
#
#  AUDIO FORMAT is signed 16-bit little-endian mono, always. Uplink is 16 kHz
#  (what the recogniser wants); downlink carries its rate in `speak_begin`,
#  because that is a property of whichever TTS provider is configured and must
#  not be assumed. The browser AudioContext usually runs at 48 kHz, so the
#  capture worklet resamples — it must never assume it is already at 16 kHz.
#
#  WHY NOT ON /events: /events is a documented external contract that a second
#  team builds against. Audio goes on its own socket so /events keeps its exact
#  current shape and stays JSON-only.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

# ── Client -> server ─────────────────────────────────────────────────────────

# The user gestured to start a turn (push-to-talk). Carries no payload; the
# server decides whether it is expecting one.
MSG_START_TURN = "start_turn"

# The capture worklet has stopped sending audio for this turn. Advisory only:
# the server also has its own endpointing, because a browser that goes away
# mid-turn will never send this.
MSG_END_TURN = "end_turn"

# The playback buffer has drained and the utterance was heard to the end. This
# is the remote replacement for core/tts.py's sd.get_stream().active poll —
# without it, speak()'s bool return is unknowable, and that return is what
# drives note_interrupted_reply() in assistant.py.
MSG_PLAYBACK_FINISHED = "playback_finished"

# The client dropped queued audio (user interrupted, or tab lost focus).
MSG_PLAYBACK_ABORTED = "playback_aborted"

# Sent once after connecting, so the server knows the real capture rate rather
# than trusting a constant.
MSG_HELLO = "hello"

# ── Server -> client ─────────────────────────────────────────────────────────

# Start capturing and streaming microphone frames.
MSG_LISTEN_START = "listen_start"

# Stop capturing. The turn is over (endpointed, timed out, or abandoned).
MSG_LISTEN_STOP = "listen_stop"

# Audio is about to stream. Carries {"sample_rate": int}.
MSG_SPEAK_BEGIN = "speak_begin"

# No more audio for this utterance; play out what is buffered, then report
# playback_finished.
MSG_SPEAK_END = "speak_end"

# Drop every queued sample RIGHT NOW. This is barge-in, and it is the whole
# reason playback is an AudioWorklet ring buffer rather than an <audio> element
# or MediaSource: those cannot be flushed promptly.
MSG_FLUSH = "flush"

# Something went wrong; carries {"message": str}. Never carries provider
# errors verbatim — those can contain hosts, keys and request ids.
MSG_ERROR = "error"

# The connection is being closed because another session already holds the
# assistant. Carries {"message": str}.
MSG_BUSY = "busy"

# ── Close codes ──────────────────────────────────────────────────────────────

# Application-level close codes must sit in the 4000-4999 private range.
# NOTE: a close sent BEFORE websocket.accept() never reaches the browser as a
# code at all — the ASGI server rejects the HTTP handshake and the browser sees
# 1006. That already bit this project once on /events (ui/app.js still branches
# on 1008, which can never fire). So any code here is only meaningful AFTER
# accept(); rejections before it must be communicated some other way.
CLOSE_BUSY = 4001          # another audio session is already attached
CLOSE_UNAUTHORIZED = 4003  # ticket/token rejected, after accept()
CLOSE_SHUTDOWN = 4004      # server is going away

# ── Audio constants ──────────────────────────────────────────────────────────

UPLINK_SAMPLE_RATE = 16_000  # what the recogniser is fed
UPLINK_CHANNELS = 1
SAMPLE_WIDTH_BYTES = 2       # s16le

# Uplink frame size. ~64 ms at 16 kHz: small enough that barge-in detection is
# responsive, large enough that a conversation is not thousands of tiny frames.
UPLINK_FRAME_SAMPLES = 1024

CONTROL_MESSAGES = frozenset(
    {
        MSG_START_TURN,
        MSG_END_TURN,
        MSG_PLAYBACK_FINISHED,
        MSG_PLAYBACK_ABORTED,
        MSG_HELLO,
        MSG_LISTEN_START,
        MSG_LISTEN_STOP,
        MSG_SPEAK_BEGIN,
        MSG_SPEAK_END,
        MSG_FLUSH,
        MSG_ERROR,
        MSG_BUSY,
    }
)
