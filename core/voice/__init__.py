# ─────────────────────────────────────────────────────────────────────────────
#  core/voice – Hosted speech providers
#
#  Lana's speech used to run on the GPU in the same process as the mic and the
#  speaker (core/stt.py -> faster-whisper, core/tts.py -> Kokoro). v1 moves the
#  backend to a GPU-less Ubuntu server with the microphone in a browser tab, so
#  both stages become network calls.
#
#  This package holds ONLY the vendor calls: bytes in, text out; text in, bytes
#  out. It knows nothing about turn-taking, silence, barge-in, or WebSockets —
#  that lives in the browser-session layer. Keeping the split here is what lets
#  the adapters be tested offline against recorded audio with no browser and no
#  server running.
#
#  Provider selection mirrors core/email_fetch.PROVIDER_FETCHERS and
#  core/email_send.PROVIDER_SENDERS: a Protocol, concrete implementations, and a
#  name -> instance registry at the bottom of each module. A new vendor is a new
#  entry, not a rewrite — which is the seam that let TTS move from ElevenLabs to
#  Cartesia for v1 without touching a caller.
#
#  Never log an API key, and never log a transcript body at INFO.
# ─────────────────────────────────────────────────────────────────────────────
