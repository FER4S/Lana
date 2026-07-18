# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Lana is a GPU-accelerated, fully local voice AI assistant for Windows. Pipeline:

```
Wake word (openwakeword) → STT (faster-whisper) → LLM (Claude API) → TTS (Kokoro-82M) → Speaker
```

Everything runs on-device except the LLM call itself, which goes to the Anthropic API. A FastAPI server
wraps the pipeline so an external frontend (e.g. an Electron app) can start/stop it and receive
real-time state over a WebSocket.

On top of that pipeline sit two feature modules, both driven by the same voice loop:
- **Persistent memory** (`core/memory.py`) — a static profile seed (`profile.json`), "remember that…"
  commands, background extraction.
- **Email** (`core/email_accounts.py`, `core/email_fetch.py`, `core/email_send.py`, `core/email_manager.py`)
  — multi-account IMAP + Gmail-OAuth, background polling, proactive spoken new-mail announcements, voice
  reading/searching/Q&A, and voice **sending + replying** behind a mandatory spoken confirmation. CC/BCC,
  attachments, multiple recipients, and reply threading are deliberately not implemented. Account
  management endpoints live under `/email/*` in `api/server.py`; **no endpoint sends email.**

## Commands

```bash
# Setup (Windows only — CUDA build of torch must be installed first)
python -m venv .venv
.venv\Scripts\activate
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
pip install -r requirements.txt
copy .env.example .env   # then fill in ANTHROPIC_API_KEY

# Run the full app (starts the voice pipeline + FastAPI server on :8000)
python main.py
```

There is no test suite, linter, or formatter configured in this repo. Each `core/*.py` module has a
`if __name__ == "__main__":` block that runs it standalone for manual testing — this is the way to
exercise one stage of the pipeline in isolation without running the whole assistant:

```bash
python -m core.wake_word   # mic loop, prints on wake word detection
python -m core.stt         # records one utterance and prints the transcription
python -m core.llm         # sends two hardcoded test messages to Claude and prints replies
python -m core.tts         # synthesizes and plays a couple of hardcoded lines
python -m core.memory      # loads data/memory.json + profile.json, adds a test fact, prints the summary
python -m core.email_accounts  # DPAPI encrypt/decrypt round-trip + tamper check; add/list/delete a smoke account
python -m core.email_fetch     # offline html_to_text/date asserts; live IMAP smoke if SMOKE_IMAP_* env vars are set
python -m core.email_send      # offline MIME/derive_smtp_host asserts; live send if SMOKE_SMTP_* env vars are set
python -m core.email_manager   # loads the account store + cache, runs one poll cycle, prints the dashboard summary
```

(Each module also inserts the project root onto `sys.path` at import time, so `python core/stt.py`
direct-path invocation works too, not just `python -m`.)

### Verifying email changes (no microphone needed)

The email module is large, multi-turn, and stateful, so `__main__` blocks are not enough. The established
practice is throwaway **driver scripts in `scratchpad/`** (git-ignored) that exercise the *real* handler
code with STT/TTS stubbed:

- **Classifier harness** — call `EmailManager.classify_intent()` on exact (often STT-garbled) utterances
  with the same `context_emails` / `active_search` the live turn had, and assert on the returned intent.
  Run repeats (3×) on critical rows, since it's a live Haiku call with real variance.
- **Conversation drives** — construct `LanaAssistant()`, monkeypatch `_speak_confirmation` /
  `_ask_and_listen` to capture and script, stub `classify_intent` + `get_emails_for_scope`, then call
  `_maybe_handle_email_query(text, email_ctx)` turn by turn and assert on what got "spoken" and how
  `email_ctx` evolved.

**Every serious bug in this module was found by driving the handlers, not by reading them** — routing and
gate bugs are invisible on the page. Prefer writing a drive script over reasoning about a multi-turn flow.
A `python scratchpad/oauth_url_check.py --http` script also exists to mint/verify a real Google consent URL.

## Architecture

### Two entry points, one shared singleton

`main.py` and `api/server.py` both reference the same `LanaAssistant` instance
(`api/server.py` constructs it as a module-level singleton: `assistant = LanaAssistant()`).
`main.py` imports `app, assistant` from `api.server` and spins the voice loop up in a background
thread before handing control to `uvicorn.run(app, ...)`. The `/start` and `/stop` REST endpoints in
`api/server.py` can also start/stop that same background thread — so the assistant can be running
because of the auto-start in `main.py`, or because a frontend hit `/start`, and both paths converge
on `assistant.run()` / `assistant.stop()`.

### State machine and threading model (`core/assistant.py`)

`LanaAssistant.run()` blocks forever on a **dispatch loop**. Each pass, in priority order:

