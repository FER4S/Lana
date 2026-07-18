# ─────────────────────────────────────────────────────────────────────────────
#  core/email_send.py – Unified email sending layer
#  The write-side counterpart to core/email_fetch.py, kept in its own module
#  precisely so that file's read-only invariant stays legible: nothing in
#  email_fetch.py may mutate a mailbox (select_folder(readonly=True) is
#  load-bearing there), and nothing here may fetch.
#
#  IMAP-provider accounts send over SMTP; Gmail-OAuth accounts send through the
#  Gmail API using the same refresh token and the gmail.send scope granted at
#  connect time. Both providers are reached through PROVIDER_SENDERS, mirroring
#  email_fetch.PROVIDER_FETCHERS - a new provider is a new entry, not a rewrite.
#
#  Deliberately NOT supported: CC/BCC, attachments, multiple recipients, and
#  reply threading (In-Reply-To/References/threadId). A reply is a plain new
#  message to the original sender with a "Re: " subject. All future work.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import base64
import email.utils
import os
import smtplib
import ssl
import sys
from datetime import datetime
from email.message import EmailMessage
from typing import Protocol

# Ensure the project root is on sys.path so `import config` works when this
# file is run directly (e.g. `python core/email_send.py`).
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import imapclient
from imapclient import IMAPClient
from loguru import logger

import config
from core.email_accounts import PROVIDER_GMAIL, PROVIDER_IMAP
from core.email_fetch import build_gmail_service

# ── Tuning constants ──────────────────────────────────────────────────────────
# Module-level rather than env vars, per house convention: these only ever
# change when a provider changes, not per deployment.
SMTP_DEFAULT_SSL_PORT: int = 465      # implicit TLS (SMTPS)
SMTP_DEFAULT_STARTTLS_PORT: int = 587  # explicit TLS
SMTP_TIMEOUT_S: float = 20.0           # the boss is waiting; fail fast, don't hang the turn

# After an SMTP send, IMAP-provider accounts also save a copy to their Sent
# folder (SMTP itself never does this — the Gmail API does, which is why only the
# Hostinger-style path needed it). Kept shorter than SMTP_TIMEOUT_S because by
# now the mail has already left; a slow save must not drag the spoken "Sent to X".
SENT_APPEND_TIMEOUT_S: float = 10.0
# The \Sent SPECIAL-USE attribute (imapclient.SENT lives in a submodule and isn't
# re-exported at top level in 3.0.1, so the flag bytes are named here directly).
_IMAP_SENT_FLAG: bytes = b"\\Sent"
# Case-insensitive last-path-segment fallbacks when the server advertises no
# \Sent folder — a superset of what find_special_folder() already tries.
_SENT_FALLBACK_NAMES: frozenset[str] = frozenset({
    "sent", "sent items", "sent messages", "sent mail",
})

# Subject prefix for replies. Compared case-insensitively before prepending, so
# replying to an already-"Re: " subject doesn't stack them.
REPLY_SUBJECT_PREFIX: str = "Re: "


def derive_smtp_host(imap_host: str) -> str:
    """
    Best-effort outgoing host for an account that only stored an incoming one.
    Virtually every provider that serves `imap.<domain>` also serves
    `smtp.<domain>` (Hostinger included), so this covers the accounts that were
    added before sending existed. Only a leading "imap." is rewritten; anything
    else is returned unchanged and will simply be tried as-is.

    An explicit credentials["smtp_host"] always wins over this guess.
    """
    host = (imap_host or "").strip()
    if host.lower().startswith("imap."):
        return "smtp." + host[len("imap."):]
    return host


def reply_subject(original_subject: str) -> str:
    """"Re: " the original subject, without stacking the prefix."""
    subject = (original_subject or "").strip()
    if not subject:
        return REPLY_SUBJECT_PREFIX.strip()
    if subject.lower().startswith("re:"):
        return subject
    return f"{REPLY_SUBJECT_PREFIX}{subject}"


