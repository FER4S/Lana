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

# ── FastAPI Server ────────────────────────────
SERVER_HOST: str = os.environ.get("SERVER_HOST", "127.0.0.1")
SERVER_PORT: int = int(os.environ.get("SERVER_PORT", "8000"))
SERVER_RELOAD: bool = os.environ.get("SERVER_RELOAD", "false").lower() == "true"
# Shared secret the frontend must present to use /start, /stop and /events.
# Empty = auth fails closed (all protected requests rejected). Generate one:
#   python -c "import secrets; print(secrets.token_urlsafe(32))"
LANA_API_TOKEN: str = os.environ.get("LANA_API_TOKEN", "")

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