1. **Wake word pending?** (`_wake_word_pending`, set by `_on_wake_word` *before* it sets `_wake_event`)
   → spawn a normal conversation.
2. **Otherwise `self._email.claim_announcement()`** → if it returns text, spawn an *announcement*
   conversation (`_run_conversation(announcement=...)`).
3. **Otherwise** block on `_wake_event.wait()`. Both `_on_wake_word` and the email poller's
   `_on_new_mail` callback set that event; the flag is what tells the two apart.

Each spawn is a **new** `lana-conversation` thread that the loop `.join()`s before continuing — so
conversations are strictly serial and **all TTS/mic use is serialized through one thread by
construction**. That is the whole reason announcements are delivered from this loop rather than from the
poller thread (`TTSEngine` has no lock, and the mic is exclusively held). A new-mail poke that arrives
while a conversation is running is not lost: the loop re-checks `claim_announcement()` at the top before
ever waiting again, so the dispatch checks must stay **above** the `wait()`.

Inside `_run_conversation(announcement=None)`:
1. The wake detector is stopped first, to free the microphone (`WakeWordDetector` and `STTEngine` both
   open their own `pyaudio` stream and cannot run concurrently).
2. `LLMEngine.reset_history()` is called — every conversation starts a **fresh** Claude conversation.
3. Lana speaks the fixed greeting — or, in announcement mode, speaks the announcement instead (via
   `_speak_confirmation`, so it emits an `llm_response` the frontend transcript can show) and then uses
   the follow-up timeout for the first listen, since the boss wasn't asked to speak.
4. Turn loop: listen → transcribe → **memory intercept** (`_extract_explicit_memory_trigger`) →
   **email intercept** (`_maybe_handle_email_query`) → otherwise Claude → speak. The two intercepts
   short-circuit the turn and never enter `self._llm.history`. Ordering matters: memory runs first, so
   "remember that…" inside an email conversation still goes to memory.
5. After the first turn, `listen_and_transcribe()` uses a longer `initial_silence_timeout`
   (`FOLLOWUP_SILENCE_TIMEOUT` = 5s). If nothing is transcribed, the conversation ends.
6. The `finally` block spawns background memory extraction, clears `_wake_word_pending`/`_wake_event`,
   and restarts the wake detector — but **only `if self._running`**. Restarting it unconditionally would
   resurrect the mic thread after a `stop()` that landed mid-conversation.

**Rule for anything that speaks or listens:** a handler that asks a question and needs the answer *in the
same turn* uses `_ask_and_listen` (speak + listen, and it **must consume the return value**). A handler
that asks a question whose answer should arrive as the *next* turn uses `_speak_confirmation` (speak
only) and lets the turn loop's own listen pick it up. Calling `_ask_and_listen` and discarding its result
causes a phantom second listen window with no prompt — a real bug this codebase has already shipped once.

`AssistantState` (IDLE/LISTENING/THINKING/SPEAKING) and a `queue.Queue` of JSON-able event dicts are
the only two channels the FastAPI layer reads from — `_emit_event()` is called at every pipeline
transition, and `api/server.py`'s `broadcast_events()` background task drains that queue and fans each
event out to every connected WebSocket client. Event names are documented in [API.md](API.md) and must
stay in sync with what `_emit_event()` actually sends.

### Persistent memory (`core/memory.py`)

`MemoryManager` stores what Lana knows about the boss in `data/memory.json` (git-ignored — personal
data, never committed): a `profile` dict seeded from the committed `profile.json` (see below), plus
`people`/`events`/`facts` lists that accumulate over time. It owns its own `anthropic.Anthropic` client,
separate from `LLMEngine`'s, because its calls (extraction, fact cleanup) must never touch the stateful
voice-conversation history.

Model tiering: `core/llm.py`'s conversational engine always uses the fast Haiku model for realtime voice
latency. Memory's background work uses a second, heavier tier — `EXTRACTION_MODEL_ID` in `memory.py`
(currently `claude-sonnet-5`) — since extraction runs off the voice-latency critical path. Any future
non-realtime, heavier task (briefings, summaries, etc.) should default to this same background tier
rather than introducing a third model casually.

**Static profile seed, not onboarding.** There is no first-run onboarding dialogue.
`MemoryManager.load()` reads `data/memory.json` and then calls `_apply_static_profile_locked()`, which
reads the committed `profile.json` (repo root — `name`/`clinic`/`role`/`key_people`/`priorities`/
`preferences`) and overwrites `self._state["profile"]` with it whenever the two differ, persisting the
result. So `profile.json` is authoritative for the profile on every startup: editing it and restarting
is the only way to change the boss's profile, with no `memory.json` surgery and no voice interaction. A
missing or unreadable `profile.json` logs a warning and leaves whatever profile is already in
`memory.json` alone — it never blocks startup. `core/assistant.py`'s `run()` just calls
`self._memory.load()` before `self._detector.start()`, same as before; there is no separate onboarding
gate, question loop, state, or event to keep in sync with `api/server.py`.