def build_message(from_addr: str, to_addr: str, subject: str, body: str) -> EmailMessage:
    """
    The one place a MIME message is constructed. Plain-text UTF-8 only;
    set_content() picks the transfer encoding and charset, so a body with
    non-ASCII (or a very long line) is encoded correctly rather than mangled
    or silently rejected by the server.

    Date and Message-ID are set explicitly so the copy saved to the Sent folder
    displays with the right timestamp and a stable id (SMTP would otherwise leave
    the appended copy dateless). Both are standard, well-formed headers; the
    Gmail API preserves supplied headers, so this is benign for that path too.
    The Message-ID domain is taken from the sender address rather than the
    machine hostname make_msgid() would otherwise leak.
    """
    message = EmailMessage()
    message["From"] = from_addr
    message["To"] = to_addr
    message["Subject"] = subject
    message["Date"] = email.utils.format_datetime(datetime.now(config.TIMEZONE))
    msgid_domain = (from_addr.rsplit("@", 1)[-1].strip() or None) if from_addr else None
    message["Message-ID"] = email.utils.make_msgid(domain=msgid_domain)
    message.set_content(body)
    return message


def _find_sent_folder(client: IMAPClient) -> str | None:
    """Locate an account's Sent folder, or None. find_special_folder() checks
    the \\Sent SPECIAL-USE attribute and common names (incl. namespace-prefixed
    'INBOX.Sent'); the fallback catches servers that advertise neither by
    matching the last path segment case-insensitively."""
    folder = client.find_special_folder(_IMAP_SENT_FLAG)
    if folder:
        return folder
    for flags, delimiter, name in client.list_folders():
        delim = delimiter.decode() if isinstance(delimiter, bytes) else (delimiter or "")
        leaf = name.rsplit(delim, 1)[-1] if delim else name
        if leaf.strip().lower() in _SENT_FALLBACK_NAMES:
            return name
    return None


# ── Sender contract ───────────────────────────────────────────────────────────

class EmailSender(Protocol):
    def send(self, account: dict, to_addr: str, subject: str, body: str) -> None:
        """
        Send one plain-text message from `account` (a full record WITH
        credentials, as returned by EmailAccountStore.get()).

        Raises on any authentication, connection, or delivery failure - never
        returns a bool, never swallows. The caller speaks the failure aloud, so
        a silent no-op would be indistinguishable from success to the boss.

        Must not read or write the poll cache, seen_ids, or announcement state.
        """
        ...

    def sender_address(self, credentials: dict) -> str:
        """The address this account sends as (the From header)."""
        ...

    def test_connection(self, credentials: dict) -> None:
        """Authenticate against the outgoing server without sending anything.
        Raises on failure. Used to validate explicitly-supplied SMTP settings
        before they are stored."""
        ...


# ── IMAP-provider accounts: SMTP ──────────────────────────────────────────────

class SmtpSender:
    """
    Sends via SMTP using the account's IMAP username/password - the same
    credential pair works for both on every host this supports (Hostinger,
    standard cPanel-style mail). Host/port come from the optional smtp_* keys
    when present and are derived from the IMAP host otherwise.
    """

    def sender_address(self, credentials: dict) -> str:
        return credentials["username"].strip()

    @staticmethod
    def _resolve_endpoint(credentials: dict) -> tuple[str, int, bool]:
        """(host, port, use_ssl). An explicitly stored value always beats a
        derived one; the port default follows whichever TLS mode is in use."""
        use_ssl = bool(credentials.get("smtp_use_ssl", True))
        host = credentials.get("smtp_host") or derive_smtp_host(credentials["host"])
        port = credentials.get("smtp_port") or (
            SMTP_DEFAULT_SSL_PORT if use_ssl else SMTP_DEFAULT_STARTTLS_PORT
        )
        return host, int(port), use_ssl

    def _connect(self, credentials: dict) -> smtplib.SMTP:
        host, port, use_ssl = self._resolve_endpoint(credentials)
        if use_ssl:
            client = smtplib.SMTP_SSL(
                host, port, timeout=SMTP_TIMEOUT_S, context=ssl.create_default_context()
            )
        else:
            client = smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_S)
            client.starttls(context=ssl.create_default_context())
        client.login(credentials["username"], credentials["password"])
        return client

    def send(self, account: dict, to_addr: str, subject: str, body: str) -> None:
        credentials = account["credentials"]
        message = build_message(self.sender_address(credentials), to_addr, subject, body)
        with self._connect(credentials) as client:
            client.send_message(message)

        # Best-effort: save a copy to the IMAP Sent folder. The mail has already
        # been delivered by SMTP above, so a failure here must NEVER propagate —
        # raising would make the caller speak a send FAILURE for a message that
        # actually went out, and leave the boss with no record either way.
        try:
            self._append_to_sent(credentials, message)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"Sent to {to_addr}, but couldn't save a copy to the Sent folder: "
                f"{type(exc).__name__}: {exc}"
            )

    def _append_to_sent(self, credentials: dict, message: EmailMessage) -> None:
        """Append the just-sent message to the account's Sent folder over IMAP,
        reusing the same incoming credentials ImapFetcher connects with. A no-op
        (logged) if no Sent folder can be located; folders are never created."""
        host = credentials["host"]
        port = int(credentials.get("port") or 993)
        use_ssl = bool(credentials.get("use_ssl", True))
        with IMAPClient(host, port=port, ssl=use_ssl, timeout=SENT_APPEND_TIMEOUT_S) as client:
            client.login(credentials["username"], credentials["password"])
            folder = _find_sent_folder(client)
            if not folder:
                logger.info("No Sent folder found on the IMAP server — skipping the Sent copy.")
                return
            client.append(
                folder,
                message.as_bytes(),
                flags=(imapclient.SEEN,),
                msg_time=datetime.now(config.TIMEZONE),
            )
            logger.info(f"Saved a copy of the sent message to '{folder}'.")

    def test_connection(self, credentials: dict) -> None:
        with self._connect(credentials) as client:
            client.noop()


