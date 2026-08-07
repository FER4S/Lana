# ─────────────────────────────────────────────
#  config.py – Centralised settings for Lana
#  All secrets are loaded from .env — never hardcoded.
# ─────────────────────────────────────────────

import os
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

# Load environment variables from .env (silently ignored if file is missing)
load_dotenv()

# ── Timezone ──────────────────────────────────
TIMEZONE = ZoneInfo("Asia/Beirut")

# ── Claude API ────────────────────────────────
ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL: str = os.environ.get("CLAUDE_MODEL", "claude-opus-4-5")

# ── Wake Word ─────────────────────────────────
# openwakeword model: absolute path to a custom .onnx (default: the trained
# "Hey Lana" model in models/) or a built-in model name — setting
# WAKE_WORD_MODEL=hey_jarvis in .env is the instant rollback.
WAKE_WORD_MODEL: str = os.environ.get(
    "WAKE_WORD_MODEL",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "hey_lana.onnx"),
)
# Detection confidence threshold (0.0 – 1.0)
WAKE_WORD_THRESHOLD: float = float(os.environ.get("WAKE_WORD_THRESHOLD", "0.5"))
# Threshold for the SECOND detector that listens while Lana is speaking, so the
# boss can interrupt her ("barge-in"). Kept separate from and higher than the
# idle wake threshold: a false fire here cuts her off mid-sentence. On speakers
# (no headphones) her own voice bleeds into the mic, so if self-triggers persist
# after the "don't say your own name" guard, raise this (e.g. 0.7–0.8) until they
# stop while a deliberate, close "Hey Lana" still fires. Env-tunable so it can be
# dialled in during a soak test without editing code.
BARGE_IN_THRESHOLD: float = float(os.environ.get("BARGE_IN_THRESHOLD", "0.6"))

# ── Microphone / Audio Input ──────────────────
# Set to None to use the system default microphone.
MIC_DEVICE_INDEX: int | None = (
    int(os.environ.get("MIC_DEVICE_INDEX"))
    if os.environ.get("MIC_DEVICE_INDEX")
    else None
)
MIC_SAMPLE_RATE: int = int(os.environ.get("MIC_SAMPLE_RATE", "16000"))  # Hz
MIC_CHANNELS: int = int(os.environ.get("MIC_CHANNELS", "1"))            # mono

# ── Speech-to-Text (faster-whisper) ──────────
# Model size: tiny | base | small | medium | large-v2 | large-v3 | large-v3-turbo
# large-v3-turbo (~1.7 GB fp16) is the default: short command words ("gmail")
# need a big model, and every target machine has a capable GPU.
WHISPER_MODEL_SIZE: str = os.environ.get("WHISPER_MODEL_SIZE", "large-v3-turbo")
# Device: "cuda" for GPU (RTX 5060), "cpu" as fallback
WHISPER_DEVICE: str = os.environ.get("WHISPER_DEVICE", "cuda")
WHISPER_COMPUTE_TYPE: str = os.environ.get("WHISPER_COMPUTE_TYPE", "float16")
# Model used when CUDA is unavailable and STT falls back to CPU (int8): the
# large default above is seconds-per-utterance on CPU, so the fallback
# downshifts to stay usable rather than technically-working-but-unusable.
WHISPER_CPU_FALLBACK_MODEL: str = os.environ.get("WHISPER_CPU_FALLBACK_MODEL", "base")
WHISPER_LANGUAGE: str = os.environ.get("WHISPER_LANGUAGE", "en")

# ── Text-to-Speech (Kokoro-82M, local GPU) ───────────────────────────────────
# Voice ID — see VOICES.md on hexgrad/Kokoro-82M for the full list.
# af_bella: American English female, A- overall — the boss's pick (2026-07-18)
# from the four rendered candidates in scratchpad/voice_samples/.
TTS_VOICE: str = os.environ.get("TTS_VOICE", "af_bella")
# Speed multiplier: 1.0 = normal, 1.1 = slightly faster (good for assistants)
TTS_SPEED: float = float(os.environ.get("TTS_SPEED", "1.1"))
# Device for Kokoro: "auto" (GPU if available, else CPU), "cuda", or "cpu".
# The CPU fallback in tts.py is a Python try/except, so it only catches a
# *catchable* GPU error — a NATIVE crash in torch/CUDA while loading the model
# onto the GPU (e.g. a torch build that predates the installed GPU) kills the
# process outright, before the except can run. Set TTS_DEVICE=cpu to load
# Kokoro on the CPU from the start and sidestep that. Whisper stays on its own
# WHISPER_DEVICE, so STT can remain on the GPU while only TTS moves to the CPU.
TTS_DEVICE: str = os.environ.get("TTS_DEVICE", "auto").strip().lower()

# ── Hosted voice providers ────────────────────
# Used when speech runs off-device (no GPU on the server, mic in a browser).
# Selected by name the same way email picks a fetcher/sender, so swapping a
# vendor is a config change rather than surgery. Keys are never logged.
# "local" = the GPU pipeline this project shipped with (faster-whisper + Kokoro
# + openWakeWord, microphone on this machine). Anything else routes through
# core/voice/ to a hosted provider with the microphone in a browser.
#
# DEFAULT IS STILL "local" ON PURPOSE. The hosted path needs the browser audio
# transport, which does not exist yet — flipping these before it lands would
# break the working build in exchange for nothing. Flip both defaults to
# deepgram/cartesia as part of that change; the server's .env sets them
# explicitly regardless, since it has no GPU and no microphone.
STT_PROVIDER: str = os.environ.get("STT_PROVIDER", "local").strip().lower()
TTS_PROVIDER: str = os.environ.get("TTS_PROVIDER", "local").strip().lower()

