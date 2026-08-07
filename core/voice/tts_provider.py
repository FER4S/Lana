# ─────────────────────────────────────────────────────────────────────────────
#  core/voice/tts_provider.py – Hosted text-to-speech
#
#  Replaces the Kokoro half of core/tts.py. The important difference is not the
#  vendor, it is the SHAPE: core/tts.py accumulates every Kokoro segment into a
#  list and concatenates before playing a single sample, so first-audio latency
#  equals full synthesis time. Over a network that is unusable, so the primary
#  method here is stream(), which yields PCM as it arrives.
#
#  Measured on this machine (sonic-3, 69 chars, 4.0 s of speech):
#      /tts/bytes  — 2.06 s before any audio exists
#      /tts/sse    — 0.91 s to first audio, 1.75 s to last
#  i.e. streaming starts speaking while ~0.84 s of the utterance is still being
#  generated. That head start is the entire reason this interface is a generator.
#
#  Output is ALWAYS raw PCM s16le mono, never MP3: browser playback must be able
#  to drop queued audio instantly on barge-in, and a decode step would block it.
#
#  Providers are reached through TTS_PROVIDERS, mirroring PROVIDER_SENDERS —
#  a new vendor is a new entry, not a rewrite.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Iterator, Protocol

from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import config  # noqa: E402

PROVIDER_CARTESIA = "cartesia"
PROVIDER_ELEVENLABS = "elevenlabs"

CARTESIA_SSE_URL = "https://api.cartesia.ai/tts/sse"
CARTESIA_VOICES_URL = "https://api.cartesia.ai/voices/?limit=1"
ELEVENLABS_STREAM_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice}/stream"
ELEVENLABS_USER_URL = "https://api.elevenlabs.io/v1/user/subscription"

REQUEST_TIMEOUT_S: float = 60.0

# Lana's voice: "Skylar - Friendly Guide", chosen by ear on 2026-08-07 from a
# shortlist auditioned with scratchpad/voice_audition.py. Overridable with
# CARTESIA_VOICE_ID, which is what .env sets.
#
# Still worth Alexandra's sign-off before launch — it is the voice her patients
# and staff will associate with the clinic, so it is her call rather than ours.
# Re-run the audition script to hear the shortlist again.
#
# NOT portable across providers: switching to ElevenLabs for Arabic means
# re-choosing from their library, since voice ids are vendor-specific.
CARTESIA_DEFAULT_VOICE = "db6b0ed5-d5d3-463d-ae85-518a07d3c2b4"  # "Skylar - Friendly Guide"


class SpeechSynthesizer(Protocol):
    """One vendor's text-to-speech. Text in, raw PCM s16le mono out."""

    def stream(self, text: str) -> Iterator[bytes]:
        """
        Yield PCM chunks as they are generated.

        The caller may abandon the generator at any point (close it, or break
        out of the loop) — that is how barge-in aborts synthesis, and it must
        not leave a socket open or raise on the way out.
        """
        ...

    def synthesize(self, text: str) -> bytes:
        """Whole utterance in one buffer. Convenience for tests and caching."""
        ...

    def sample_rate(self) -> int:
        """Sample rate of the PCM this synthesizer emits."""
        ...

    def test_connection(self) -> None:
        """Raise if credentials or connectivity are bad. Used at startup."""
        ...


class _BaseSynthesizer:
    """Shared plumbing: neither vendor differs in how synthesize() is derived."""

    def synthesize(self, text: str) -> bytes:
        return b"".join(self.stream(text))  # type: ignore[attr-defined]

    def sample_rate(self) -> int:
        return config.TTS_SAMPLE_RATE