# ── Gmail-OAuth accounts: Gmail API ───────────────────────────────────────────

class GmailSender:
    """
    Sends through users().messages().send(). The gmail.send scope has been
    requested since the OAuth flow was first written, so every already-
    connected account can send without re-consent.
    """

    def sender_address(self, credentials: dict) -> str:
        return credentials["email_address"].strip()

    def send(self, account: dict, to_addr: str, subject: str, body: str) -> None:
        credentials = account["credentials"]
        message = build_message(self.sender_address(credentials), to_addr, subject, body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        service = build_gmail_service(credentials)
        service.users().messages().send(userId="me", body={"raw": raw}).execute()

    def test_connection(self, credentials: dict) -> None:
        service = build_gmail_service(credentials)
        service.users().getProfile(userId="me").execute()


# ── Registry (extensibility point, mirrors PROVIDER_FETCHERS) ────────────────

PROVIDER_SENDERS: dict[str, EmailSender] = {
    PROVIDER_IMAP: SmtpSender(),
    PROVIDER_GMAIL: GmailSender(),
}


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Offline assertions always run. A live send happens only when all of
    # SMOKE_SMTP_HOST / _USER / _PASS / _TO are set, and goes to _TO only.
    failures = 0

    def check(label: str, condition: bool) -> None:
        global failures
        if not condition:
            failures += 1
            print(f"  FAIL  {label}")
        else:
            print(f"  ok    {label}")

    print("-- derive_smtp_host --")
    check("imap. prefix is rewritten", derive_smtp_host("imap.hostinger.com") == "smtp.hostinger.com")
    check("case-insensitive prefix", derive_smtp_host("IMAP.hostinger.com") == "smtp.hostinger.com")
    check("non-imap host untouched", derive_smtp_host("mail.example.com") == "mail.example.com")
    check("an smtp. host is untouched", derive_smtp_host("smtp.example.com") == "smtp.example.com")
    check("'imap' inside the domain is not rewritten",
          derive_smtp_host("mail.imap-host.com") == "mail.imap-host.com")
    check("empty host", derive_smtp_host("") == "")

    print("\n-- reply_subject --")
    check("prefixes a plain subject", reply_subject("Monthly KPI Report") == "Re: Monthly KPI Report")
    check("does not stack Re:", reply_subject("Re: Monthly KPI") == "Re: Monthly KPI")
    check("case-insensitive stack guard", reply_subject("RE: Budget") == "RE: Budget")
    check("empty subject", reply_subject("") == "Re:")

    print("\n-- _resolve_endpoint --")
    resolve = SmtpSender._resolve_endpoint
    check("derives host + ssl port from imap creds",
          resolve({"host": "imap.hostinger.com"}) == ("smtp.hostinger.com", 465, True))
    check("explicit smtp_host wins over derivation",
          resolve({"host": "imap.a.com", "smtp_host": "out.b.com"}) == ("out.b.com", 465, True))
    check("explicit port wins",
          resolve({"host": "imap.a.com", "smtp_port": 2525}) == ("smtp.a.com", 2525, True))
    check("starttls mode defaults to 587",
          resolve({"host": "imap.a.com", "smtp_use_ssl": False}) == ("smtp.a.com", 587, False))

    print("\n-- build_message --")
    msg = build_message("me@codex.com", "you@acme.com", "Meeting at 5pm", "Hi,\n\nLet's meet at 5pm.\n")
    check("From header", msg["From"] == "me@codex.com")
    check("To header", msg["To"] == "you@acme.com")
    check("Subject header", msg["Subject"] == "Meeting at 5pm")
    check("body round-trips", msg.get_content().strip() == "Hi,\n\nLet's meet at 5pm.")
    check("content type is text/plain", msg.get_content_type() == "text/plain")
    check("serializes to bytes", isinstance(msg.as_bytes(), bytes))
    check("Date header present", bool(msg["Date"]))
    check("Date parses back to a datetime", email.utils.parsedate_to_datetime(msg["Date"]) is not None)
    check("Message-ID present and bracketed",
          bool(msg["Message-ID"]) and msg["Message-ID"].startswith("<") and msg["Message-ID"].endswith(">"))
    check("Message-ID domain is the sender's", msg["Message-ID"].rstrip(">").endswith("@codex.com"))

    unicode_msg = build_message("me@codex.com", "you@acme.com", "Café", "Naïve résumé — 5pm\n")
    check("non-ascii body round-trips", "résumé" in unicode_msg.get_content())
    check("non-ascii subject is encodable", isinstance(unicode_msg.as_bytes(), bytes))
    check("base64url-encodes for the Gmail API",
          isinstance(base64.urlsafe_b64encode(unicode_msg.as_bytes()).decode("ascii"), str))

    smoke = {k: os.environ.get(f"SMOKE_SMTP_{k}") for k in ("HOST", "USER", "PASS", "TO")}
    imap_host = os.environ.get("SMOKE_IMAP_HOST")  # optional: enables the real Sent-append + verify
    if all(smoke.values()):
        print(f"\n-- live SMTP send to {smoke['TO']} --")
        # When SMOKE_IMAP_HOST is set, credentials carry a real incoming host so
        # the Sent-folder append actually runs and can be verified below. Without
        # it, "host" is the SMTP host and the append will log a warning and be
        # skipped — that warning is EXPECTED, not a failure of the send.
        creds = {"host": imap_host or smoke["HOST"], "username": smoke["USER"], "password": smoke["PASS"]}
        marker = f"Lana send/append smoke {os.getpid()}"
        try:
            SmtpSender().send({"credentials": creds}, smoke["TO"], marker,
                              "This is a test send from core/email_send.py.")
            check("live send succeeded", True)
        except Exception as exc:
            check(f"live send succeeded (got {type(exc).__name__}: {exc})", False)

        if imap_host:
            print(f"-- verifying Sent copy on {imap_host} --")
            try:
                with IMAPClient(imap_host, ssl=True, timeout=SENT_APPEND_TIMEOUT_S) as vclient:
                    vclient.login(smoke["USER"], smoke["PASS"])
                    sent = _find_sent_folder(vclient)
                    check("Sent folder was located", bool(sent))
                    if sent:
                        vclient.select_folder(sent, readonly=True)
                        uids = vclient.search(["SUBJECT", marker])
                        check("sent message appears in the Sent folder", len(uids) >= 1)
            except Exception as exc:
                check(f"Sent-folder verify (got {type(exc).__name__}: {exc})", False)
        else:
            print("(Sent-append not exercised — set SMOKE_IMAP_HOST to the IMAP host to verify it)")
    else:
        print("\n(skipping live send — set SMOKE_SMTP_HOST/_USER/_PASS/_TO to enable)")

    print(f"\n{'FAILED' if failures else 'PASSED'} — {failures} failure(s)")
    sys.exit(1 if failures else 0)