# Speech-to-text. Flux is the conversational model: it decides end-of-turn with
# a real model (~260 ms) instead of the local RMS heuristic, which was tuned to
# raw int16 from one specific microphone and does not survive browser capture.
DEEPGRAM_API_KEY: str = os.environ.get("DEEPGRAM_API_KEY", "")
DEEPGRAM_MODEL: str = os.environ.get("DEEPGRAM_MODEL", "flux-general-en")
# Batch/fallback model for non-conversational transcription.
DEEPGRAM_BATCH_MODEL: str = os.environ.get("DEEPGRAM_BATCH_MODEL", "nova-3")
# BCP-47 tag. "ar" (plus regional variants like ar-EG/ar-SA) is why this vendor
# was chosen — Kokoro has no Arabic voice at all, which is what forced the move.
DEEPGRAM_LANGUAGE: str = os.environ.get("DEEPGRAM_LANGUAGE", "en")

# Text-to-speech: Cartesia (v1 default). Chosen over ElevenLabs because v1 is
# English-only — the plan defers Arabic to its own pass — so ElevenLabs' Arabic
# advantage does not pay off yet, while Cartesia is cheaper, lower-latency, and
# has no account/tier obstacle. Revisit at the Arabic pass; swapping is a
# TTS_PROVIDER change, which is the whole point of the registry below.
CARTESIA_API_KEY: str = os.environ.get("CARTESIA_API_KEY", "")
CARTESIA_MODEL: str = os.environ.get("CARTESIA_MODEL", "sonic-3")
CARTESIA_VOICE_ID: str = os.environ.get("CARTESIA_VOICE_ID", "")
# Cartesia pins its API by date and REQUIRES this header on every request;
# omitting it is an error, not a default-to-latest.
CARTESIA_VERSION: str = os.environ.get("CARTESIA_VERSION", "2026-03-01")

# Text-to-speech: ElevenLabs (kept wired for the Arabic pass). Flash is the
# low-latency tier (~75 ms to first audio) at half the multilingual credit rate.
ELEVENLABS_API_KEY: str = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_MODEL: str = os.environ.get("ELEVENLABS_MODEL", "eleven_flash_v2_5")
ELEVENLABS_VOICE_ID: str = os.environ.get("ELEVENLABS_VOICE_ID", "")

# Shared output contract for whichever TTS provider is selected. Always raw PCM,
# never MP3: browser playback must be able to drop queued audio instantly on
# barge-in, and a decode step would block that. Signed 16-bit little-endian mono
# is what both vendors emit and what the playback worklet consumes.
TTS_SAMPLE_RATE: int = int(os.environ.get("TTS_SAMPLE_RATE", "24000"))

# ── FastAPI Server ────────────────────────────
SERVER_HOST: str = os.environ.get("SERVER_HOST", "127.0.0.1")
SERVER_PORT: int = int(os.environ.get("SERVER_PORT", "8000"))
SERVER_RELOAD: bool = os.environ.get("SERVER_RELOAD", "false").lower() == "true"
# Shared secret the frontend must present to use /start, /stop, /events, and
# every /email/* and /plans/* endpoint (all except the Gmail OAuth callback,
# which a one-time state nonce protects instead — see api/server.py).
# Empty = auth fails closed (all protected requests rejected). Generate one:
#   python -c "import secrets; print(secrets.token_urlsafe(32))"
LANA_API_TOKEN: str = os.environ.get("LANA_API_TOKEN", "")
# Opt-in: print a one-click /ui/ link with the token embedded at startup.
# OFF by default, and it must stay off anywhere the logs outlive the terminal.
# There is deliberately no automatic guard here — neither SERVER_HOST nor
# LOG_FILE can distinguish "my laptop" from "the server": behind a reverse proxy
# the server also binds 127.0.0.1, and under systemd there is no LOG_FILE
# because stderr goes to journald, where the credential is readable by anyone in
# the systemd-journal group. Any inferred rule would fail open on the one host
# that matters, so this is an explicit switch that fails closed.
LOG_TOKEN_LINK: bool = (
    os.environ.get("LANA_LOG_TOKEN_LINK", "false").strip().lower() == "true"
)

# ── Logging ───────────────────────────────────
# Loguru level: TRACE | DEBUG | INFO | SUCCESS | WARNING | ERROR | CRITICAL
LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")
LOG_FILE: str | None = os.environ.get("LOG_FILE")  # e.g. "logs/lana.log"

# ── Email ─────────────────────────────────────
# Google Cloud OAuth "Web application" client (Gmail API enabled), used for
# connecting Gmail accounts. Redirect URI registered on that client must be
# exactly "<GOOGLE_OAUTH_REDIRECT_BASE>/email/accounts/gmail/oauth-callback".
GOOGLE_OAUTH_CLIENT_ID: str = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
GOOGLE_OAUTH_CLIENT_SECRET: str = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
GOOGLE_OAUTH_REDIRECT_BASE: str = os.environ.get(
    "GOOGLE_OAUTH_REDIRECT_BASE", "http://localhost:8000"
)
# How often (seconds) to poll all connected email accounts for new mail.
EMAIL_POLL_INTERVAL_S: int = int(os.environ.get("EMAIL_POLL_INTERVAL_S", "240"))