Two further touchpoints in `core/assistant.py`:
1. **Explicit memory commands** ("remember that...", "don't forget...") are intercepted in
   `_run_conversation`'s turn loop right after transcription and handled by `_handle_explicit_memory`,
   short-circuiting `LLMEngine.get_response()` for that turn (so they never enter `self._llm.history`).
   `MemoryManager.structure_explicit_memory` (one Haiku call) classifies the command as a plain **fact**
   (→ `clean_fact_text` + `add_explicit_memory`, the facts list) or a dated **event** (→ the events list).
   Event-like commands get dedup/update handling: a novel event is saved silently via `add_event`; one that
   resembles an existing event triggers a **live spoken clarifying question** ("same one rescheduled, or
   new?"), and the boss's answer (classified three-way by `classify_same_or_new` as same/new/unclear)
   decides the outcome. `same` → `update_event_date` in place; an explicit `new` → `add_event(force_new=True)`,
   which appends a separate entry even on a description collision (an explicit answer must not be silently
   merged by dedup); `unclear`/silent → `add_event` without forcing, still recording the event but letting
   the dedup safety net prevent a stray duplicate on a non-answer. This whole sub-dialogue reuses only the existing
   `THINKING`/`LISTENING`/`SPEAKING` states and `llm_response`/`speaking_started`/`speaking_ended`/
   `listening_started`/`transcription` events (no new state or event names — `api/server.py`/API.md
   untouched).
2. **Background extraction** (`memory.extract_and_save(self._llm.history)`) is spawned as a daemon thread
   — the *first* statement in `_run_conversation`'s `finally` block, before the wake detector restarts —
   so a slow Sonnet-tier call never delays "Hey Lana" being re-triggerable. `LLMEngine.history` returns a
   defensive copy, so snapshotting it there (before the detector can hand off to a new conversation) is
   race-free. The extraction call is also given the current events list as context and returns an `updates`
   field per event so `_merge_extracted` can update an existing event in place (matched by exact description
   string) rather than appending a near-duplicate.

**Event dedup/update** (the one category with matching logic): events dedup by an LLM similarity judgment,
not string equality. Both paths match a new mention against existing events by their **exact description
string** (a stable content key — the events list is mutated by the conversation thread and the background
daemon and read by `get_context_summary`, so an array index would be racy). On a match, the existing
entry's date is updated in place (never blanked by an empty new date). Pre-existing duplicate events are
**not** retroactively merged — matching only ever runs new-mention→existing, so legacy dupes are left as-is;
this only prevents new ones. Facts (exact-match dedup) and people (name-match + notes-merge) are unchanged.

`get_context_summary()` (injected into `LLMEngine.get_response()`'s system prompt as `memory_context`)
budgets itself to roughly 300 tokens via a char-count heuristic (no tokenizer dependency in this project).
`facts`/`events` are the two lists that grow unboundedly over time, so that budget is applied
recency-first — the oldest entries are dropped before the newest ones, never the reverse.

### Email integration (`core/email_accounts.py`, `core/email_fetch.py`, `core/email_send.py`, `core/email_manager.py`)

Four layers, plus voice handlers in `core/assistant.py` and `/email/*` endpoints in `api/server.py`.

**Exactly one `EmailManager` per process.** It is constructed in `LanaAssistant.__init__`, and
`api/server.py` binds the *same* object via `assistant.get_email_manager()`. Two instances would
double-poll and race the cache file. `initialize()` and `start_polling()` are both **idempotent**,
because `main.py` starts `assistant.run()` *before* uvicorn runs the server lifespan — either may go
first. `assistant.stop()` deliberately does **not** stop polling (the dashboard needs `/email/summary`
fresh while the voice pipeline is stopped); only the server lifespan shutdown does.

**Account storage (`email_accounts.py`).** `EmailAccountStore` keeps accounts in `data/email_accounts.dat`,
encrypted at rest with **Windows DPAPI** via `ctypes` (no key file anywhere — the repo lives in OneDrive,
so only ciphertext syncs). Same persistence pattern as `memory.py`: one lock, atomic tmp+`os.replace`,
and quarantine-on-unreadable (rename aside, start empty, never crash — this also covers a DPAPI blob
copied to another machine/user, which can never be decrypted there). Never log credentials. Schema:
`{version, accounts:[{id, label, provider: "imap"|"gmail_oauth", created_at, credentials:{...}}]}` — a new
provider is a new provider string + creds dict, no schema rewrite. IMAP creds carry optional
`smtp_host`/`smtp_port`/`smtp_use_ssl`; absent, `email_send.derive_smtp_host()` derives the outgoing host
from the incoming one at send time, so accounts stored before sending existed need no migration.

**Fetch layer (`email_fetch.py`).** `PROVIDER_FETCHERS` maps provider → fetcher (`ImapFetcher` via
imapclient, `GmailFetcher` via the Gmail API). Both return the same **normalized email dict**
(`id`, `account_id`, `account_label`, `subject`, `sender_name`, `sender_email`, `date` ISO in
`config.TIMEZONE`, `unread`, `snippet`, `body`). Three methods: `fetch_recent()` (poller),
`search(days, max_n, sender, subject, unread_only, before)` (on-demand), `test_connection()`.
IMAP `select_folder("INBOX", readonly=True)` is **load-bearing** — fetching bodies must never set `\Seen`.

**Poller + cache.** A daemon thread (`lana-email-poller`) polls every `config.EMAIL_POLL_INTERVAL_S`
(240s default, floored at `POLL_MIN_INTERVAL_S`), writing `data/email_cache.json` (plaintext, git-ignored).
Per account it caches only the **recent window**: `FETCH_WINDOW_DAYS=2`, `CACHED_EMAILS_PER_ACCOUNT=25`.

> **The single most misleading thing about this module:** `unread_count` and the cached `emails` list are
> **decoupled**. `unread_count` is the provider's total-unread number (Gmail's INBOX label — can be 2900+).
> The readable/cached `emails` are only the last 2 days (often a handful). A big count with 4 readable
> emails is *correct*, not a bug. Anything older is reached by live search, never from the cache.

New mail = `id not in seen_ids` AND `unread` AND younger than `NEW_MAIL_MAX_AGE_S`. The first poll for an
account is a silent **baseline** (mark seen, announce nothing). An IMAP `UIDVALIDITY` change resets
`seen_ids` and re-baselines silently. One dead account never kills a cycle (per-account try/except,
throttled logging); the poller — not any voice path — owns `last_error`.

**Proactive announcements.** The poll thread calls `EmailManager(on_new_mail=...)` → `_on_new_mail`, which
(like `_on_wake_word`) only pokes `_wake_event` and returns — no work on the poller's thread. The dispatch
loop wakes, gives a real wake word priority, and otherwise calls `claim_announcement()`; a non-`None`
result is passed to `_run_conversation(announcement=...)`, which speaks it in place of the greeting and
then listens as if "Hey Lana" had been said.

`claim_announcement()` **atomically** composes the text *and* clears every account's `unannounced_ids`
under the cache lock, after a staleness filter (still cached AND still unread AND younger than
`ANNOUNCE_MAX_AGE_S`). So it is **at-most-once**: claim-then-crash loses the nudge (the mail is still
queryable), which is the deliberate alternative to a compose-then-mark split that could loop forever
re-announcing the same batch.

**The voice email intercept (`_maybe_handle_email_query` in `assistant.py`).** Runs after the memory
intercept, short-circuits the turn, never touches `self._llm.history`. The **gate** opens on an
`EMAIL_KEYWORDS` hit **or** live conversation context: `last_listed` / `announced` / an in-flight
`pending_search` / a `last_read` email. Natural follow-ups ("yes, please", "the last one about X",
"who sent it?", "maybe three weeks ago") carry no keyword — this is why context opens the gate. A
classifier failure always resolves to `not_email`, so a hiccup never hijacks a turn.

`EmailManager.classify_intent(text, context_emails, active_search)` is one **Haiku** call returning a
fully-defaulted dict (`intent, account_hint, sender, subject_hint, timeframe, count, ordinal, days_back,
unread_only`). Intents: `not_email, check_new, check_now, list_emails, from_person, read_email,
email_question, load_more, summarize, action_needed, meetings_mentioned, send_email, dismiss`.
A `send_email` hit hands off to `classify_send` for recipient/message extraction — this classifier never
carries send fields (see "Sending email" below).
`context_emails` is rendered as a **numbered, newest-first list matching the caller's stored list exactly**
(the returned `ordinal` indexes into it). `active_search` tells the model a find is in progress so garbled
corrections are read as refinements instead of leaking to plain chat. `dismiss` and `load_more` are only
described to the model when a list was just presented.

**Per-conversation state lives in one `email_ctx` dict** (created once in `_run_conversation`):

| key | meaning |
|---|---|
| `last_listed` | exactly the emails **spoken aloud** (≤5). Ordinals resolve only against this. |
| `announced` | the batch a proactive announcement just offered. |
| `pending_search` | accumulated "find this email" criteria + `rounds`. |
| `last_read` | the email just read aloud — the referent for "who sent it?" |
| `account_focus` | which account the boss is browsing. |
| `oldest_shown` | paging anchor: the oldest email actually spoken. |

- **`pending_search`** accumulates `sender`/`subject_hint`/`timeframe`/`days_back` across turns, so repeating
  or adding detail *narrows* rather than restarting. A zero-match or unresolved round calls
  `_handle_unresolved_search_round`, which re-asks for **only what's still missing**, up to
  `MAX_SEARCH_REFINEMENT_ROUNDS = 3`, then offers a capped fallback ("want me to just list everything from
  Michael in the last month?"). A `not_email` aside does **not** clear it (resumable, no-trap); any *other*
  email intent does. Any topic-filtered find (`read_email`, or `list_emails`/`from_person` **with** a
  `subject_hint`) routes through the accumulator via `_handle_email_search`.
- **`account_focus`** — naming an account sets it, a follow-up with no account inherits it ("load earlier"
  stays on Gmail), a different account switches it, `all/both accounts` clears it. `check_new`/`check_now`
  are excluded: "how many unread" is an all-accounts question.
- **`oldest_shown` + `load_more`** — "load earlier emails" pages **older than the batch just spoken**, via
  `get_older_emails()` and the `before` date bound on `search()` (IMAP `BEFORE`, Gmail `before:`). Both
  providers are date-granular, so callers pass `anchor_date + 1 day` and then filter on the exact datetime.
  Without this, a deep search returns newest-first and just repeats what the boss already heard.

**Scope routing + deep search.** `get_emails_for_scope()` answers from the cache when the requested window
fits inside it, and otherwise runs a **live provider search**. `_scope_window_days`: `days_back` wins
outright; `this_week` is computed from the weekday (so Mon/Tue stay on the cache path). `needs_deep_search()`
is a pre-flight so a handler can speak a filler line *before* the seconds-long network call.
**Invariant: the deep-search path is stateless** — it never reads or writes the poll cache, `seen_ids`,
`unannounced_ids`, `last_error`, or announcement state, and holds no manager lock during network calls
(`store.list_all()` locks internally and returns deep copies). `data/email_cache.json` must stay
byte-identical across a deep search; there is a scratchpad check that asserts exactly that.

**Subject matching is token-overlap, not substring** (`EmailManager.subject_hint_matches`). A spoken
paraphrase ("KPI monthly") almost never appears as a contiguous substring of the real subject
("Monthly KPI Report"). Server-side `SUBJECT`/`subject:` is a best-effort prefilter only; when it returns
nothing, handlers re-query without it and apply the token match locally over the sender/time-narrowed set.

**TTS body safety (non-negotiable).** `sanitize_body_text()` in `email_fetch.py` strips Unicode category
`Cf` (all zero-width/BOM/bidi/soft-hyphen format chars), `Cc` controls except `\n`/`\t`, and explicitly
**U+034F COMBINING GRAPHEME JOINER** (category `Mn`, so no category rule catches it). It runs **twice**:
at fetch time on the body, and again **at read time** in `_read_email_aloud` — the single place any email
is read aloud. Read-time sanitization is what makes it impossible to bypass with a body cached before the
sanitizer existed. Feeding raw email bodies to Kokoro caused an apparent runaway/stuck TTS. Never add
another path that speaks a raw `body`.

**Model tiering.** Realtime Haiku (`INTENT_MODEL_ID`, `max_retries=0`): `classify_intent`, `classify_scope`,
`answer_email_question` (a one-sentence factual answer about the in-focus email — the boss is waiting),
plus the send path's `classify_send`, `classify_confirmation`, and `parse_spoken_email_address`.
Sonnet (`EXTRACTION_MODEL_ID`, imported from `memory.py`, also `max_retries=0` since the boss waits here
too, unlike memory extraction): `reason_over_emails`, `summarize_email`, and `draft_email` — heavier
batch/full-body/writing work. Handlers speak a filler line before the Sonnet calls.

### Sending email (`core/email_send.py` + `_handle_send_email` in `assistant.py`)

**A bad send cannot be undone.** That asymmetry — not symmetry with the read path — drives every design
choice here. The two failure shapes the reading build kept hitting (cross-turn state not surviving the
turn loop; the classifier confusing similar-sounding intents) are designed out structurally rather than
tested out:

- **The send flow is one self-contained sub-dialogue.** `_handle_send_email` drives its own speak/listen
  rounds and returns only once the email is sent, cancelled, or abandoned; it never hands control back to
  the turn loop mid-send. So the confirmation answer **never passes back through `classify_intent`** (no
  "send it" can be re-routed to `read_email`), and there is **no cross-turn `pending_send`** to lose or
  leak. `email_ctx` gains no new keys. The only state outliving the handler is the pending-contact slot,
  closed in a `finally` here *and* in `_run_conversation`'s.
- **`classify_send` is a separate Haiku call**, mirroring the `classify_scope` precedent, not new fields on
  `classify_intent`. That prompt is a heavily-tuned artifact of the reading build with an "exactly these
  keys" contract; keeping send extraction out of it means a send change can never regress reading. It runs
  only *after* `classify_intent` returns `send_email`, so a read turn pays nothing.
- **`classify_confirmation` fails closed.** It returns `yes`/`no`/`revise`/`unclear`, and **only an
  unambiguous, unqualified affirmative returns `yes`**. Silence short-circuits to `unclear` with no API
  call; `"yeah, no"`, `"sure I guess"`, `"wait"`, and `"yes but make it shorter"` (→ `revise`) all resolve
  to something that does not send. An exception resolves to `unclear`. **There is no path from `unclear`
  to a send** — one blunt "yes or no" re-ask, then cancel.
- **The readback is derived in Python from the validated address** (`_spell_email_for_speech`:
  `feras@codex.com` → `"f-e-r-a-s at codex dot com"`), never from the model's transcription of what it
  thought it heard — reading that back would confirm nothing. `parse_spoken_email_address` returns `None`
  rather than a guess; every stored address passes `memory.is_valid_email`.

Flow: `classify_send` → resolve recipient → resolve account → `draft_email` (Sonnet) → **mandatory
confirmation** → `send_email`. Every loop is bounded (`MAX_ADDRESS_ROUNDS`, `MAX_SEND_REVISION_ROUNDS`,
`MAX_SEND_CONFIRM_ROUNDS`) and every bound exits toward *not sending*. A `revise` verdict re-drafts with
`prior_draft` + `revision` (editing, never restarting) and re-confirms. Failures are always spoken; the
raw exception never is (`_describe_send_failure` — SMTP/Gmail errors carry hosts, usernames, and URLs).

**Listen windows.** `_ask_and_listen` takes a keyword-only `silence_timeout` (default
`FOLLOWUP_SILENCE_TIMEOUT`). The send confirmation and address entry pass
`SEND_CONFIRM_SILENCE_TIMEOUT`/`ADDRESS_ENTRY_SILENCE_TIMEOUT` = **15s**, the longest in the codebase:
hearing out a whole drafted email and deciding is thinking, not reacting, and a short
window would turn "still deciding" into silence, which cancels.

**Recipient resolution.** From memory's people list (`find_people_by_name`: exact full-name match wins
outright; else anchored per-token prefix; else a single-token surname match — more than one survivor means
Lana asks which). A **reply** resolves against `email_ctx["last_read"]` — the same referent "who sent
it?" uses, and deliberately the *only* reply mechanism; there is no parallel tracking of "the email being
discussed". `_speak_draft` must **never** route through `_read_email_aloud`: that is the received-mail read
path and would clobber `last_read` and `pending_search` mid-send, destroying the very referent the reply
was resolved from.

**Address entry, voice or typed.** Addresses are hard for STT (symbols, dots, spelling). The default is
spoken + spelled-back-for-confirmation. As an **optional** alternative, `open_contact_request(name)` opens
a single TTL'd slot on the `EmailManager` singleton (same shape as the OAuth nonce store;
`claim_contact_email()` is the same atomic-pop idea as `claim_announcement()`), the dashboard fulfils it via
`POST /email/pending-contact`, and the voice loop claims it between listen rounds. A typed address needs no
readback — it wasn't transcribed. The voice path never waits on the UI. A confirmed address is saved via
`memory.set_person_email`. Emits `contact_email_requested`/`contact_email_resolved`.

