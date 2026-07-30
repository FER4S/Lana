# Lana API Reference

Lana is a voice assistant that runs entirely on one Windows machine. A clinician
speaks to it; it listens, answers, reads and sends email, and drafts treatment
plans. This document is the complete integration contract for a frontend built
against it.

**Read [The event stream has no history](#the-event-stream-has-no-history) before
you write any code.** It is the one design property that will cost you a rewrite
if you discover it late.

## Contents

- [Orientation](#orientation)
- [Base URL and schema](#base-url-and-schema)
- [Authentication](#authentication)
- [The event stream has no history](#the-event-stream-has-no-history)
- [Conventions: timestamps, errors, CORS](#conventions-timestamps-errors-cors)
- [REST: meta and control](#rest-meta-and-control)
- [REST: email](#rest-email)
- [REST: treatment plans](#rest-treatment-plans)
- [WebSocket: `/events`](#websocket-events)
- [React integration](#react-integration)
- [What this API cannot do](#what-this-api-cannot-do)

---

## Orientation

Everything Lana *does* is driven by voice. The user says a wake word, speaks, and
Lana replies out loud. Reading email, searching it, sending it, drafting a
treatment plan, remembering a fact — all of it happens in that spoken
conversation.

This API is therefore mostly a **window onto that conversation**, plus setup and
retrieval around the edges. Concretely, a frontend can:

| Capability | Surface |
|---|---|
| Turn the voice pipeline on and off | `POST /start`, `POST /stop` |
| Watch everything happen live — transcript, replies, plan progress | `WS /events` |
| Connect, list and remove email accounts | `/email/accounts…` |
| Show unread counts and recent mail | `GET /email/summary` |
| List and read saved treatment-plan drafts | `GET /plans`, `GET /plans/{filename}` |
| Type a recipient's address when Lana asks for one aloud | `POST /email/pending-contact` |

There is no endpoint that makes Lana *do* something beyond starting and stopping
her. See [What this API cannot do](#what-this-api-cannot-do) for the full
boundary — it is worth reading before you design screens.

### The pieces that run independently

Three things have separate lifecycles, which explains some otherwise surprising
behaviour:

- **The voice pipeline** — started by `POST /start` (and automatically when the
  backend process launches). Stopping it stops the microphone, nothing else.
- **The email poller** — starts with the *server* and keeps running across
  `POST /stop`. So `GET /email/summary` stays fresh while the assistant is
  stopped. Only shutting the backend down stops it.
- **The HTTP/WebSocket server** — always up while the process is.

---

## Base URL and schema

```
http://127.0.0.1:8000
ws://127.0.0.1:8000
```

The host and port are configurable (`SERVER_HOST`, `SERVER_PORT`), and the
backend binds to `127.0.0.1` by default — **loopback only**. A frontend must run
on the same machine unless the operator deliberately changes that binding.

A machine-readable schema is served, unauthenticated, at:

- `/openapi.json` — OpenAPI 3.1, usable for client generation
- `/docs` — Swagger UI
- `/redoc` — ReDoc

Two caveats if you generate a client from `/openapi.json`:

1. **The WebSocket is not in the schema.** OpenAPI cannot express it. Everything
   under [WebSocket](#websocket-events) is hand-written and authoritative.
2. **Auth is not declared as a security scheme.** Protected routes list
   `authorization` as an ordinary optional header parameter, so a generated
   client will not know it is required, and will not know that omitting it yields
   `401`. Wire the header yourself.

There is no version prefix in any path. The application version (`0.1.0`) appears
only in `/openapi.json`. Treat this API as unversioned and pinned to the backend
build you are given.

`/` redirects (307) to `/ui/`, a small built-in dashboard the backend serves for
local testing. It is static, unauthenticated, contains no data, and is **not part
of your integration surface** — mentioned only so you know what is answering
there and do not mistake it for an API route.

---

## Authentication

Every data and control endpoint requires a shared secret token. Auth **fails
closed**: if the backend has no token configured, those endpoints reject
*everything* rather than falling back to open access.

The operator generates the token once and puts it in the backend's `.env`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

```
LANA_API_TOKEN=<the generated value>
```

Your frontend must be configured with the same value.

| Surface | How to send it | On failure |
|---|---|---|
| `POST /start`, `POST /stop` | `Authorization: Bearer <token>` header | `401` `{"detail": "Missing or invalid API token."}` |
| All `/email/*` except the OAuth callback | `Authorization: Bearer <token>` header | `401`, same body |
| All `/plans/*` | `Authorization: Bearer <token>` header | `401`, same body |
| `WS /events` | `?token=<token>` query param — browsers cannot set headers on a WebSocket handshake | Handshake rejected with **HTTP 403**; see [below](#when-the-token-is-wrong) |
| `GET /email/accounts/gmail/oauth-callback` | Not token-protected. Google's redirect is a plain browser GET that cannot carry a header. Protected instead by a one-time `state` nonce minted by the token-protected `GET /email/accounts/gmail/oauth-url` — so with no token configured, no nonce can be minted and the whole OAuth flow still fails closed. | `403` HTML page |
| `GET /health`, `GET /status` | No token needed | — |
| `/`, `/ui/*`, `/openapi.json`, `/docs`, `/redoc` | No token needed (static assets and schema; no data) | — |

Notes:

- The scheme match is case-insensitive: `Bearer`, `bearer` and `BEARER` all work.
- A token in a query string is **not** accepted on REST endpoints — only on the
  WebSocket, which has no alternative. Use the header everywhere else.
- Comparison is constant-time. There is no rate limiting, lockout, or
  per-client identity: one token is shared by every consumer.

---

## The event stream has no history

`WS /events` is a **live broadcast with no buffer, no backlog, and no replay.**

Internally, events go onto a single queue that one broadcaster drains and fans
out to whoever is connected *at that instant*. If no client is connected, the
event is consumed and **discarded**. If your client is mid-reconnect, the events
in that window are gone permanently.

Verified behaviour:

- Two or more clients connected simultaneously each receive every event. Your app
  and the built-in dashboard can both be open.
- An event emitted while nobody is connected is never delivered to a client that
  connects afterwards — there is no replay.
- There is no sequence number, no event id, and no "catch me up" endpoint.

### What this means for you

1. **Connect the socket before you need it,** and keep it connected for the whole
   session. Do not open it lazily when a screen mounts.
2. **Treat the socket as the only source of conversation transcript.** Nothing
   else exposes it. If you need transcript history to survive a refresh, persist
   it on your side as events arrive.
3. **On reconnect, resynchronise from REST** — `GET /status` for pipeline state
   and `GET /plans` for anything saved. You cannot recover the transcript of the
   gap.
4. **`GET /status` is not a snapshot of an in-flight conversation.** It gives one
   coarse value (`idle` / `listening` / `thinking` / `speaking`) and the last
   component error. It will not tell you a treatment plan is halfway through
   drafting.

---

## Conventions: timestamps, errors, CORS

### Timestamps

Every timestamp — `created_at`, `date`, `last_poll`, `saved_at` — is ISO 8601
**with a UTC offset, in the server's local timezone**, which is `Asia/Beirut`,
hardcoded in the backend and not configurable by environment variable:

```
2026-07-29T09:35:00+03:00
```

These are **not UTC** and never carry a trailing `Z`. `new Date(value)` parses
them correctly because the offset is present, but any code that assumes UTC, or
strips the offset, or reformats by slicing the string, will be wrong by hours.

### Error responses

| Status | Meaning | Body |
|---|---|---|
| `400` | Semantically invalid input the handler rejected | `{"detail": "<human-readable message>"}` |
| `401` | Missing or wrong token | `{"detail": "Missing or invalid API token."}` |
| `403` | Invalid/expired/reused Gmail OAuth `state`, or a rejected WebSocket handshake | HTML page (OAuth) / no body (WebSocket) |
| `404` | Unknown account id, or a plan filename that isn't one we wrote | `{"detail": "<message>"}` |
| `409` | Conflicts with current state (nothing is pending) | `{"detail": "<message>"}` |
| `422` | Request body failed schema validation | FastAPI's array of field errors — **not** a string |
| `500` | Server-side failure (e.g. a saved draft could not be read from disk) | `{"detail": "<message>"}` |
| `503` | A required backend feature is not configured (Gmail OAuth) | `{"detail": "<message>"}` |

**`422` is shaped differently from every other error.** `detail` is an array, not
a string:

```json
{"detail": [{"type": "missing", "loc": ["body", "password"],
             "msg": "Field required", "input": {"label": "x"}}]}
```

Error-handling code that does `err.detail.toUpperCase()` or renders `detail`
directly will break on `422`. Branch on the status, or check whether `detail` is
an array.

Every constraint that produces a `422` is listed with its endpoint below.

### CORS

The backend allows all origins, all methods, all headers, with credentials
**off**. It uses no cookies — the bearer token is the only credential, so a
wildcard origin does not weaken it. Because you send a custom `Authorization`
header, browsers will issue a preflight `OPTIONS` on most calls; this is handled.

Since CORS does not apply to WebSockets at all, the `?token=` query param is the
only thing protecting `/events`.

---

## REST: meta and control

### `GET /health`

Liveness probe. No token required. The body is fixed:

```json
{"status": "ok", "service": "lana"}
```

### `GET /status`

Pipeline state. No token required.

```json
{"running": true, "state": "idle", "error": null}
```

| Field | Notes |
|---|---|
| `running` | `true` for the *entire* pipeline lifetime — from the moment startup begins (loading speech models, which takes seconds) until shutdown fully completes. Not "is listening right now". |
| `state` | Exactly one of `idle`, `listening`, `thinking`, `speaking`. |
| `error` | `null` when healthy. Otherwise a short human-readable reason a component died unrecoverably — currently only the wake-word microphone failing to open or dying mid-stream. The process is still up, but Lana cannot hear her wake word until it is fixed. Clears automatically when the detector next starts successfully. The same string arrives on the socket as an [`error`](#event-reference) event. |

Because `running` covers startup, a `POST /start` sent immediately after a
`POST /stop` may report `already running` while the previous run winds down. Poll
`/status` until `running` is `false`, then retry.

### `POST /start`

Starts the voice pipeline in a background thread. Requires the bearer token.

Two possible **200** responses — both are success, neither is an error:

```json
{"status": "started"}
```

```json
{"status": "already running"}
```

Do not treat a response other than `"started"` as a failure. `"already running"`
means the pipeline is up, which is what you wanted.

### `POST /stop`

Stops the voice pipeline cleanly. Requires the bearer token. Symmetrically, two
**200** responses:

```json
{"status": "stopped"}
```

```json
{"status": "already stopped"}
```

This does **not** stop the email poller — `GET /email/summary` keeps updating.

---

## REST: email

Eight endpoints: account management, a dashboard summary, and one optional assist
for the voice send flow. Reading, searching, summarising and **sending** mail all
happen by voice and have no REST equivalent.

**Nothing here sends email.** The only way a message leaves the machine is for
the drafted email to be read aloud in full and then approved out loud.
`POST /email/pending-contact` supplies a recipient's *address* when Lana asks for
one; it sends nothing by itself.

### `POST /email/accounts/imap`

Adds a standard IMAP account. The IMAP connection is validated live before
anything is stored — on failure nothing is saved.

The `smtp_*` fields are optional. Left out, the outgoing host is derived from
`host` at send time (`imap.example.com` → `smtp.example.com`, port 465, implicit
TLS), so an account added for reading can later send with no changes. When
`smtp_host` *is* supplied, the SMTP login is validated live too — so adding a
read-only mailbox never fails on an outgoing server it will never use.

```json
{
  "label": "Clinic Support",
  "host": "imap.example.com",
  "port": 993,
  "username": "support@example.com",
  "password": "the-mailbox-password",
  "use_ssl": true,

  "smtp_host": "smtp.example.com",
  "smtp_port": 465,
  "smtp_use_ssl": true
}
```

Validation (all produce `422`): `label` required, 1–60 characters; `host`
required, non-empty; `username` and `password` required, non-empty; `port` and
`smtp_port` integers in 1–65535. `port` defaults to 993, `use_ssl` and
`smtp_use_ssl` to `true`.

**200:**

```json
{"account": {"id": "3f9a1c2b4e5d4f6a8b9c0d1e2f3a4b5c",
             "label": "Clinic Support", "provider": "imap",
             "created_at": "2026-07-07T09:15:00+03:00"}}
```

**400** — the connection or login failed:
`{"detail": "Login failed — check the username and password."}`. The raw server
error is deliberately never echoed back (IMAP banners leak host detail), so the
message is one of a small fixed set. Show it verbatim.

Note this call blocks for as long as the IMAP handshake takes, and can take
several seconds against a slow or unreachable host.

### `GET /email/accounts/gmail/oauth-url`

Returns a Google consent URL to open in a browser, requesting `gmail.readonly`
and `gmail.send`. Also mints the single-use `state` nonce the callback requires.

```json
{"url": "https://accounts.google.com/o/oauth2/auth?...&state=...",
 "expires_in": 600}
```

**How to use `url` — this is where integrations go wrong:**

- **Open it verbatim.** It is ~400 characters. Truncating, re-encoding, or
  re-adding query params yourself produces Google's confusing
  `Missing required parameter: scope` and `invalid client_id` errors. Hand the
  whole string to the browser.
- **The nonce is in-memory, single-use, and expires in `expires_in` seconds
  (600).** It lives in the running backend process. So: fetch a fresh `url` at
  the moment the user clicks "Connect Gmail"; never cache one; never persist one
  across a backend restart (a restart invalidates every pending URL); and after a
  failed attempt fetch a new one rather than reusing it. A stale, reused, or
  expired nonce is exactly what produces the `403` page on the callback.

**503** — `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` are not set in
the backend `.env`. Surface as "Gmail sign-in isn't set up on this machine". Not
a user error; retrying will not help. IMAP accounts work without these.

**500** — the backend built a consent URL missing its scopes and refused to hand
it out (a broken OAuth library install). Never expected in a healthy deployment;
treat as a backend bug, not something to retry.

### `GET /email/accounts/gmail/oauth-callback`

Google redirects the user's browser here after consent, with `code` and `state`
query params (or `error` if they cancelled). Exchanges the code, looks up the
address, stores the account, and returns a small human-readable **HTML** page.

**There is nothing here for a frontend to call or parse** — the user just closes
the tab. It is documented so you recognise it, not so you use it.

- `403` — the `state` nonce was invalid, already used, or older than 10 minutes.
  The fix is always "fetch a fresh consent URL", never "retry this link".
- `400` — no authorization code arrived, or the token exchange failed. One common
  operator-side cause: the **Gmail API is not enabled** in the Google Cloud
  project that owns the OAuth client. Consent succeeds and then the callback
  fails. Nothing is stored.
- If Google returns no refresh token (possible on a repeat consent without first
  revoking access), nothing is stored and the page explains how to revoke and
  retry.

A newly connected account is usable **immediately, with no backend restart** —
the voice assistant and `GET /email/summary` both see it, and its mail lands in
the cache within seconds.

### `GET /email/accounts`

Connected accounts. Credentials and tokens are never included here or anywhere
else.

```json
{"accounts": [
  {"id": "3f9a1c2b4e5d4f6a8b9c0d1e2f3a4b5c", "label": "Clinic Support",
   "provider": "imap", "created_at": "2026-07-07T09:15:00+03:00"},
  {"id": "7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f", "label": "user@gmail.com",
   "provider": "gmail_oauth", "created_at": "2026-07-07T09:20:00+03:00"}
]}
```

Those four keys are the whole object. `provider` is `imap` or `gmail_oauth`.

### `DELETE /email/accounts/{account_id}`

Removes an account and its cached mail. **200** `{"status": "deleted"}`;
**404** `{"detail": "No email account with that id."}`.

### `GET /email/summary`

Unread counts and recent mail per account. Answered from a local cache the
background poller maintains — not a live fetch, so it returns instantly and is
safe to poll. Every 30–60s is plenty; the backend itself refreshes every
`EMAIL_POLL_INTERVAL_S` (240s by default).

```json
{
  "accounts": [
    {
      "id": "3f9a1c2b4e5d4f6a8b9c0d1e2f3a4b5c",
      "label": "Clinic Support",
      "provider": "imap",
      "unread_count": 2922,
      "last_poll": "2026-07-07T09:40:00+03:00",
      "last_error": null,
      "recent": [
        {"subject": "Ticket #4521: Refund request",
         "sender_name": "Jane Doe", "sender_email": "jane@example.com",
         "date": "2026-07-07T09:35:00+03:00", "unread": true,
         "snippet": "Hi, I'd like to request a refund for..."}
      ]
    }
  ],
  "total_unread": 2922
}
```

> #### `unread_count` and `recent` describe different sets of email
>
> This is the most misread part of this API. Do not build UI implying they match
> — "2,922 unread" above a list of four items reading as "here they are" is
> wrong.
>
> - `unread_count` is the **provider's total unread count** for the whole inbox.
>   It can be in the thousands for a neglected mailbox.
> - `recent` is at most **10 items from the local cache**, and the cache only
>   holds the **last 2 days**, up to 25 messages per account — **read as well as
>   unread**.
> - So `recent.length` has no relationship to `unread_count`, `recent` contains
>   already-read mail, and most unread mail is not in `recent` at all.
> - **A large count beside a short list is correct.** Label them separately, e.g.
>   "2,922 unread total" and "Last 2 days".
> - There is **no full-inbox sync** and nothing is ever "still importing" — do
>   not show a progress indicator. Older mail is not cached; it is fetched live,
>   on demand, **by voice only**.

Per-account fields: `last_poll` is `null` until that account's first poll
completes (a few seconds after it is added). `last_error` is `null` when healthy,
otherwise a short reason that account's last poll failed (bad password, host
unreachable, revoked token). Other accounts keep working, so **show it per row,
not as a global banner**.

Each `recent` item has exactly those six keys. Message bodies and ids are
deliberately not exposed.

### `GET /email/pending-contact`

Whether Lana is currently waiting for a recipient's email address, and whose.

When the voice send flow needs an address it does not have, it asks aloud **and**
opens this request, so a frontend can offer a text box instead — email addresses
are hard for speech recognition to get right. The `contact_email_requested`
socket event fires at the same moment; use that rather than polling.

```json
{"pending": {"name": "Michael", "expires_in": 142}}
```

```json
{"pending": null}
```

**Entirely optional.** The voice flow never waits on it: if no frontend is open,
the user simply speaks the address and Lana spells it back for confirmation. At
most one request is open at a time, it expires after 180 seconds, and it is
always closed when the conversation ends.

### `POST /email/pending-contact`

Fulfils the open request with a typed address. The voice loop picks it up on its
next round and uses it verbatim — no spoken read-back is needed, because it was
never transcribed.

```json
{"email": "michael@example.com"}
```

`email` is required, 3–254 characters (`422` outside that range).

**202** `{"status": "accepted"}`.

- `400` `{"detail": "That doesn't look like a valid email address."}` — malformed.
  Shape is checked *before* anything else, so a request that expires mid-flight is
  never misreported as a bad address.
- `409` `{"detail": "Lana isn't waiting for a contact's email address."}` —
  nothing pending, or it expired.

Submitting again before the value is claimed **replaces** it, so a typo caught in
time does the right thing. Claiming is single-use but leaves the request open, so
a second attempt after a mistake does not `409`.

**This sends nothing.** The drafted email is still read aloud in full and must be
approved out loud. A confirmed address is saved against that person, so the next
email to them needs no spelling.

---

## REST: treatment plans

Read-only, all three. Drafts are produced by the voice sub-dialogue and by
nothing else: there is no endpoint that creates, edits, approves, sends or
deletes one. All three require the bearer token — these files are patient data.

### `GET /plans/knowledge`

Provenance for the clinical corpus behind every draft.

```json
{
  "available": true,
  "unavailable_reason": "",
  "doc_count": 9,
  "doc_names": ["01_intake.md", "04_gut_repair_5r.md"],
  "rule_count": 76,
  "referral_rule_count": 18,
  "contraindication_rule_count": 58,
  "rules_version": 1,
  "corpus_hash": "0288c8e7fecd",
  "reviewed": false,
  "review_status": "PENDING — …"
}
```

When the corpus failed to load, only `{"available": false, "unavailable_reason":
"…"}` is returned — check `available` before reading any other field.

**`reviewed` refers to the rulebook, not to any individual draft.** It is `false`
until the reviewing clinician signs off on the safety rules. **Display it rather
than hiding it** — the rendered documents carry the same notice, and the review
status is information the reviewer needs, not a blemish to suppress.

### `GET /plans`

Metadata for every saved draft and referral memo, newest first.

```json
{"plans": [{"filename": "DRAFT_plan_nadia_20260723-174826.md",
            "kind": "plan", "patient_label": "nadia",
            "saved_at": "2026-07-23T17:48:26+03:00", "size": 5312}]}
```

- Parsed **from the filename** — the documents are not opened, so this is cheap.
- `kind` is `plan` or `referral_memo`.
- `patient_label` is a slug derived from the spoken patient name. **It is not a
  patient identifier** and cannot be reliably joined to a record in another
  system — see [What this API cannot do](#what-this-api-cannot-do).
- `size` is bytes.
- Ordering is filename-descending, which is newest-first because the timestamp is
  in the name.
- **No pagination and no filtering.** The list returns everything and grows
  without bound. Page or filter client-side.

### `GET /plans/{filename}`

The raw markdown of one draft, as `text/plain; charset=utf-8` — never
`text/html`, because the document contains model-generated prose. Render it
through a markdown renderer you control; do not inject it as HTML.

`filename` must match `DRAFT_(plan|referral_memo)_<slug>_<YYYYmmdd-HHMMSS>.md`.
Anything else, and anything resolving outside the plans directory, returns
**404** `{"detail": "No saved draft with that name."}`. **500** means the file
matched but could not be read from disk.

Every document carries a review banner at the **top and bottom**, written by the
backend in code where no model output can drop it — a blockquote beginning
`⚠ DRAFT — PENDING ALEXANDRA'S REVIEW` for a plan, or
`⚠ REFERRAL NOTE — DRAFT, PENDING ALEXANDRA'S REVIEW` for a referral memo. Do not
strip them when rendering. No draft has been clinically reviewed, and none should
reach a patient before it has been.

---

## WebSocket: `/events`

```
ws://127.0.0.1:8000/events?token=<token>
```

Server-to-client broadcast of every pipeline transition. Re-read
[The event stream has no history](#the-event-stream-has-no-history) — there is no
replay.

### When the token is wrong

The backend rejects the connection **before accepting the WebSocket upgrade**, so
what fails is the HTTP handshake:

- The server responds **HTTP 403**.
- A browser `WebSocket` therefore fires `onerror` and then `onclose` with code
  **1006** (abnormal closure) — the browser cannot expose the HTTP status to
  JavaScript.

So **a `1006` close on first connect almost always means a bad or missing
token**, not a network fault. Do not retry a `1006` on the initial connect in a
tight loop; check the token first. Server-side libraries (Python `websockets`,
Node `ws`) do surface the real `403`.

### Sending to the server

**Don't.** The socket is one-directional. The endpoint reads incoming text purely
to notice that you disconnected, and discards it. Two consequences:

- Sending **text** is harmless but pointless — there is no message the backend
  acts on.
- Sending a **binary** frame kills the connection. The server reads text only, so
  a binary frame raises internally and the socket drops. If your client library
  can be configured to heartbeat with binary payloads, turn that off.

There is no application-level ping/pong. Rely on the transport's own keepalive.

### Reconnecting

Reconnect with exponential backoff (e.g. 1s, doubling, capped around 30s) plus
jitter. On every successful reconnect:

1. `GET /status` — the pipeline may have started or stopped while you were away.
2. `GET /plans` — a draft may have been saved.
3. Accept that the transcript for the gap is unrecoverable, and show that
   honestly rather than implying continuity.

### Event reference

Every message is a JSON object with an `"event"` field plus any payload keys.

| Event | Description | Payload example |
|---|---|---|
| `wake_word_detected` | The wake word fired; a conversation is starting. | `{"event": "wake_word_detected"}` |
| `listening_started` | The microphone started recording. | `{"event": "listening_started"}` |
| `transcription` | Speech-to-text finished on one utterance. | `{"event": "transcription", "text": "Hello Lana"}` |
| `llm_response` | Lana produced a reply (also used for her fixed spoken lines and confirmations). | `{"event": "llm_response", "text": "Hello! How can I help?"}` |
| `speaking_started` | Text-to-speech playback began. | `{"event": "speaking_started"}` |
| `speaking_ended` | Text-to-speech playback finished. | `{"event": "speaking_ended"}` |
| `idle` | The conversation cycle reset; back to waiting for the wake word. | `{"event": "idle"}` |
| `error` | A component died unrecoverably (currently only the wake-word microphone). Same string as `GET /status`'s `error`. | `{"event": "error", "message": "wake word detector: microphone failed to open: ..."}` |
| `contact_email_requested` | The send flow needs a recipient's address and has opened a pending request. Cue to show a text box wired to `POST /email/pending-contact`. | `{"event": "contact_email_requested", "name": "Michael"}` |
| `contact_email_resolved` | That request ended. `source` is `voice` (spoken and confirmed), `typed` (submitted from a frontend), or `cancelled` (never supplied — nothing was sent). Cue to hide the box. | `{"event": "contact_email_resolved", "source": "typed"}` |

#### Treatment-plan events

Emitted by the plan sub-dialogue. They are **emit-only**: nothing in the backend
reads them and none of them can affect the flow. They exist because a plan run
otherwise produces only `transcription` and `llm_response`, which leaves the
screening, the referral gate and the safety check invisible.

| Event | Description | Payload |
|---|---|---|
| `plan_started` | A plan request was recognised and the sub-dialogue began. Always the first plan event of a run. `patient_hint` may be `null`. | `{"event": "plan_started", "patient_hint": "Nadia"}` |
| `plan_stage` | Progress ticker. `stage` is one of `dictating`, `screening`, `drafting`, `revising`, `checking`, `confirming`. **Not linear** — `revising` and `checking` repeat. | `{"event": "plan_stage", "stage": "screening"}` |
| `plan_screened` | Screening finished. `confidence` is `confident` or `possible` and is **presentational only — a `possible` flag is still a referral.** | `{"event": "plan_screened", "patient_label": "Nadia", "plan_type": "standard", "referral_flags": [{"rule_id": "REF-…", "confidence": "possible", "matched_because": "…", "verbatim": "…", "refer_to": "GP", "kind": "refer_out"}], "applicable_rule_ids": ["CI-…"], "missing_safety_fields": ["pregnancy status"], "uncovered_topics": ["…"]}` |
| `plan_drafted` | A draft exists. Carries the plan's **shape only** — fetch the document from `GET /plans/{filename}` once saved. Re-emitted after every revision. | `{"event": "plan_drafted", "phase_count": 2, "total_months": "3", "phases": [{"name": "Phase 1 — Remove", "duration_weeks": "6", "supplement_count": 3}]}` |
| `plan_checked` | The post-draft contraindication check finished. **`check_ran: false` means the check itself failed — an empty `violations` list is then NOT a clean bill of health.** `auto_cleared` is `true` when an automatic fix pass ran and its re-check found strictly fewer violations. | `{"event": "plan_checked", "check_ran": true, "auto_cleared": false, "violations": [{"rule_id": "CI-…", "item": "Berberine", "phase": "Phase 1", "explanation": "…", "verbatim": "…"}]}` |
| `plan_saved` | A file was written. `kind` distinguishes a plan from a referral memo. `unsure` is `true` when it was saved because the spoken confirmation was unclear rather than because it was approved — surface that distinction. | `{"event": "plan_saved", "filename": "DRAFT_plan_nadia_20260723-174826.md", "kind": "plan", "unsure": false}` |
| `plan_ended` | Terminal, exactly one per run. `outcome` is `saved`, `discarded`, `abandoned`, `failed`, or `unavailable`. | `{"event": "plan_ended", "outcome": "saved"}` |

**Payload types and optional keys — the part that breaks clients:**

- Inside `referral_flags` and `violations`, the keys `verbatim`, `refer_to` and `kind` are **optional**.
  They are attached only when the rule id resolves in the rulebook, and are
  **absent entirely** (not empty strings) when it does not. Destructuring them
  unconditionally yields `undefined`. `rule_id`, `matched_because` /
  `explanation`, and the other listed keys are always present.
- In `plan_drafted`, `total_months` and `duration_weeks` are always **strings** —
  never numbers, and never `null`, but frequently `""` when the model left the
  field out. Test for emptiness, not for null. `phase_count` and
  `supplement_count` *are* integers.
- `phases` may be an empty array.

**Case dictation has no event of its own.** While the user dictates, each spoken
segment arrives as an ordinary `transcription`. `plan_stage: "dictating"` is your
signal to stitch those into one block rather than render them as separate
conversational turns. No `plan_*` event ever carries case text.

**Sending email has no events of its own.** The whole exchange — the draft read
aloud, "should I send it?", "sent to Michael" — flows through the ordinary
`llm_response` → `speaking_started` → `speaking_ended` → `listening_started` →
`transcription` sequence, so a transcript view needs no special handling. The two
`contact_email_*` events are the only additions.

**Proactive email announcements have no events of their own either.** When the
poller finds new mail and Lana is idle, she speaks a nudge using that same
sequence and then listens, exactly as if the wake word had been said. The only
way to tell: that `llm_response` has **no preceding `wake_word_detected`**. Poll
`GET /email/summary` for email state rather than inferring it from the socket.

---

## React integration

### A fetch helper

```js
const BASE = "http://127.0.0.1:8000";
const TOKEN = import.meta.env.VITE_LANA_API_TOKEN;

export async function lana(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    ...options,
    headers: {
      Authorization: `Bearer ${TOKEN}`,
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
  });

  if (res.status === 401) throw new Error("Bad or missing LANA_API_TOKEN");

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    // 422 gives an ARRAY of field errors; everything else gives a string.
    const message = Array.isArray(body.detail)
      ? body.detail.map((e) => `${e.loc?.join(".")}: ${e.msg}`).join("; ")
      : body.detail ?? res.statusText;
    throw new Error(message);
  }

  const type = res.headers.get("content-type") ?? "";
  return type.includes("application/json") ? res.json() : res.text();
}
```

`GET /plans/{filename}` returns `text/plain`, which is why the helper branches on
content type rather than always calling `.json()`.

### The event socket

One socket for the whole app, mounted high in the tree — not per screen, because
anything emitted while you are disconnected is gone.

```js
import { useEffect, useRef, useState } from "react";

const WS_URL = `ws://127.0.0.1:8000/events?token=${TOKEN}`;

export function useLanaEvents(onEvent) {
  const [connected, setConnected] = useState(false);
  const attempts = useRef(0);
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    let socket;
    let timer;
    let closed = false;

    const open = () => {
      socket = new WebSocket(WS_URL);

      socket.onopen = () => {
        attempts.current = 0;
        setConnected(true);
      };

      socket.onmessage = (e) => handler.current(JSON.parse(e.data));

      socket.onclose = (e) => {
        setConnected(false);
        if (closed) return;
        // 1006 on the FIRST connect is almost always a bad token: the server
        // rejects the handshake with HTTP 403, which a browser cannot expose.
        if (e.code === 1006 && attempts.current === 0) {
          console.error("Lana WS rejected — check LANA_API_TOKEN");
        }
        const delay = Math.min(30000, 1000 * 2 ** attempts.current++);
        timer = setTimeout(open, delay + Math.random() * 500);
      };
    };

    open();
    return () => {
      closed = true;
      clearTimeout(timer);
      socket?.close();
    };
  }, []);

  return connected;
}
```

Never call `socket.send(...)`, and never send binary — see
[Sending to the server](#sending-to-the-server).

### Resynchronising after a gap

```js
const connected = useLanaEvents(handleEvent);

useEffect(() => {
  if (!connected) return;
  // The transcript for the disconnected window is unrecoverable; everything
  // else has a REST source of truth.
  lana("/status").then(setStatus);
  lana("/plans").then((r) => setPlans(r.plans));
}, [connected]);
```

### Consuming a plan run safely

```js
function handleEvent(evt) {
  switch (evt.event) {
    case "plan_stage":
      setStage(evt.stage); // may repeat: revising -> checking -> revising
      break;

    case "plan_screened":
      setFlags(
        evt.referral_flags.map((f) => ({
          ruleId: f.rule_id,
          why: f.matched_because,
          // Optional: absent when the rule id isn't in the rulebook.
          quote: f.verbatim ?? null,
          referTo: f.refer_to ?? null,
          // A `possible` flag is still a referral. Never filter these out.
          confidence: f.confidence,
        })),
      );
      break;

    case "plan_checked":
      // An empty list with check_ran false means the check FAILED.
      setSafety(
        evt.check_ran
          ? { ok: evt.violations.length === 0, violations: evt.violations }
          : { ok: false, unverified: true, violations: [] },
      );
      break;

    case "plan_drafted":
      // Strings, possibly "" — never numbers.
      setShape({
        months: evt.total_months || null,
        phases: evt.phases.map((p) => ({
          name: p.name,
          weeks: p.duration_weeks || null,
          supplements: p.supplement_count,
        })),
      });
      break;

    case "plan_saved":
      lana(`/plans/${evt.filename}`).then(setDocument);
      if (evt.unsure) showUnclearConfirmationNotice();
      break;

    default:
      appendToTranscript(evt);
  }
}
```

### Two things the UI must not soften

1. **Every draft is unreviewed.** Keep a persistent, non-dismissible notice
   wherever a draft is displayed, in addition to the banners inside the document
   itself. Do not strip those banners when rendering.
2. **`check_ran: false` is not "no problems found."** It means the safety check
   did not complete. Render it as an unverified draft, not a clean one.

---

## What this API cannot do

Nothing below exists. It is listed so you can design around it rather than
discover it late.

**No way to make Lana do anything.** Beyond `POST /start` and `POST /stop`, there
is no endpoint that triggers behaviour. Specifically there is no way to:

- send Lana a typed message or question — every interaction is spoken
- start a treatment-plan draft from a patient record, or from anything but voice
- trigger an email check, search or read
- make her speak arbitrary text

**No approval or edit path for treatment plans.** `/plans/*` is read-only, and
there is **no per-draft status stored anywhere**. In particular:

- `reviewed` on `GET /plans/knowledge` describes the **rulebook**, not a draft.
- Every draft is a `DRAFT_…md` file with the review banner written into its
  bytes. There is no "approved" state to read or set.
- Revising a draft happens only inside the voice sub-dialogue, where every
  revision **re-runs the contraindication check** before it is kept. Any future
  edit or approve endpoint must preserve that; a draft modified without re-running
  the check would bypass the clinical safety net entirely.

So a review screen with an Approve button must, today, store approval in its own
database and treat Lana's file as an immutable input.

**No patient identifier.** `GET /plans` exposes `patient_label`, a slug derived
from the spoken name (e.g. `nadia`). There is no patient id, no MRN, no stable
key. Two patients sharing a first name are indistinguishable, and a draft cannot
be reliably joined to a record in another system. Plan for a manual association
step, or raise this before building anything that depends on the join.

**No email beyond the 2-day cache.** `GET /email/summary` is the only mail
endpoint. There is no REST search, no message fetch by id, no bodies, no folders,
no threads. Older mail is reachable **by voice only**.

**No sending, by design.** No endpoint sends email. A message leaves the machine
only after the draft is read aloud in full and approved out loud. CC/BCC,
attachments, multiple recipients and reply threading are not implemented at all.

**No memory access.** The facts, people and profile Lana remembers have no
endpoint — no read, no write.

**Thin health signalling.** `GET /status`'s `error` covers the wake-word
microphone only. A failure in speech recognition, speech synthesis, or the
language model is not surfaced over this API; it appears in the backend log.

**One shared credential.** A single static token, with no per-client identity, no
scopes, no expiry and no rotation. Every consumer holds the same secret, so
access cannot be revoked for one without breaking all of them, and requests
cannot be attributed to a caller.

**Loopback only, unencrypted.** The backend binds `127.0.0.1` and speaks plain
HTTP. It carries patient treatment drafts and mailbox contents, so it must not be
exposed on a network without first addressing transport security and per-client
credentials with the operator.
