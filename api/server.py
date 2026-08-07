# ─────────────────────────────────────────────
#  api/server.py – FastAPI application factory
#  Exposes Lana pipeline and WebSocket events
# ─────────────────────────────────────────────

import asyncio
import html
import os
import re
import secrets
import smtplib
import threading
from datetime import datetime
from typing import Dict, Any, List, Optional, Set
from contextlib import asynccontextmanager
from urllib.parse import urlparse, parse_qs

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from imapclient.exceptions import IMAPClientError
from loguru import logger
from pydantic import BaseModel, Field

from api.audio_ws import register_audio_ws

from core import plan_manager
from core.assistant import LanaAssistant
from core.email_manager import OAUTH_STATE_TTL_S
from core.memory import is_valid_email
import config

# Global singleton assistant
assistant = LanaAssistant()
# Exactly one EmailManager must exist per process - bind to the assistant's
# shared instance rather than constructing a second one (two instances would
# double-poll and race the cache file; see core/email_manager.py's module
# docstring).
email_manager = assistant.get_email_manager()
# Same instance the voice flow drafts with, so the corpus provenance this API
# reports is the corpus that actually produced the drafts it serves.
plan = assistant.get_plan_manager()

# The demo dashboard (repo-root ui/). Static HTML/CSS/JS with no data in it,
# so the mount itself is deliberately unauthenticated - every endpoint the
# page then calls still requires the bearer token.
_UI_DIR: str = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui"
)

# ── API token auth ────────────────────────────────────────────────────────────
# /start, /stop and /events carry control of the assistant and the live
# transcription stream — anything on this machine (including any web page in a
# browser: CORS does not apply to WebSockets) could reach them otherwise.
# Auth fails CLOSED: no configured token means every protected request is
# rejected, not silently allowed.

if not config.LANA_API_TOKEN:
    logger.warning(
        "LANA_API_TOKEN is not set — /start, /stop, /events, and all "
        "/email/* endpoints (except the OAuth callback, which is protected "
        "by a one-time state nonce instead) will reject all requests until "
        "it is added to .env (see .env.example)."
    )


def _token_matches(presented: Optional[str]) -> bool:
    """Constant-time token comparison; False when unset/missing (fail closed)."""
    if not config.LANA_API_TOKEN or not presented:
        return False
    return secrets.compare_digest(
        presented.encode("utf-8"), config.LANA_API_TOKEN.encode("utf-8")
    )


async def require_token(authorization: Optional[str] = Header(default=None)) -> None:
    """Dependency guarding control endpoints: `Authorization: Bearer <token>`."""
    presented: Optional[str] = None
    if authorization and authorization.lower().startswith("bearer "):
        presented = authorization[len("bearer "):].strip()
    if not _token_matches(presented):
        raise HTTPException(status_code=401, detail="Missing or invalid API token.")


async def optional_token(authorization: Optional[str] = Header(default=None)) -> bool:
    """
    Like require_token but never rejects — reports whether one was presented.

    Used by /status, which must stay reachable without a token because the
    other team's monitoring polls it, while not handing internal failure
    detail to anyone on the open internet.
    """
    presented: Optional[str] = None
    if authorization and authorization.lower().startswith("bearer "):
        presented = authorization[len("bearer "):].strip()
    return _token_matches(presented)

# A set of active websocket connections for broadcasting
active_websockets: Set[WebSocket] = set()


# Sentinel pushed onto the event queue to end broadcast_events()'s blocking read.
# Identity-compared, so it can never collide with a real event (always a dict).
_SHUTDOWN = object()


