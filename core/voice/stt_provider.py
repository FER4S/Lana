# ─────────────────────────────────────────────────────────────────────────────
#  core/voice/stt_provider.py – Hosted speech-to-text
#
#  Replaces the faster-whisper half of core/stt.py. Deliberately narrow: it
#  takes a finished buffer of PCM and returns text. It does NOT decide when the
#  speaker started or stopped — that endpointing moves to the browser-session
#  layer, because the local RMS heuristic in core/stt.py (threshold =
#  max(ambient_rms * 1.5, 50.0), recalibrated per utterance) was tuned against
#  raw int16 from one specific microphone and is meaningless against browser
#  capture, which applies AGC and noise suppression and delivers float32.
#
#  Providers are reached through STT_PROVIDERS, mirroring
#  email_fetch.PROVIDER_FETCHERS - a new vendor is a new entry, not a rewrite.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Protocol

from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import config  # noqa: E402

PROVIDER_DEEPGRAM = "deepgram"

# Deepgram rejects a body it cannot frame, so raw PCM must declare encoding and
# sample_rate as QUERY params. A Content-Type of audio/l16 alone yields
# "corrupt or unsupported data" — a 400 that reads like a bad key but is not.
DEEPGRAM_LISTEN_URL = "https://api.deepgram.com/v1/listen"
DEEPGRAM_PROJECTS_URL = "https://api.deepgram.com/v1/projects"

REQUEST_TIMEOUT_S: float = 30.0

# Guard against a mis-framed buffer costing a real transcription call. 16 kHz
# mono s16le is 32 kB/s, so this is ~0.03 s of audio — below any real utterance.
MIN_PCM_BYTES: int = 1_000


class SpeechRecognizer(Protocol):
    """One vendor's speech-to-text. Bytes in, text out — nothing stateful."""

    def transcribe(
        self,
        pcm: bytes,
        *,
        sample_rate: int = 16_000,
        hotwords: str | None = None,
        language: str | None = None,
    ) -> str:
        """
        Transcribe mono signed-16-bit little-endian PCM.

        Returns "" for silence or an empty result, matching
        core/stt.py's listen_and_transcribe contract — every one of its ~50
        call sites in core/assistant.py already reads "" as "nothing was said".
        Raises on transport/auth failure so the caller can distinguish a dead
        provider from a quiet room; the two must never collapse together.
        """
        ...

    def test_connection(self) -> None:
        """Raise if credentials or connectivity are bad. Used at startup."""
        ...


class DeepgramRecognizer:
    """
    Deepgram speech-to-text over the prerecorded endpoint.

    This is the buffer-at-a-time path, used once the browser-session layer has
    a complete utterance. The streaming Flux socket — which decides end-of-turn
    with a real model (~260 ms) rather than a fixed silence timer — is a
    separate concern and arrives with the audio WebSocket.

    Uses urllib rather than the vendor SDK on purpose: one HTTP POST does not
    justify a dependency, and the SDK would drag in its own transport stack on
    a box whose whole point is being small.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        language: str | None = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else config.DEEPGRAM_API_KEY
        self._model = model or config.DEEPGRAM_BATCH_MODEL
        self._language = language if language is not None else config.DEEPGRAM_LANGUAGE

    # ── internals ────────────────────────────────────────────────────────────

    def _headers(self, content_type: str | None = None) -> dict[str, str]:
        if not self._api_key:
            raise RuntimeError(
                "DEEPGRAM_API_KEY is not set — speech-to-text cannot run. "
                "Add it to .env (see .env.example)."
            )
        headers = {"Authorization": f"Token {self._api_key}"}
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _listen_url(
        self, sample_rate: int, hotwords: str | None, language: str | None
    ) -> str:
        params = {
            "model": self._model,
            "encoding": "linear16",
            "sample_rate": str(sample_rate),
            "channels": "1",
            "punctuate": "true",
            "smart_format": "true",
        }
        lang = language if language is not None else self._language
        if lang:
            params["language"] = lang
        query = urllib.parse.urlencode(params)

        # Keyterm prompting is why this vendor suits Lana specifically: the
        # vocabulary that matters here is supplement names, clinical terms and
        # patient first names, none of which a general model spells reliably.
        # core/stt.py already threads a `hotwords` argument through, so the
        # existing call sites map across unchanged.
        if hotwords:
            for term in (t.strip() for t in hotwords.split(",")):
                if term:
                    query += "&" + urllib.parse.urlencode({"keyterm": term})
        return f"{DEEPGRAM_LISTEN_URL}?{query}"

    # ── SpeechRecognizer ─────────────────────────────────────────────────────

    def transcribe(
        self,
        pcm: bytes,
        *,
        sample_rate: int = 16_000,
        hotwords: str | None = None,
        language: str | None = None,
    ) -> str:
        if not pcm or len(pcm) < MIN_PCM_BYTES:
            # Too short to contain speech. Return the silence answer rather
            # than paying for a call that can only come back empty.
            return ""

        request = urllib.request.Request(
            self._listen_url(sample_rate, hotwords, language),
            data=pcm,
            headers=self._headers("application/octet-stream"),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Never echo the response body outward: it can carry request ids and
            # account detail. Log the status, raise something plain.
            logger.error(f"Deepgram transcription failed: HTTP {exc.code}")
            raise RuntimeError(f"speech-to-text failed (HTTP {exc.code})") from None
        except Exception as exc:
            logger.error(f"Deepgram transcription failed: {type(exc).__name__}")
            raise RuntimeError("speech-to-text is unreachable") from None

        try:
            alternatives = body["results"]["channels"][0]["alternatives"]
            transcript = (alternatives[0].get("transcript") or "").strip()
        except (KeyError, IndexError):
            logger.warning("Deepgram returned a response with no transcript field.")
            return ""

        # Length only — a transcript is the boss speaking, and on a shared box
        # the log is not the place for it.
        logger.debug(f"Deepgram transcript: {len(transcript)} chars")
        return transcript

    def test_connection(self) -> None:
        request = urllib.request.Request(
            DEEPGRAM_PROJECTS_URL, headers=self._headers(), method="GET"
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
                json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Deepgram rejected the API key (HTTP {exc.code})") from None
        except Exception as exc:
            raise RuntimeError(f"Deepgram unreachable: {type(exc).__name__}") from None


# ── Registry (extensibility point, mirrors PROVIDER_FETCHERS) ────────────────

STT_PROVIDERS: dict[str, type] = {
    PROVIDER_DEEPGRAM: DeepgramRecognizer,
}


def get_recognizer(provider: str | None = None) -> SpeechRecognizer:
    """Build the configured recognizer. Raises on an unknown provider name."""
    name = (provider or config.STT_PROVIDER).strip().lower()
    if name not in STT_PROVIDERS:
        raise ValueError(
            f"Unknown STT_PROVIDER {name!r}. Known: {sorted(STT_PROVIDERS)}"
        )
    return STT_PROVIDERS[name]()