**Transport (`email_send.py`).** `PROVIDER_SENDERS` maps provider → sender (`SmtpSender` via stdlib
`smtplib`, `GmailSender` via `messages().send()` and the `gmail.send` scope granted since day one), exactly
mirroring `PROVIDER_FETCHERS`. It is a **separate module from `email_fetch.py` on purpose**, so that file's
read-only invariant (`select_folder(readonly=True)` is load-bearing) stays legible. `build_gmail_service()`
is module-level in `email_fetch.py` so both paths share one way of using the one OAuth grant.
**Invariant: the send path is stateless** — `EmailManager.send_email` never reads or writes the poll cache,
`seen_ids`, `unannounced_ids`, `last_error`, or announcement state, and holds no manager lock across the
network call. `data/email_cache.json` must stay byte-identical across a send; there is a scratchpad check
that asserts exactly that.

**Memory's `email` field.** People are `{name, notes, email}`. Old `memory.json` files have no `email` key
and load unchanged (`load()` shallow-back-fills top-level keys only; every read uses `.get()`).
`_upsert_person_locked` is the single writer, shared by `_merge_extracted` and `set_person_email`.
**The extraction prompt is never told the `email` field exists** — so the Sonnet extractor can't be
prompted into *inventing* an address. But it still surfaces real addresses the boss states out loud, with
nowhere to put them but the `notes` prose; `_split_email_from_notes` lifts a *valid* address out of
incoming notes into the field at merge time, **empty-slot-only** (it never displaces an address the send
flow confirmed, since a lift passes `overwrite_email=False`). `load()` runs the same lift once over
existing people (`_normalize_people_emails_locked`) to self-heal entries written before this existed. Net
invariant: an address is only ever *written* from an explicit confirmed `set_person_email`, or *lifted*
from something the model already transcribed — never conjured by the extractor. Memory is what the send
flow resolves recipients from, so this stays strict.