class CartesiaSynthesizer(_BaseSynthesizer):
    """
    Cartesia Sonic over the SSE streaming endpoint.

    v1 default. Chosen over ElevenLabs because v1 is English-only, so
    ElevenLabs' Arabic advantage does not pay off yet, while Cartesia is
    cheaper and lower-latency and had no account obstacle.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        voice_id: str | None = None,
        version: str | None = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else config.CARTESIA_API_KEY
        self._model = model or config.CARTESIA_MODEL
        self._voice_id = voice_id or config.CARTESIA_VOICE_ID or CARTESIA_DEFAULT_VOICE
        self._version = version or config.CARTESIA_VERSION

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            raise RuntimeError(
                "CARTESIA_API_KEY is not set — speech synthesis cannot run. "
                "Add it to .env (see .env.example)."
            )
        # Cartesia pins its API by date and REQUIRES this header on every
        # request. Omitting it is an error, not a default-to-latest.
        return {
            "X-API-Key": self._api_key,
            "Cartesia-Version": self._version,
            "Content-Type": "application/json",
        }

    def _body(self, text: str) -> bytes:
        return json.dumps(
            {
                "model_id": self._model,
                "transcript": text,
                "voice": {"mode": "id", "id": self._voice_id},
                "output_format": {
                    "container": "raw",
                    "encoding": "pcm_s16le",
                    "sample_rate": config.TTS_SAMPLE_RATE,
                },
            }
        ).encode("utf-8")

    def stream(self, text: str) -> Iterator[bytes]:
        if not text or not text.strip():
            return

        request = urllib.request.Request(
            CARTESIA_SSE_URL, data=self._body(text), headers=self._headers()
        )
        try:
            response = urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S)
        except urllib.error.HTTPError as exc:
            logger.error(f"Cartesia synthesis failed: HTTP {exc.code}")
            raise RuntimeError(f"speech synthesis failed (HTTP {exc.code})") from None
        except Exception as exc:
            logger.error(f"Cartesia synthesis failed: {type(exc).__name__}")
            raise RuntimeError("speech synthesis is unreachable") from None

        # Closing the response is what aborts a barge-in mid-utterance; the
        # finally runs whether the caller exhausts us or throws GeneratorExit.
        try:
            for raw in response:
                line = raw.decode("utf-8", "ignore").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    event = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "error":
                    raise RuntimeError("speech synthesis failed mid-stream")
                chunk = event.get("data")
                if chunk:
                    yield base64.b64decode(chunk)
        finally:
            response.close()

    def test_connection(self) -> None:
        request = urllib.request.Request(
            CARTESIA_VOICES_URL, headers=self._headers(), method="GET"
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
                json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Cartesia rejected the API key (HTTP {exc.code})") from None
        except Exception as exc:
            raise RuntimeError(f"Cartesia unreachable: {type(exc).__name__}") from None


class ElevenLabsSynthesizer(_BaseSynthesizer):
    """
    ElevenLabs Flash. Kept wired for the Arabic pass, not used in v1.

    Two failure modes seen on this project, both of which return 401 and are
    easy to mistake for a bad key:
      - a permission-scoped key -> "missing_permissions"
      - an account-level "detected_unusual_activity" flag, which disables
        free-tier synthesis entirely and is NOT cleared by regenerating a key
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        voice_id: str | None = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else config.ELEVENLABS_API_KEY
        self._model = model or config.ELEVENLABS_MODEL
        self._voice_id = voice_id or config.ELEVENLABS_VOICE_ID

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            raise RuntimeError(
                "ELEVENLABS_API_KEY is not set — speech synthesis cannot run."
            )
        return {"xi-api-key": self._api_key, "Content-Type": "application/json"}

    def stream(self, text: str) -> Iterator[bytes]:
        if not text or not text.strip():
            return
        if not self._voice_id:
            raise RuntimeError(
                "ELEVENLABS_VOICE_ID is not set — pick one from the voice library."
            )

        url = ELEVENLABS_STREAM_URL.format(voice=self._voice_id)
        url += f"?output_format=pcm_{config.TTS_SAMPLE_RATE}"
        body = json.dumps({"text": text, "model_id": self._model}).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers=self._headers())

        try:
            response = urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S)
        except urllib.error.HTTPError as exc:
            logger.error(f"ElevenLabs synthesis failed: HTTP {exc.code}")
            raise RuntimeError(f"speech synthesis failed (HTTP {exc.code})") from None
        except Exception as exc:
            logger.error(f"ElevenLabs synthesis failed: {type(exc).__name__}")
            raise RuntimeError("speech synthesis is unreachable") from None

        try:
            while True:
                chunk = response.read(4096)
                if not chunk:
                    break
                yield chunk
        finally:
            response.close()

    def test_connection(self) -> None:
        request = urllib.request.Request(
            ELEVENLABS_USER_URL, headers=self._headers(), method="GET"
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
                json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"ElevenLabs rejected the API key (HTTP {exc.code})"
            ) from None
        except Exception as exc:
            raise RuntimeError(f"ElevenLabs unreachable: {type(exc).__name__}") from None


# ── Registry (extensibility point, mirrors PROVIDER_SENDERS) ─────────────────

TTS_PROVIDERS: dict[str, type] = {
    PROVIDER_CARTESIA: CartesiaSynthesizer,
    PROVIDER_ELEVENLABS: ElevenLabsSynthesizer,
}


def get_synthesizer(provider: str | None = None) -> SpeechSynthesizer:
    """Build the configured synthesizer. Raises on an unknown provider name."""
    name = (provider or config.TTS_PROVIDER).strip().lower()
    if name not in TTS_PROVIDERS:
        raise ValueError(
            f"Unknown TTS_PROVIDER {name!r}. Known: {sorted(TTS_PROVIDERS)}"
        )
    return TTS_PROVIDERS[name]()
