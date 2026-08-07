# ─────────────────────────────────────────────────────────────────────────────
#  api/audio_ws.py – The /audio WebSocket
#
#  Separate from /events ON PURPOSE. /events is a documented external contract
#  that a second team builds against; it stays JSON-only and byte-identical.
#  Audio gets its own socket, carrying binary PCM frames and JSON control
#  frames on the same connection (WebSocket distinguishes the two natively).
#
#  ONE SESSION AT A TIME. api/server.py holds a module-level LanaAssistant
#  singleton: one conversation history, one email_ctx, one state machine. Two
#  browsers would silently drive the same Lana and corrupt each other's turn.
#  So a second connection is rejected with an explicit reason the UI can show,
#  rather than being allowed to quietly break things.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import asyncio
import threading

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from loguru import logger

from core.voice import protocol as proto
from core.voice.browser_session import BrowserAudioSession

# Guards the single-session slot. Held only for the swap, never across I/O.
_session_lock = threading.Lock()
_active_session: BrowserAudioSession | None = None


def _claim_slot(session: BrowserAudioSession) -> bool:
    global _active_session
    with _session_lock:
        if _active_session is not None and not _active_session.closed:
            return False
        _active_session = session
        return True


def _release_slot(session: BrowserAudioSession) -> None:
    global _active_session
    with _session_lock:
        if _active_session is session:
            _active_session = None


def register_audio_ws(app: FastAPI, assistant, token_matches) -> None:
    """
    Mount /audio on `app`.

    `token_matches` is injected rather than imported so this module does not
    depend on api/server.py, which imports it — that would be circular.
    """

    @app.websocket("/audio")
    async def audio_socket(websocket: WebSocket) -> None:
        # Rejecting BEFORE accept() means the browser sees close code 1006, not
        # whatever we pass here — the ASGI server turns it into an HTTP 403 on
        # the handshake. That already caught this project out once on /events
        # (ui/app.js still branches on 1008, which can never fire). Auth has to
        # reject pre-accept anyway, so the client must treat 1006 on /audio as
        # "rejected", not "network glitch".
        if not token_matches(websocket.query_params.get("token")):
            await websocket.close(code=1008)
            return

        await websocket.accept()

        stt = getattr(assistant, "_stt", None)
        tts = getattr(assistant, "_tts", None)
        if not hasattr(stt, "attach_session") or not hasattr(tts, "attach_session"):
            # Local engines are selected: the microphone is on this machine and
            # a browser session has nothing to attach to. Say so plainly rather
            # than accepting a socket that can never carry audio.
            await websocket.send_json(
                {
                    "type": proto.MSG_ERROR,
                    "message": (
                        "This server is configured for a local microphone "
                        "(STT_PROVIDER/TTS_PROVIDER=local). Browser audio is off."
                    ),
                }
            )
            await websocket.close(code=proto.CLOSE_UNAUTHORIZED)
            return

        loop = asyncio.get_running_loop()
        # Pressing "talk" is the browser's wake word. _on_wake_word is exactly
        # the right entry point: it sets _wake_word_pending BEFORE _wake_event,
        # which is the ordering the dispatch loop relies on to tell a real turn
        # apart from a new-mail poke. It also emits wake_word_detected, so a
        # frontend watching for "a conversation is starting" keeps working
        # unchanged — the event means the same thing, only the trigger differs.
        session = BrowserAudioSession(
            websocket, loop, on_start_turn=assistant._on_wake_word
        )
        session.set_recognizer(stt._recognizer)

        if not _claim_slot(session):
            await websocket.send_json(
                {
                    "type": proto.MSG_BUSY,
                    "message": "Lana is already open in another tab.",
                }
            )
            await websocket.close(code=proto.CLOSE_BUSY)
            return

        stt.attach_session(session)
        tts.attach_session(session)
        logger.info("Browser audio session attached.")

        try:
            while True:
                message = await websocket.receive()

                if message.get("type") == "websocket.disconnect":
                    break

                data = message.get("bytes")
                if data is not None:
                    session.on_audio_frame(data)
                    continue

                text = message.get("text")
                if text is not None:
                    try:
                        import json

                        session.on_control(json.loads(text))
                    except (ValueError, TypeError):
                        logger.warning("Ignoring unparseable control frame on /audio.")
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            logger.warning(f"/audio closed unexpectedly: {type(exc).__name__}")
        finally:
            # Order matters: close() first so any thread blocked in
            # listen_and_transcribe or speak is released immediately, THEN
            # detach. Detaching first would leave a conversation waiting on a
            # session nothing can ever signal.
            session.close()
            stt.detach_session()
            tts.detach_session()
            _release_slot(session)
            logger.info("Browser audio session detached.")