**LLM account awareness.** Conversational meta-questions ("how many accounts?", "do you have my Gmail?")
have no email keyword and reach plain Claude, which used to confabulate ("it's still syncing"). So
`EmailManager.get_accounts_context()` is passed to `LLMEngine.get_response(email_context=...)` **fresh
every turn** (a cheap in-memory read), and states the accounts are connected and ready. **Adding an account
needs no restart:** `add_*_account` mutates the in-memory store and calls `request_poll_soon()`. The
`load()`/`_load_cache()` you see at startup are restore-from-disk only.

**Gmail OAuth.** `/oauth-url` (bearer-authed) mints a **one-time, in-memory, 10-minute `state` nonce**;
the callback can't carry a bearer header, so validating that nonce is what protects it — and with
`LANA_API_TOKEN` unset no nonce can be minted, so the whole chain fails closed. Consequences: the URL
**must be opened from the same running server process**, and a restart invalidates pending nonces
(`scratchpad/get_gmail_url.py` mints one against the running server). `/oauth-url` also self-verifies that
the generated URL actually carries both scopes before returning it, failing loud rather than letting Google
reject it later. Scopes requested: `gmail.readonly` + `gmail.send` — both are now used; because `send` was
requested from the first version of this flow, already-connected accounts never needed re-consent. The
Gmail API must be enabled in the Google Cloud project, or the first API call fails with an `HttpError`.

