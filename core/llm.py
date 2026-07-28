# ─────────────────────────────────────────────────────────────────────────────
#  core/llm.py – LLM layer for Lana
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import os
import sys
from datetime import datetime

# Ensure the project root is on sys.path so `import config` works when this
# file is run directly (e.g. `python core/llm.py`).
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from typing import Optional

import anthropic
from loguru import logger

from core.memory import extract_response_text
import config

# ── Constants ─────────────────────────────────────────────────────────────────

# Model: claude-haiku — fastest Claude model, ideal for real-time voice latency
MODEL_ID: str = "claude-haiku-4-5-20251001"

# Maximum tokens in Claude's reply.
# ~150 tokens ≈ 2–3 spoken sentences — keeps TTS snappy.
MAX_TOKENS: int = 150

# Request timeout in seconds. Voice assistants must feel responsive.
REQUEST_TIMEOUT: float = 10.0

# System prompt — shapes Claude's persona and response style. Deliberately
# says nothing about who the boss is: their name/clinic/role live in
# profile.json and reach every call via memory_context, so those details have
# exactly one home.
# Self-trigger guard (the last sentence): the wake word is "Hey Lana" and the
# barge-in detector listens while Lana speaks, so the boss can interrupt her.
# The wake model was trained on the same Kokoro voice she speaks with, so if she
# says her own name aloud it bleeds from the speakers into the mic and fires the
# wake word, cutting her off (verified live at score 0.681 on "I'm Lana"). The
# instruction below must stay part of the STRING, not a comment.
SYSTEM_PROMPT: str = (
    "You are Lana, a helpful voice assistant. "
    "Keep responses short and conversational since they will be spoken aloud. "
    "Avoid bullet points, markdown, or any formatting — plain sentences only. "
    "Never say the word \"Lana\" or the phrase \"hey Lana\" out loud: refer to "
    "yourself only as \"I\" and never state or introduce your own name, because "
    "hearing your name through the speakers falsely triggers the wake word and "
    "cuts you off."
)

# Fallback replies for each error category
_FALLBACK_TIMEOUT = "I'm sorry, that took too long. Please try again."
_FALLBACK_AUTH    = "I can't reach Claude right now — please check the API key."
_FALLBACK_NETWORK = "I couldn't connect. Please check your internet connection."
_FALLBACK_GENERIC = "Something went wrong. Please try again in a moment."