async def broadcast_events():
    """Background task to read from assistant queue and broadcast to all websockets."""
    q = assistant.get_event_queue()
    while True:
        try:
            # Wait for an event in a thread so we don't block the asyncio event loop.
            # This parks a worker thread in a blocking queue.get(); task.cancel()
            # cancels the awaitable, NOT the OS thread, so the only way out is a
            # value arriving. The lifespan pushes _SHUTDOWN to provide one —
            # without it the event loop hangs forever joining its executor and
            # uvicorn.run() never returns.
            event = await asyncio.to_thread(q.get)
            if event is _SHUTDOWN:
                break

            # Broadcast to all connected clients
            dead_sockets = set()
            for ws in active_websockets:
                try:
                    await ws.send_json(event)
                except Exception:
                    dead_sockets.add(ws)
            
            # Clean up disconnected sockets
            for ws in dead_sockets:
                active_websockets.discard(ws)
                
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Event broadcast error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: load the email account store/cache, start its poller (both
    # calls are idempotent - assistant.run(), if also active, may have
    # already done this; see core/email_manager.py's module docstring), then
    # start the background event broadcaster task.
    await asyncio.to_thread(email_manager.initialize)
    email_manager.start_polling()
    # Load the plan corpus if assistant.run() hasn't already (main.py starts it
    # first, but /start may never be hit). Guarded rather than unconditional so
    # the corpus is never parsed twice, and non-fatal for the same reason it is
    # in run(): a broken corpus disables drafting, never the server.
    if not plan.available:
        await asyncio.to_thread(plan.load)
    task = asyncio.create_task(broadcast_events())
    yield
    # Shutdown: stop the email poller (assistant.stop() deliberately does
    # NOT do this - see EmailManager.stop_polling()'s docstring) and cancel
    # the broadcaster task.
    email_manager.stop_polling()
    # Unblock the broadcaster's queue.get() so its worker thread can exit and the
    # loop can join the default executor. Anything already queued is broadcast
    # first (the sentinel goes to the back), then the loop breaks. cancel() stays
    # as a backstop for the case where the broadcaster is mid-send to a wedged
    # socket and never reaches the next get().
    assistant.get_event_queue().put(_SHUTDOWN)
    try:
        await asyncio.wait_for(task, timeout=5.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        task.cancel()


app = FastAPI(
    title="Lana API",
    description="Local backend server for the Lana voice AI assistant.",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS for the local Electron frontend. Origins stay wildcard because the
# Electron app's origin scheme (file:// → "null", or app://) isn't fixed;
# the bearer token is the actual protection (and CORS can't cover WebSockets
# anyway). Credentials are OFF — we use no cookies, and wildcard+credentials
# is the most permissive combination possible.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["meta"])
async def health_check() -> Dict[str, str]:
    """Simple liveness probe for the Electron frontend."""
    return {"status": "ok", "service": "lana"}


@app.get("/status", tags=["state"])
async def get_status(authed: bool = Depends(optional_token)) -> Dict[str, Any]:
    """
    Current assistant state and last component failure (if any).

    Deliberately still reachable WITHOUT a token: the CRM team's monitoring
    polls it, and breaking that to close a leak would be trading one problem
    for another. The shape never changes, so `error is not null` remains a
    valid alerting condition either way.

    What changes is the detail. The raw string names the failing component and
    carries the OS error verbatim ("microphone failed to open: [Errno -9996]
    Invalid input device") — useful to an operator, free reconnaissance to
    anyone else once this is on the open internet. So an unauthenticated caller
    learns THAT something is wrong; a token holder learns what.
    """
    error = assistant.get_last_error()
    if error is not None and not authed:
        error = "A component has failed. Authenticate for detail."
    return {
        "running": assistant.is_active(),
        "state": assistant.get_state().value,
        "error": error,
        # Whether the microphone is expected to come from a browser over
        # /audio, or is a device on this machine. A frontend needs this to know
        # whether offering a "talk" control makes any sense; unauthenticated
        # because it is a deployment shape, not data.
        "browser_audio": hasattr(getattr(assistant, "_stt", None), "attach_session"),
    }


@app.post("/start", tags=["control"], dependencies=[Depends(require_token)])
async def start_assistant() -> Dict[str, str]:
    """Starts the assistant pipeline in a background thread."""
    if assistant.is_active():
        return {"status": "already running"}
    
    logger.info("Starting Lana Assistant from API request...")
    threading.Thread(target=assistant.run, name="lana-main-loop", daemon=True).start()
    return {"status": "started"}


@app.post("/stop", tags=["control"], dependencies=[Depends(require_token)])
async def stop_assistant() -> Dict[str, str]:
    """Stops the assistant cleanly."""
    if not assistant.is_active():
        return {"status": "already stopped"}
    
    logger.info("Stopping Lana Assistant from API request...")
    assistant.stop()
    return {"status": "stopped"}


@app.websocket("/events")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint that receives real-time state transitions from the assistant.
    Requires the API token as a query param (ws://.../events?token=...) — browsers
    can't set headers on WebSocket handshakes, so it rides in the URL.
    """
    # Authenticate BEFORE accepting: a bad/missing token never gets a socket.
    if not _token_matches(websocket.query_params.get("token")):
        await websocket.close(code=1008)  # 1008 = policy violation
        return

    await websocket.accept()
    active_websockets.add(websocket)
    logger.info("WebSocket client connected.")
    
    try:
        while True:
            # Keep the connection alive and wait for client to disconnect
            await websocket.receive_text()
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected.")
    finally:
        active_websockets.discard(websocket)


# ── Email ──────────────────────────────────────────────────────────────────
# All endpoints below require the same LANA_API_TOKEN as /start and /stop,
# EXCEPT the OAuth callback: Google's redirect is a plain browser GET that
# cannot carry an Authorization header. That one is instead protected by a
# one-time state nonce minted by /oauth-url (which IS behind require_token),
# so with LANA_API_TOKEN unset, no nonce can ever be minted and the whole
# OAuth chain still fails closed. Every blocking call (IMAP validation,
# OAuth token exchange, account deletion) runs via asyncio.to_thread so it
# never stalls the WebSocket event broadcaster.

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    # Used by core/email_send.py's GmailSender. Requested from the very first
    # version of this flow, so every already-connected account can send
    # without re-consenting.
    "https://www.googleapis.com/auth/gmail.send",
]

_OAUTH_RESULT_HTML = """<!doctype html>
<html><head><title>Lana - Gmail</title></head>
<body style="font-family: sans-serif; text-align: center; padding-top: 4rem;">
<h2>{message}</h2>
</body></html>"""


class ImapAccountRequest(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    host: str = Field(min_length=1)
    port: int = Field(default=993, ge=1, le=65535)
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)
    use_ssl: bool = True
    # Outgoing (SMTP) settings, all optional. Left unset, the outgoing host is
    # derived from `host` at send time (imap.x.com -> smtp.x.com:465), so an
    # account added for reading needs no changes to start sending. When
    # smtp_host IS given, the SMTP login is validated live before storing.
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = Field(default=None, ge=1, le=65535)
    smtp_use_ssl: bool = True


class ContactEmailRequest(BaseModel):
    """A recipient's address, typed into the dashboard instead of spoken."""
    email: str = Field(min_length=3, max_length=254)


def _sanitize_imap_error(exc: Exception) -> str:
    """
    Maps a test_connection() failure to a user-safe message - never echoes
    the raw exception text (which can include IMAP server banners) back in
    an API response or a log line.
    """
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "The outgoing mail server rejected the username and password."
    if isinstance(exc, smtplib.SMTPException):
        return "Could not connect with the given SMTP settings."
    if isinstance(exc, IMAPClientError):
        return "Login failed — check the username and password."
    if isinstance(exc, OSError):  # socket timeouts, DNS failures, TLS errors, refused connections
        return "Could not connect to the server — check the host, port, and SSL setting."
    return "Could not connect with the given IMAP settings."


def _gmail_redirect_uri() -> str:
    return f"{config.GOOGLE_OAUTH_REDIRECT_BASE}/email/accounts/gmail/oauth-callback"


def _build_oauth_flow() -> Flow:
    redirect_uri = _gmail_redirect_uri()
    client_config = {
        "web": {
            "client_id": config.GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": config.GOOGLE_OAUTH_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }
    flow = Flow.from_client_config(client_config, scopes=GMAIL_SCOPES)
    flow.redirect_uri = redirect_uri
    return flow


def _authorization_url_has_scopes(url: str) -> bool:
    """
    True only if the generated consent URL actually carries a non-empty
    `scope` query param containing every scope in GMAIL_SCOPES. Guards against
    ever handing Google (or the frontend) a URL that would fail with "Missing
    required parameter: scope" - a silent, confusing failure mode that a
    dependency upgrade or refactor could otherwise reintroduce.
    """
    scope_values = parse_qs(urlparse(url).query).get("scope")
    if not scope_values:
        return False
    return all(scope in scope_values[0] for scope in GMAIL_SCOPES)


@app.post("/email/accounts/imap", tags=["email"], dependencies=[Depends(require_token)])
async def add_imap_account(request: ImapAccountRequest) -> Dict[str, Any]:
    """Adds a Hostinger-style (or any standard) IMAP account. Validates the
    connection live before storing - never persists unreachable credentials.
    An explicitly-supplied smtp_host is validated too; a derived one is not
    (see EmailManager.add_imap_account)."""
    try:
        account = await asyncio.to_thread(
            email_manager.add_imap_account,
            request.label, request.host, request.port,
            request.username, request.password, request.use_ssl,
            request.smtp_host, request.smtp_port, request.smtp_use_ssl,
        )
    except Exception as exc:
        logger.warning(f"IMAP account validation failed for host '{request.host}': {type(exc).__name__}")
        raise HTTPException(status_code=400, detail=_sanitize_imap_error(exc))
    return {"account": account}


# ── Typed recipient addresses ────────────────────────────────────────────────
# When the voice send flow needs an address it doesn't have, it asks aloud and
# opens a pending request. The boss can answer by speaking it (the default) or,
# because email addresses are hard for STT to get right, by typing it here.
# This path is strictly optional: the voice flow never waits on it, and works
# identically if the dashboard is closed.

@app.get("/email/pending-contact", tags=["email"], dependencies=[Depends(require_token)])
async def get_pending_contact() -> Dict[str, Any]:
    """Whether Lana is currently waiting on a contact's email address, and
    for whom. Lets the dashboard show an input box in response to the
    `contact_email_requested` WebSocket event (or by polling)."""
    return {"pending": email_manager.get_contact_request()}


@app.post("/email/pending-contact", tags=["email"], status_code=202,
          dependencies=[Depends(require_token)])
async def submit_pending_contact(request: ContactEmailRequest) -> Dict[str, str]:
    """
    Fulfil the open address request with a typed address. The voice loop claims
    it on its next round. Submitting again before it's claimed replaces the
    value, so a typo caught in time does the right thing.

    400 when the address is malformed; 409 when nothing is pending (or it
    expired). Shape is checked first so the two are never confused by a request
    that expires between a check and a submit. Note this never sends anything
    by itself - the drafted email is still read back in full and explicitly
    confirmed out loud.
    """
    if not is_valid_email(request.email):
        raise HTTPException(status_code=400, detail="That doesn't look like a valid email address.")
    if not email_manager.submit_contact_email(request.email):
        raise HTTPException(
            status_code=409, detail="Lana isn't waiting for a contact's email address."
        )
    return {"status": "accepted"}


@app.get("/email/accounts/gmail/oauth-url", tags=["email"], dependencies=[Depends(require_token)])
async def gmail_oauth_url() -> Dict[str, Any]:
    """
    Returns the Google consent URL to open in a browser. Minting the state
    nonce here - behind require_token - is what lets the callback (which
    can't carry a bearer header) effectively inherit this endpoint's auth.
    """
    if not config.GOOGLE_OAUTH_CLIENT_ID or not config.GOOGLE_OAUTH_CLIENT_SECRET:
        raise HTTPException(
            status_code=503,
            detail="Gmail OAuth is not configured — set GOOGLE_OAUTH_CLIENT_ID/"
            "GOOGLE_OAUTH_CLIENT_SECRET in .env.",
        )
    flow = _build_oauth_flow()
    state = email_manager.mint_oauth_state()
    # prompt=consent is load-bearing: Google omits refresh_token on repeat
    # consents without it, and a refresh token is the whole point.
    auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent", state=state)
    # Fail loud and local if the URL somehow lacks its scopes, rather than
    # letting Google reject it downstream with a confusing "Missing required
    # parameter: scope". A no-op on correct output (the normal case).
    if not _authorization_url_has_scopes(auth_url):
        logger.error("Generated Gmail OAuth URL is missing scopes — refusing to return it.")
        raise HTTPException(
            status_code=500,
            detail="Failed to build a valid Google consent URL (scopes missing). "
            "Check the google-auth-oauthlib / oauthlib install.",
        )
    return {"url": auth_url, "expires_in": int(OAUTH_STATE_TTL_S)}


@app.get("/email/accounts/gmail/oauth-callback", tags=["email"])
async def gmail_oauth_callback(state: str = "", code: str = "", error: str = "") -> HTMLResponse:
    """
    Handles Google's OAuth redirect. Deliberately NOT behind require_token -
    see the "Email" section header comment above for why the state nonce
    takes its place. Never logs or renders the authorization code or any
    token; the only values ever interpolated into the response HTML are our
    own fixed strings (plus the boss's own, HTML-escaped, Gmail address).
    """
    if not email_manager.consume_oauth_state(state):
        return HTMLResponse(
            _OAUTH_RESULT_HTML.format(
                message="This link is invalid or has expired. Please try connecting your Gmail account again."
            ),
            status_code=403,
        )

    if error:
        logger.info("Gmail OAuth: consent was cancelled or denied.")
        return HTMLResponse(
            _OAUTH_RESULT_HTML.format(
                message="Connection cancelled — you can try again anytime from the settings dashboard."
            )
        )

    if not code:
        return HTMLResponse(
            _OAUTH_RESULT_HTML.format(message="No authorization code was received. Please try again."),
            status_code=400,
        )

    def _exchange_and_store() -> str:
        flow = _build_oauth_flow()
        flow.fetch_token(code=code)
        credentials = flow.credentials
        if not credentials.refresh_token:
            return (
                "Google didn't grant a long-lived connection this time. Please remove "
                "Lana's access at myaccount.google.com/permissions and try connecting again."
            )
        service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        profile = service.users().getProfile(userId="me").execute()
        email_address = profile.get("emailAddress", "")
        email_manager.add_gmail_account(email_address or "Gmail", credentials.refresh_token, email_address)
        return f"Gmail account connected — {html.escape(email_address)}. You can close this tab."

    try:
        message = await asyncio.to_thread(_exchange_and_store)
    except Exception as exc:
        # Log the full error detail (type + message) for the operator: a bare
        # exception name is useless for diagnosing setup issues. Google's
        # HttpError message here is setup guidance (e.g. "Gmail API has not
        # been used in project N before or it is disabled … enable it by
        # visiting <link>"), not a secret — it carries no auth code or token.
        logger.warning(f"Gmail OAuth exchange failed: {type(exc).__name__}: {exc}")
        return HTMLResponse(
            _OAUTH_RESULT_HTML.format(message="Something went wrong connecting your Gmail account. Please try again."),
            status_code=400,
        )

    return HTMLResponse(_OAUTH_RESULT_HTML.format(message=message))


@app.get("/email/accounts", tags=["email"], dependencies=[Depends(require_token)])
async def list_email_accounts() -> Dict[str, Any]:
    """Lists connected accounts - label and provider only, never credentials."""
    return {"accounts": email_manager.list_accounts_safe()}


@app.delete("/email/accounts/{account_id}", tags=["email"], dependencies=[Depends(require_token)])
async def delete_email_account(account_id: str) -> Dict[str, str]:
    removed = await asyncio.to_thread(email_manager.delete_account, account_id)
    if not removed:
        raise HTTPException(status_code=404, detail="No email account with that id.")
    return {"status": "deleted"}


@app.get("/email/summary", tags=["email"], dependencies=[Depends(require_token)])
async def email_summary() -> Dict[str, Any]:
    """Dashboard payload: unread counts + recent subjects/senders per account."""
    return email_manager.get_summary()


# ── Treatment plans (READ-ONLY) ──────────────────────────────────────────────
# Three GETs, no POST/PUT/DELETE anywhere: drafts are created by the voice
# sub-dialogue and by nothing else. core/plan_manager.py has no delivery path
# and must never gain one - serving a draft to the local dashboard is not one,
# but adding a write or a send endpoint here would be.
#
# The files are patient-adjacent, so both are behind require_token, same as
# /email/summary. Filenames are matched against an allowlist derived from
# PlanManager._save()/_slugify() rather than sanitized, and the resolved path
# is then re-checked for containment: an allowlist plus a realpath check is
# two independent reasons a traversal cannot escape the folder.

_PLAN_FILENAME_RE = re.compile(
    r"DRAFT_(?P<kind>plan|referral_memo)_(?P<slug>[a-z0-9-]{1,24})_"
    r"(?P<stamp>\d{8}-\d{6})\.md"
)


def _resolve_plan_file(filename: str) -> Optional[str]:
    """
    Absolute path of a saved draft, or None if the name is not one we wrote.

    Belt: the name must match exactly the shape _save() produces. Braces: the
    resolved real path must still sit inside the plans directory, which also
    catches a symlink pointed out of it.
    """
    if not _PLAN_FILENAME_RE.fullmatch(filename):
        return None
    root = os.path.realpath(plan_manager.plans_dir())
    path = os.path.realpath(os.path.join(root, filename))
    if not path.startswith(root + os.sep) or not os.path.isfile(path):
        return None
    return path


def _list_plan_files() -> List[Dict[str, Any]]:
    """
    Saved drafts, newest first, described from the FILENAME ONLY - the
    documents are never opened here. Anything in the folder that we didn't
    write is ignored rather than listed.
    """
    root = plan_manager.plans_dir()
    try:
        names = os.listdir(root)
    except OSError:
        return []  # folder doesn't exist yet: no drafts have ever been saved

    found: List[Dict[str, Any]] = []
    for name in names:
        match = _PLAN_FILENAME_RE.fullmatch(name)
        if match is None:
            continue
        try:
            stamp = datetime.strptime(match.group("stamp"), "%Y%m%d-%H%M%S")
            saved_at = stamp.replace(tzinfo=config.TIMEZONE).isoformat()
        except ValueError:
            continue
        try:
            size = os.path.getsize(os.path.join(root, name))
        except OSError:
            continue
        found.append({
            "filename": name,
            "kind": match.group("kind"),
            "patient_label": match.group("slug"),
            "saved_at": saved_at,
            "size": size,
        })

    found.sort(key=lambda item: item["filename"], reverse=True)
    return found


# Declared BEFORE /plans/{filename} on purpose: routes match in registration
# order, so the path-param route would otherwise swallow "knowledge".
@app.get("/plans/knowledge", tags=["plans"], dependencies=[Depends(require_token)])
async def plan_knowledge() -> Dict[str, Any]:
    """
    Provenance for the corpus behind every draft: how many of her documents,
    how many rules, which version, and whether she has reviewed them yet.

    `reviewed` is reported as-is. It is false until Alexandra signs off on
    safety_rules.json, and the dashboard is expected to say so - the rendered
    document already carries the same notice.
    """
    knowledge = plan.knowledge
    if not plan.available:
        return {
            "available": False,
            "unavailable_reason": plan.unavailable_reason,
        }
    return {
        "available": True,
        "unavailable_reason": "",
        "doc_count": knowledge.source_count,
        "doc_names": list(knowledge.source_names()),
        "rule_count": len(knowledge.all_rule_ids()),
        "referral_rule_count": len(knowledge.referral_rule_ids()),
        "contraindication_rule_count": len(knowledge.contraindication_rule_ids()),
        "rules_version": knowledge.rules_version,
        "corpus_hash": knowledge.corpus_hash(),
        "reviewed": knowledge.rules_reviewed,
        "review_status": knowledge.review_status,
    }


@app.get("/plans", tags=["plans"], dependencies=[Depends(require_token)])
async def list_plans() -> Dict[str, Any]:
    """Saved drafts and referral memos, newest first. Metadata only."""
    return {"plans": await asyncio.to_thread(_list_plan_files)}


@app.get("/plans/{filename}", tags=["plans"], dependencies=[Depends(require_token)])
async def read_plan(filename: str) -> PlainTextResponse:
    """
    One saved draft, as raw markdown for the dashboard to render.

    Served as text/plain, never text/html: the document contains
    model-generated prose, and handing a browser a document it will parse as
    HTML is the one way this endpoint could become an injection vector.
    """
    path = _resolve_plan_file(filename)
    if path is None:
        raise HTTPException(status_code=404, detail="No saved draft with that name.")
    try:
        markdown = await asyncio.to_thread(
            lambda: open(path, "r", encoding="utf-8").read()
        )
    except OSError as exc:
        logger.warning(f"Could not read treatment plan draft: {type(exc).__name__}")
        raise HTTPException(status_code=500, detail="Could not read that draft.")
    return PlainTextResponse(markdown, media_type="text/plain; charset=utf-8")


# ── Demo dashboard ───────────────────────────────────────────────────────────
# Mounted last. Static assets only - no data is embedded in the page, so this
# mount carries no token requirement; the page asks for one and presents it to
# the endpoints above.

# The browser audio socket. Registered from its own module and given
# _token_matches by injection, so api/audio_ws.py never imports this file back.
# Deliberately NOT folded into /events: that socket is an external contract a
# second team builds against, and it stays JSON-only.
register_audio_ws(app, assistant, _token_matches)


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/ui/")


@app.middleware("http")
async def _no_stale_dashboard(request, call_next):
    """
    Make browsers revalidate the dashboard instead of trusting their cache.

    Found the hard way: after editing ui/app.js the browser kept serving a
    33 KB cached copy of a 38 KB file, so a newly added control simply never
    appeared and nothing in the console said why. On a deployed box that is
    worse — Alexandra opens Lana after an update and silently gets yesterday's
    dashboard, with a token flow or an event name that no longer matches the
    backend.

    `no-cache` is revalidate-per-request, NOT no-store: the ETag still short-
    circuits the body, so this costs a 304 and nothing more. Scoped to /ui
    because the API responses are already uncached.
    """
    response = await call_next(request)
    if request.url.path.startswith("/ui"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


if os.path.isdir(_UI_DIR):
    app.mount("/ui", StaticFiles(directory=_UI_DIR, html=True), name="ui")
else:  # pragma: no cover - only when the repo is checked out without ui/
    logger.warning(f"Dashboard directory not found ({_UI_DIR}) — /ui is disabled.")