### Windows CUDA DLL loading

`core/stt.py` (transitively, via `faster_whisper`) needs `cublas`/`cudnn` DLLs that pip installs into
`site-packages/nvidia/*/bin` but Windows won't find via PATH — it manually calls `os.add_dll_directory()`
on every `nvidia/*/bin` folder before importing the CUDA-dependent library. If you add a new module that
needs CUDA, this same shim (see top of `core/stt.py`) is required. `core/wake_word.py` does *not* need
this — openwakeword runs via `onnxruntime` on CPU only, which is intentional: the detector is idle,
listening, almost all the time, so there's no latency benefit to putting it on the GPU worth the added
complexity.

Both `STTEngine.load_model()`/`_transcribe()` and `TTSEngine.initialize()` catch CUDA load/inference
failures and transparently fall back to CPU (`int8` compute for Whisper) — don't remove these fallbacks
without a reason; they're what keeps the assistant working on machines/configs where the CUDA DLLs
fail to load mid-session.

### Configuration (`config.py`)

All tunables are env vars loaded via `python-dotenv`, with defaults baked into `config.py` — there is no
separate settings schema/validation layer. New settings should follow the same pattern: add to
`config.py` with an `os.environ.get(..., default)`, and document the var in `.env.example`.

Note `core/llm.py` hardcodes its own `MODEL_ID` (`claude-haiku-4-5-20251001`) rather than reading
`config.CLAUDE_MODEL` — Haiku is used deliberately for voice-latency reasons (see the comment in that
file). Don't casually swap in `config.CLAUDE_MODEL` without checking latency implications.