class LLMEngine:
    """
    Thin wrapper around the Anthropic Messages API.

    Maintains a rolling conversation history so Lana can handle follow-up
    questions within a session. Call reset_history() to start fresh.

    Usage:
        engine = LLMEngine()
        reply = engine.get_response("What's on my calendar today?")
        print(reply)
    """

    def __init__(
        self,
        api_key: str = config.ANTHROPIC_API_KEY,
        model: str = MODEL_ID,
        max_tokens: int = MAX_TOKENS,
        timeout: float = REQUEST_TIMEOUT,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        """
        Args:
            api_key:       Anthropic API key — loaded from .env via config.
            model:         Claude model identifier.
            max_tokens:    Max tokens in Claude's reply.
            timeout:       HTTP request timeout in seconds.
            system_prompt: Persona / instruction injected as the system turn.
        """
        if not api_key:
            logger.warning(
                "ANTHROPIC_API_KEY is not set. "
                "Claude calls will fail until it is added to .env."
            )

        self._client = anthropic.Anthropic(
            api_key=api_key,
            timeout=timeout,
            # Realtime voice: the boss is waiting on every call. The SDK's
            # default of 2 auto-retries can stack timeouts into ~30s of dead
            # air before the fallback line — fail within one timeout instead.
            max_retries=0,
        )
        self._model = model
        self._max_tokens = max_tokens
        self._system_prompt = system_prompt

        # Conversation history: list of {"role": "user"|"assistant", "content": str}
        # Kept in memory for the duration of the session.
        self._history: list[dict] = []

    # ── Public API ────────────────────────────────────────────────────────────

    def get_response(
        self, user_text: str, memory_context: str = "", email_context: str = ""
    ) -> str:
        """
        Send the user's transcribed speech to Claude and return its reply.

        The conversation history is maintained automatically, so follow-up
        questions work naturally within a session.

        Args:
            user_text: Transcribed text from the STT engine.
            memory_context: Optional short summary of what Lana remembers
                about the user (from MemoryManager.get_context_summary()).
                When provided, it's appended to the system prompt for this
                call only — self._system_prompt itself is never mutated.
                The actual current date and time (config.TIMEZONE) are always
                included in the system prompt for every call, regardless of
                whether memory_context is given.
            email_context: Optional one-line summary of the connected email
                accounts (from EmailManager.get_accounts_context()), appended
                to the system prompt for this call only. Lets conversational
                questions that fall through to Claude ("how many accounts are
                connected?", "do you have my Gmail?") be answered from real
                data instead of confabulated.

        Returns:
            Claude's plain-text reply, or a fallback string on error.
        """
        if not user_text.strip():
            logger.warning("get_response() called with empty text — skipping API call.")
            return _FALLBACK_GENERIC

        # Append the user turn to history
        self._history.append({"role": "user", "content": user_text})

        logger.info(f"Sending to Claude: '{user_text}'")

        now = datetime.now(config.TIMEZONE)
        effective_system_prompt = (
            f"{self._system_prompt}\n\n"
            f"Right now it is {now.strftime('%A, %B %d, %Y, %I:%M %p')} ({config.TIMEZONE})."
        )
        if memory_context:
            effective_system_prompt += f"\n\n[What I remember about you] {memory_context}"
        if email_context:
            effective_system_prompt += f"\n\n[Your connected email accounts] {email_context}"

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=effective_system_prompt,
                messages=self._history,
            )

            # Extract plain text - response.content[0] is not reliably the text
            # block (Claude can return e.g. a thinking block first), so scan
            # all content blocks rather than assuming index 0.
            reply: str = extract_response_text(response)

            # Append the assistant turn so follow-ups have context
            self._history.append({"role": "assistant", "content": reply})

            logger.success(f"Claude replied: '{reply}'")
            return reply

        except anthropic.APITimeoutError:
            logger.error("Claude request timed out.")
            self._history.pop()  # remove the unanswered user turn
            return _FALLBACK_TIMEOUT

        except anthropic.AuthenticationError:
            logger.error("Anthropic authentication failed — check ANTHROPIC_API_KEY in .env.")
            self._history.pop()
            return _FALLBACK_AUTH

        except anthropic.APIConnectionError:
            logger.error("No connection to Anthropic API — check internet.")
            self._history.pop()
            return _FALLBACK_NETWORK

        except anthropic.APIStatusError as exc:
            logger.error(f"Anthropic API error {exc.status_code}: {exc.message}")
            self._history.pop()
            return _FALLBACK_GENERIC

        except Exception as exc:
            logger.exception(f"Unexpected error calling Claude: {exc}")
            self._history.pop()
            return _FALLBACK_GENERIC

    def note_interrupted_reply(self) -> None:
        """
        Mark the last assistant reply as cut off by the user (barge-in), so the
        model knows the boss didn't hear the whole thing and shouldn't assume it
        landed. No-op if the last turn isn't an assistant turn. Appended to the
        stored text only — the spoken audio already stopped.
        """
        if self._history and self._history[-1]["role"] == "assistant":
            if "[interrupted by the user]" not in self._history[-1]["content"]:
                self._history[-1]["content"] += " … [interrupted by the user]"
            logger.debug("Marked last assistant reply as interrupted.")

    def reset_history(self) -> None:
        """Clear the conversation history (e.g. on a new wake-word trigger session)."""
        self._history.clear()
        logger.debug("Conversation history cleared.")

    @property
    def history(self) -> list[dict]:
        """Read-only view of the current conversation history."""
        return list(self._history)


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Pretty console logging for standalone testing
    logger.remove()
    logger.add(
        sys.stderr,
        level="DEBUG",
        colorize=True,
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | {message}",
    )

    engine = LLMEngine()

    # --- Single-turn test ---
    test_message = "What time is it in Beirut right now?"
    print(f"\nUser: {test_message}")
    reply = engine.get_response(test_message)
    print(f"Lana: {reply}\n")

    # --- Follow-up test (verifies history is maintained) ---
    followup = "And what about in Tokyo?"
    print(f"User: {followup}")
    reply2 = engine.get_response(followup)
    print(f"Lana: {reply2}\n")
