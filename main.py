# ─────────────────────────────────────────────
#  main.py – Lana entry point
# ─────────────────────────────────────────────

import sys
import threading
import uvicorn
from loguru import logger

import config
from api.server import app, assistant  # FastAPI app and global assistant


def configure_logger() -> None:
    """Set up Loguru with the level and optional file sink from config."""
    logger.remove()  # Remove the default stderr handler
    logger.add(
        sys.stderr,
        level=config.LOG_LEVEL,
        colorize=True,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> — "
            "<level>{message}</level>"
        ),
    )
    if config.LOG_FILE:
        logger.add(
            config.LOG_FILE,
            level=config.LOG_LEVEL,
            rotation="10 MB",
            retention="7 days",
            compression="zip",
        )


def main() -> None:
    configure_logger()

    logger.info("─" * 50)
    logger.success("✅  Lana is ready.")
    logger.info(f"    Model  : {config.CLAUDE_MODEL}")
    logger.info(f"    STT    : faster-whisper ({config.WHISPER_MODEL_SIZE} / {config.WHISPER_DEVICE})")
    logger.info(f"    TTS    : Kokoro-82M ({config.TTS_VOICE})")
    logger.info(f"    Server : http://{config.SERVER_HOST}:{config.SERVER_PORT}")
    # A one-click link with the token already in it. The dashboard stashes the
    # token in sessionStorage and strips it from the address bar on load, so it
    # is not left sitting in shot during a screen recording.
    if config.LANA_API_TOKEN:
        logger.info(
            f"    UI     : http://{config.SERVER_HOST}:{config.SERVER_PORT}"
            f"/ui/?token={config.LANA_API_TOKEN}"
        )
    else:
        logger.warning(
            "    UI     : unavailable — LANA_API_TOKEN is not set in .env, so "
            "the dashboard has nothing to authenticate with."
        )
    logger.info("─" * 50)

    # Start the voice pipeline automatically on launch in a background thread
    logger.info("Starting Lana Assistant background thread...")
    threading.Thread(target=assistant.run, name="lana-main-loop", daemon=True).start()

    uvicorn.run(
        app,
        host=config.SERVER_HOST,
        port=config.SERVER_PORT,
        reload=config.SERVER_RELOAD,
        log_level=config.LOG_LEVEL.lower(),
    )


if __name__ == "__main__":
    main()