`LLMEngine.get_response(user_text, memory_context="", email_context="")` appends each non-empty context
under its own labelled header for that call only; `self._system_prompt` is never mutated.

Email-related settings (all in `config.py` + `.env.example`): `GOOGLE_OAUTH_CLIENT_ID`,
`GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REDIRECT_BASE` (default `http://localhost:8000`), and
`EMAIL_POLL_INTERVAL_S` (default `240`). IMAP-only accounts work without the Google vars. Everything else
that tunes email behavior is a **module-level constant**, not an env var — per house convention:
`FETCH_WINDOW_DAYS`/`FETCH_MAX_MESSAGES`/`BODY_CHAR_LIMIT` (`email_fetch.py`),
`SMTP_DEFAULT_SSL_PORT`/`SMTP_DEFAULT_STARTTLS_PORT`/`SMTP_TIMEOUT_S` (`email_send.py`),
`CACHED_EMAILS_PER_ACCOUNT`/`DEEP_SEARCH_MAX_DAYS`/`DEEP_SEARCH_MAX_MESSAGES`/`ANNOUNCE_MAX_AGE_S`/
`OAUTH_STATE_TTL_S`/`CONTACT_REQUEST_TTL_S`/`DRAFT_MAX_TOKENS` (`email_manager.py`),
`MAX_SEARCH_REFINEMENT_ROUNDS`/`EMAIL_KEYWORDS`/`SEND_CONFIRM_SILENCE_TIMEOUT`/
`ADDRESS_ENTRY_SILENCE_TIMEOUT`/`MAX_ADDRESS_ROUNDS`/`MAX_SEND_REVISION_ROUNDS`/
`MAX_SEND_CONFIRM_ROUNDS` (`assistant.py`). **Sending needs no new env vars** — SMTP host/port are derived
or stored per-account, and Gmail reuses the existing OAuth config.

### Silence/VAD logic (`core/stt.py`)

Recording length is controlled by RMS-based silence detection, not a fixed duration: it calibrates an
ambient noise floor for `CALIBRATION_S`, then stops recording after `SILENCE_DURATION_S` of
below-threshold audio (or `MAX_DURATION_S` as a hard cap). The `initial_silence_timeout` parameter is
separate from `SILENCE_DURATION_S`: it bounds how long to wait for the user to *start* speaking before
giving up, letting follow-up turns use a longer "are you still there" window without changing
trailing-silence behavior once speech begins.

## Editing checklist

- Anything touching WebSocket event payloads or REST responses should stay consistent with
  [API.md](API.md).
- Anything touching env vars should be reflected in both `config.py` and `.env.example`.
- New engines/pipeline stages should follow the existing per-module convention: a class with an
  explicit `initialize()`/`load_model()` step separate from construction, and a `__main__` block for
  standalone manual testing.
- New heavier/non-realtime LLM tasks (background summarization, future briefings, etc.) should use
  `core/memory.py`'s `EXTRACTION_MODEL_ID` tier, not `core/llm.py`'s realtime Haiku model.

Email-specific:

- **Never speak a raw email `body`.** Route every read-aloud through `_read_email_aloud`, which
  sanitizes. Adding a second read path is how the invisible-Unicode/stuck-TTS bug comes back.
- **But never speak a *draft* through `_read_email_aloud`.** It is the received-mail path: it clears
  `pending_search` and overwrites `last_read`, which mid-send destroys the referent a reply was resolved
  from. `_speak_draft` sanitizes its own parts instead.
- **Nothing sends without an explicit `CONFIRM_YES`.** If you touch `classify_confirmation` or
  `_confirm_and_send`, re-run `scratchpad/send_classifier_check.py` (3× repeats — live Haiku has real
  variance, and a 1-in-3 flake here is a 1-in-3 chance of sending an email the boss never approved).
  Silence, garble, and "yes, but…" must never reach the network.
- **Never log credentials, tokens, refresh tokens, or OAuth codes**, and never return them from an
  endpoint. `list_safe()` is the only account view the API layer may use; `list_all()`/`get()` are for
  the fetch/send path only. Don't log an email `body`, and don't speak a raw exception from a failed send
  (`_describe_send_failure` exists because SMTP and Gmail errors carry hosts, usernames, and URLs).
- **The extraction prompt must never learn the `email` field exists** — being asked to produce an address
  is what invites a hallucinated one, and a hallucinated address is a misdirected, unrecallable send. A
  real address the extractor transcribes into `notes` is lifted into the field by `_split_email_from_notes`
  (empty-slot-only, never over a confirmed address); direct writes come only from `set_person_email`.
- **A new voice email behavior is a new intent + handler**, not a new keyword in `EMAIL_KEYWORDS`. Add
  it to `classify_intent`'s prompt *and* its allowed-intent validation, and note whether it is
  context-gated (only described to the model when a list/read is in scope, like `dismiss`/`load_more`).
- **Cross-turn state goes in `email_ctx`**, never on `self` — `_run_conversation` owns it, so it dies
  with the conversation. If a new key changes which emails are in scope, decide explicitly whether an
  account switch should invalidate it (as `oldest_shown` does).
- **The deep-search and send paths must stay stateless** w.r.t. the poll cache/announcement state, and no
  manager lock may be held across a network call.
- **The send flow is a self-contained sub-dialogue.** Keep it that way: don't spill it into `email_ctx` or
  across turns. The moment a confirmation answer round-trips through `classify_intent`, "send it" becomes
  re-routable — which is the exact bug class this design exists to prevent.
- Verify email changes with a **scratchpad drive script** that exercises the real handler with
  STT/TTS/classifier stubbed. Reading the code has repeatedly missed the actual bug. For sending, the
  existing set is `send_drive.py` (handler), `send_classifier_check.py` (adversarial, 3×),
  `typed_contact_drive.py` (cross-thread slot + live endpoints), and `reading_regression_check.py`
  (the reading side + the cache-byte-identical assert).
