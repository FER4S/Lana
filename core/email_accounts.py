# ─────────────────────────────────────────────────────────────────────────────
#  core/email_accounts.py – Encrypted local store of connected email accounts
#  Accounts (IMAP host/user/password, Gmail OAuth refresh tokens) are
#  encrypted at rest by one of two ciphers, chosen by platform:
#
#    - Windows -> DPAPI (CryptProtectData). No key file exists anywhere, so
#      OneDrive syncing data/ only ever moves ciphertext.
#    - Linux   -> Fernet, keyed from LANA_ACCOUNT_KEY or a 0600 key file.
#      DPAPI has no equivalent outside Windows, and hosting Lana on Ubuntu is
#      what forced this second path.
#
#  Every store written from now on carries a header naming the cipher that
#  wrote it, so the two can never be confused. A file with NO header predates
#  this and is read as DPAPI, which is why an existing Windows install needs
#  no migration.
#
#  The DPAPI store cannot be moved to the server: those blobs are bound to one
#  machine and one user, by design. Accounts must be re-entered on the Linux
#  host and Gmail re-consented there. That is a deployment step, not a bug.
#
#  Threat model: DPAPI ties the ciphertext to this Windows user account on
#  this machine (like a browser's saved-password store) - any process running
#  as this same user can decrypt it; there is no additional secret to steal or
#  leak. Moving the encrypted file to another machine/user (e.g. via OneDrive
#  sync) makes it permanently undecryptable there - load() quarantines it and
#  starts empty rather than crash (see _quarantine_unreadable_file).
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import base64
import copy
import json
import os
import stat
import sys
import threading
import time
import uuid
from datetime import datetime

# Ensure the project root is on sys.path so `import config` works when this
# file is run directly (e.g. `python core/email_accounts.py`).
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from loguru import logger

import config

_IS_WINDOWS: bool = os.name == "nt"

# ── Storage location ─────────────────────────────────────────────────────────
_DATA_DIR: str = os.path.join(_PROJECT_ROOT, "data")
_ACCOUNTS_FILE: str = os.path.join(_DATA_DIR, "email_accounts.dat")
# Fallback key location for the portable cipher. Only used when no key is
# supplied out-of-band; see _FernetCipher for why that ordering matters.
_KEY_FILE: str = os.path.join(_DATA_DIR, "account_key")

# Read attempts before declaring the store unreadable and quarantining it.
# OneDrive/antivirus can hold a transient lock on the file; a short retry
# rides that out instead of treating it as corruption. (Mirrors
# core/memory.py's _LOAD_ATTEMPTS/_LOAD_RETRY_DELAY_S exactly.)
_LOAD_ATTEMPTS: int = 3
_LOAD_RETRY_DELAY_S: float = 0.25

STORE_VERSION: int = 1
PROVIDER_IMAP: str = "imap"
PROVIDER_GMAIL: str = "gmail_oauth"

# Fields exposed by list_safe()/the safe view returned from add_*() - never
# "credentials". Adding a new provider never needs to touch this.
_SAFE_FIELDS: tuple[str, ...] = ("id", "label", "provider", "created_at")


# ── Cipher selection ─────────────────────────────────────────────────────────
# The store was originally Windows-only: DPAPI ties ciphertext to one Windows
# user on one machine with no key file to steal. That is genuinely stronger
# than a local key file, and it stays the default on Windows.
#
# It is also completely unavailable on Linux (crypt32.dll has no equivalent),
# so hosting Lana on Ubuntu needs a portable cipher. Both live behind the same
# two-method interface and are selected by name, mirroring PROVIDER_FETCHERS.
#
# The file records which cipher wrote it, so a store written by one is never
# silently fed to the other — a mismatch must fail loudly and quarantine, not
# decode into garbage.

_CIPHER_DPAPI: str = "dpapi"
_CIPHER_FERNET: str = "fernet"

# Header on every store written from now on:  b"LANAACC1\n<cipher>\n<ciphertext>"
# A file with no header is a pre-header DPAPI store and is read as such, so an
# existing Windows install keeps working with no migration step.
_MAGIC: bytes = b"LANAACC1"

# Environment variable holding a base64 Fernet key. Preferred over the key file:
# systemd's LoadCredential= can inject it without it ever touching the disk that
# also holds the ciphertext.
_KEY_ENV_VAR: str = "LANA_ACCOUNT_KEY"


class _AccountCipher:
    """
    Encrypt/decrypt the account blob. Two methods, no state.

    Implementations must treat a failed decrypt as UNRECOVERABLE and raise:
    the store's load() turns that into a quarantine, which is the behaviour
    that stops a bad read from being silently overwritten by an empty state.
    """

    name: str = ""

    def available(self) -> bool:
        raise NotImplementedError

    def encrypt(self, plaintext: bytes) -> bytes:
        raise NotImplementedError

    def decrypt(self, ciphertext: bytes) -> bytes:
        raise NotImplementedError


# ── DPAPI (Windows only) ─────────────────────────────────────────────────────
# crypt32.dll/CryptProtectData ties ciphertext to this Windows user account on
# this machine, with no key file existing anywhere - so syncing data/ through
# OneDrive only ever moves ciphertext, and there is no secret to leak. Moving
# the file to another machine or user makes it permanently undecryptable there,
# which load() handles by quarantining rather than crashing.

if _IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(_DATA_BLOB),
        wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(_DATA_BLOB),
    ]
    _crypt32.CryptProtectData.restype = wintypes.BOOL

    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB), ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DATA_BLOB),
        wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(_DATA_BLOB),
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL

    _kernel32.LocalFree.argtypes = [wintypes.LPVOID]
    _kernel32.LocalFree.restype = wintypes.LPVOID

    # Never let DPAPI show a UI prompt - this process is headless and a blocked
    # prompt would hang the caller (a request thread or the poll thread) forever.
    _CRYPTPROTECT_UI_FORBIDDEN = 0x01

    def _to_blob(data: bytes):
        """
        Build a DATA_BLOB pointing at a live ctypes buffer. The buffer is
        returned alongside the blob so the caller keeps a reference to it for
        the duration of the call - the blob only holds a raw pointer into it,
        so letting the buffer get garbage-collected first would leave a
        dangling pointer.
        """
        buf = ctypes.create_string_buffer(data, len(data))
        blob = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        return blob, buf

    def dpapi_encrypt(plaintext: bytes) -> bytes:
        """Encrypt with CryptProtectData, scoped to this Windows user account."""
        in_blob, _keepalive = _to_blob(plaintext)
        out_blob = _DATA_BLOB()
        ok = _crypt32.CryptProtectData(
            ctypes.byref(in_blob), None, None, None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob),
        )
        if not ok:
            raise OSError(
                "CryptProtectData failed (Win32 error %d)" % ctypes.get_last_error()
            )
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            _kernel32.LocalFree(out_blob.pbData)

    def dpapi_decrypt(ciphertext: bytes) -> bytes:
        """
        Decrypt bytes produced by dpapi_encrypt(). Raises OSError if this isn't
        the same Windows user account/machine that encrypted it, or the data is
        corrupt/tampered - callers must treat that as unrecoverable, not retry.
        """
        in_blob, _keepalive = _to_blob(ciphertext)
        out_blob = _DATA_BLOB()
        ok = _crypt32.CryptUnprotectData(
            ctypes.byref(in_blob), None, None, None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob),
        )
        if not ok:
            raise OSError(
                "CryptUnprotectData failed (Win32 error %d)" % ctypes.get_last_error()
            )
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            _kernel32.LocalFree(out_blob.pbData)

else:  # pragma: no cover - exercised on the Linux host, not on this machine

    def dpapi_encrypt(plaintext: bytes) -> bytes:
        raise RuntimeError("DPAPI is Windows-only.")

    def dpapi_decrypt(ciphertext: bytes) -> bytes:
        raise RuntimeError("DPAPI is Windows-only.")


class _DpapiCipher(_AccountCipher):
    name = _CIPHER_DPAPI

    def available(self) -> bool:
        return _IS_WINDOWS

    def encrypt(self, plaintext: bytes) -> bytes:
        return dpapi_encrypt(plaintext)

    def decrypt(self, ciphertext: bytes) -> bytes:
        return dpapi_decrypt(ciphertext)


# ── Fernet (portable: Linux, and anywhere without DPAPI) ─────────────────────
# AES-128-CBC + HMAC-SHA256, authenticated, from the `cryptography` package.
#
# HONEST TRADE-OFF, stated because it is a real weakening: unlike DPAPI this
# needs a key that exists somewhere. Anyone who can read both the key and the
# ciphertext has the credentials, whereas with DPAPI there was nothing to
# steal. The key is therefore sourced in this order:
#
#   1. LANA_ACCOUNT_KEY in the environment - preferred, because systemd's
#      LoadCredential= can inject it without it ever landing on the disk that
#      also holds the ciphertext.
#   2. data/account_key, mode 0600, generated on first use.
#
# Option 2 puts the key beside the lock. The compensating controls on the
# server are a dedicated non-root user, 0600/0700 permissions, and an encrypted
# volume - all deployment steps, not code, so they must actually be done rather
# than assumed.

class _FernetCipher(_AccountCipher):
    name = _CIPHER_FERNET

    def __init__(self, key_path: str = _KEY_FILE) -> None:
        self._key_path = key_path
        self._fernet = None

    def available(self) -> bool:
        try:
            import cryptography.fernet  # noqa: F401

            return True
        except ImportError:
            return False

    def _load_key(self) -> bytes:
        env_key = os.environ.get(_KEY_ENV_VAR, "").strip()
        if env_key:
            return env_key.encode("ascii")

        if os.path.exists(self._key_path):
            with open(self._key_path, "rb") as handle:
                return handle.read().strip()

        from cryptography.fernet import Fernet

        key = Fernet.generate_key()
        os.makedirs(os.path.dirname(self._key_path), exist_ok=True)
        # Create with 0600 from the outset rather than chmod-ing afterwards: a
        # world-readable window, however brief, is a window on a shared box.
        fd = os.open(
            self._key_path,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            stat.S_IRUSR | stat.S_IWUSR,
        )
        try:
            os.write(fd, key)
        finally:
            os.close(fd)
        logger.warning(
            "Generated a new account-store key at %s. Back it up: without it "
            "the saved email accounts cannot be decrypted." % self._key_path
        )
        return key

    def _cipher(self):
        if self._fernet is None:
            from cryptography.fernet import Fernet

            self._fernet = Fernet(self._load_key())
        return self._fernet

    def encrypt(self, plaintext: bytes) -> bytes:
        return self._cipher().encrypt(plaintext)

    def decrypt(self, ciphertext: bytes) -> bytes:
        from cryptography.fernet import InvalidToken

        try:
            return self._cipher().decrypt(ciphertext)
        except InvalidToken:
            # Wrong key or tampered data. Same contract as DPAPI's failure:
            # unrecoverable, so the caller quarantines rather than retrying.
            raise OSError(
                "account store could not be decrypted (wrong key or tampered)"
            )


# ── Registry (mirrors PROVIDER_FETCHERS / PROVIDER_SENDERS) ──────────────────

ACCOUNT_CIPHERS: dict[str, _AccountCipher] = {
    _CIPHER_DPAPI: _DpapiCipher(),
    _CIPHER_FERNET: _FernetCipher(),
}


def default_cipher_name() -> str:
    """
    DPAPI on Windows (no key file to manage), Fernet everywhere else.

    Overridable with LANA_ACCOUNT_CIPHER, which exists mainly so the portable
    path can be exercised ON Windows - otherwise it would only ever run on the
    server, which is the worst possible place to discover a bug in it.
    """
    override = os.environ.get("LANA_ACCOUNT_CIPHER", "").strip().lower()
    if override:
        if override not in ACCOUNT_CIPHERS:
            raise ValueError(
                "Unknown LANA_ACCOUNT_CIPHER %r. Known: %s"
                % (override, sorted(ACCOUNT_CIPHERS))
            )
        return override
    return _CIPHER_DPAPI if _IS_WINDOWS else _CIPHER_FERNET


def encode_store(plaintext: bytes, cipher_name: str) -> bytes:
    """Encrypt, prefixed with a header naming the cipher that did it."""
    cipher = ACCOUNT_CIPHERS[cipher_name]
    return (
        _MAGIC
        + b"\n"
        + cipher_name.encode("ascii")
        + b"\n"
        + cipher.encrypt(plaintext)
    )


def decode_store(raw: bytes) -> bytes:
    """
    Decrypt a store file, honouring its header.

    A file with no header predates this change and is raw DPAPI ciphertext, so
    an existing Windows install keeps working untouched - there is no migration
    step and no flag day.
    """
    if raw.startswith(_MAGIC + b"\n"):
        parts = raw.split(b"\n", 2)
        if len(parts) != 3:
            raise OSError("account store header is truncated")
        cipher_name = parts[1].decode("ascii", "replace")
        if cipher_name not in ACCOUNT_CIPHERS:
            raise OSError(
                "account store was written by unknown cipher %r" % cipher_name
            )
        return ACCOUNT_CIPHERS[cipher_name].decrypt(parts[2])

    # Legacy, pre-header. Only DPAPI ever wrote these.
    if not _IS_WINDOWS:
        raise OSError(
            "this account store was written by Windows DPAPI and cannot be read "
            "here - DPAPI blobs are bound to one machine and user. Re-add the "
            "accounts on this host."
        )
    return dpapi_decrypt(raw)


class EmailAccountStore:
    """
    DPAPI-encrypted local store of connected email accounts.

    Mirrors core.memory.MemoryManager's persistence pattern: a single lock,
    atomic tmp-file + os.replace writes, and quarantine-on-unreadable rather
    than ever overwriting the boss's saved accounts with an empty fallback
    state. The only difference is the file holds DPAPI ciphertext instead of
    plaintext JSON.

    Adding a future provider type (e.g. Outlook OAuth) needs only a new
    PROVIDER_* string and its own credentials dict shape - no schema rewrite,
    since "credentials" is opaque to the store itself.

    Usage:
        store = EmailAccountStore()
        store.load()
        store.add_imap_account("Hostinger Support", "imap.hostinger.com", 993, "u", "p")
    """

    def __init__(self, path: str = _ACCOUNTS_FILE, cipher: str | None = None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._accounts: list[dict] = []
        # Resolved once at construction, not per write: the cipher a store is
        # written with must not change underneath a running process. Reads
        # honour whatever header the file carries, so a store written by the
        # other cipher is still readable — it is only new writes that are
        # pinned here.
        self._cipher_name = cipher or default_cipher_name()
        # Set when the store file was unreadable AND couldn't be quarantined -
        # every save is then refused so it's never overwritten by this
        # session's empty fallback state.
        self._save_disabled = False

    # ── Storage ───────────────────────────────────────────────────────────────

    def load(self) -> None:
        """
        Load accounts from disk, creating an empty encrypted store if missing.

        If the file exists but can't be read/decrypted/parsed even after a
        short retry (transient OneDrive/AV locks, or - permanently - ciphertext
        that DPAPI on this machine/user can never decrypt, e.g. after a
        OneDrive sync to a different PC), it is quarantined: renamed aside for
        manual recovery, never overwritten. The boss re-adds accounts via the
        settings UI; nothing crashes.
        """
        with self._lock:
            if not os.path.isdir(_DATA_DIR):
                os.makedirs(_DATA_DIR, exist_ok=True)

            if not os.path.isfile(self._path):
                logger.info(f"No email account store found at {self._path} - creating a new one.")
                self._accounts = []
                self._write_locked()
                return

            last_exc: Exception | None = None
            for attempt in range(1, _LOAD_ATTEMPTS + 1):
                # ValueError covers json.JSONDecodeError plus the wrong-shape
                # check below; OSError covers locked/unreadable files AND a
                # DPAPI decrypt failure (dpapi_decrypt raises OSError).
                try:
                    with open(self._path, "rb") as f:
                        ciphertext = f.read()
                    plaintext = decode_store(ciphertext)
                    loaded = json.loads(plaintext.decode("utf-8"))
                    if not isinstance(loaded, dict) or not isinstance(loaded.get("accounts"), list):
                        raise ValueError("unexpected store shape")
                    self._accounts = loaded["accounts"]
                    logger.success(f"Loaded {len(self._accounts)} email account(s) from {self._path}.")
                    return
                except (ValueError, OSError) as exc:
                    last_exc = exc
                    if attempt < _LOAD_ATTEMPTS:
                        logger.warning(
                            f"Failed to load email account store (attempt {attempt}/"
                            f"{_LOAD_ATTEMPTS}: {exc}) - retrying in {_LOAD_RETRY_DELAY_S}s…"
                        )
                        time.sleep(_LOAD_RETRY_DELAY_S)

            self._quarantine_unreadable_file(last_exc)

    def _quarantine_unreadable_file(self, exc: Exception | None) -> None:
        """
        Handle a store file that exists but stayed unreadable through the
        retry loop. Caller must already hold self._lock.

        Same rationale as MemoryManager._quarantine_unreadable_file(): never
        let a later save() overwrite real accounts with this session's empty
        fallback. This also covers the DPAPI-specific permanent case
        (ciphertext encrypted under a different Windows user/machine can never
        be decrypted here) - there is no way to recover it, only to preserve
        it for inspection and start fresh.
        """
        timestamp = datetime.now(config.TIMEZONE).strftime("%Y%m%d-%H%M%S")
        quarantine_path = f"{self._path}.corrupt-{timestamp}"
        try:
            os.replace(self._path, quarantine_path)
            logger.critical(
                f"Email account store could not be loaded ({exc}) - moved it to "
                f"{quarantine_path} for manual recovery. Starting with NO email "
                f"accounts connected; re-add them from the settings UI."
            )
        except OSError as move_exc:
            self._save_disabled = True
            logger.critical(
                f"Email account store could not be loaded ({exc}) and could not "
                f"be quarantined ({move_exc}) - saves are DISABLED for this "
                f"session to protect the file on disk. Restart Lana once the "
                f"file is accessible again."
            )
        self._accounts = []

    def _write_locked(self) -> None:
        """
        Actual write logic. Caller must already hold self._lock. Only ever
        logs account counts, never labels/hosts/usernames/secrets.
        """
        if self._save_disabled:
            logger.error(
                "Email account store save skipped - saves are disabled for "
                "this session after an unreadable store was left in place "
                "(see startup log)."
            )
            return
        os.makedirs(_DATA_DIR, exist_ok=True)
        payload = {"version": STORE_VERSION, "accounts": self._accounts}
        plaintext = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            ciphertext = encode_store(plaintext, self._cipher_name)
        except OSError as exc:
            logger.error(f"Failed to encrypt email account store: {exc}")
            return
        tmp_path = self._path + ".tmp"
        try:
            with open(tmp_path, "wb") as f:
                f.write(ciphertext)
            os.replace(tmp_path, self._path)  # atomic on both Windows and POSIX
        except OSError as exc:
            logger.error(f"Failed to save email account store: {exc}")

    # ── Accounts ──────────────────────────────────────────────────────────────

    def add_imap_account(
        self,
        label: str,
        host: str,
        port: int,
        username: str,
        password: str,
        use_ssl: bool = True,
        smtp_host: str | None = None,
        smtp_port: int | None = None,
        smtp_use_ssl: bool = True,
    ) -> dict:
        """
        Add a new IMAP account and save immediately. Returns the SAFE view
        (no credentials) - callers needing the credentials back should use
        get()/list_all() instead (the fetch/send path only).

        The smtp_* fields are optional and only needed when the outgoing host
        can't be derived from the incoming one. Left unset, core/email_send.py
        derives it at send time (imap.example.com -> smtp.example.com:465), so
        accounts stored before sending existed keep working with no migration
        and no re-add. `credentials` stays an opaque dict as far as this store
        is concerned.
        """
        credentials = {
            "host": host.strip(),
            "port": int(port),
            "username": username.strip(),
            "password": password,
            "use_ssl": bool(use_ssl),
        }
        if smtp_host:
            credentials["smtp_host"] = smtp_host.strip()
        if smtp_port:
            credentials["smtp_port"] = int(smtp_port)
        if smtp_host or smtp_port:
            credentials["smtp_use_ssl"] = bool(smtp_use_ssl)

        account = {
            "id": uuid.uuid4().hex,
            "label": label.strip() or host,
            "provider": PROVIDER_IMAP,
            "created_at": datetime.now(config.TIMEZONE).isoformat(),
            "credentials": credentials,
        }
        with self._lock:
            self._accounts.append(account)
            self._write_locked()
        logger.success(f"IMAP email account added: '{account['label']}' (host {host}) - id {account['id']}")
        return self._safe_view(account)

    def add_gmail_account(self, label: str, refresh_token: str, email_address: str) -> dict:
        """
        Add a new Gmail OAuth account and save immediately. Returns the SAFE
        view (no refresh token).
        """
        account = {
            "id": uuid.uuid4().hex,
            "label": label.strip() or email_address,
            "provider": PROVIDER_GMAIL,
            "created_at": datetime.now(config.TIMEZONE).isoformat(),
            "credentials": {
                "refresh_token": refresh_token,
                "email_address": email_address.strip(),
            },
        }
        with self._lock:
            self._accounts.append(account)
            self._write_locked()
        logger.success(f"Gmail email account added: '{account['label']}' - id {account['id']}")
        return self._safe_view(account)

    def list_safe(self) -> list[dict]:
        """All accounts with credentials stripped - safe for an API response."""
        with self._lock:
            return [self._safe_view(a) for a in self._accounts]

    def list_all(self) -> list[dict]:
        """
        Deep copies of every account INCLUDING credentials. For the fetch/
        poll path only - never expose this to an API response.
        """
        with self._lock:
            return copy.deepcopy(self._accounts)

    def get(self, account_id: str) -> dict | None:
        """
        Deep copy of one account INCLUDING credentials, or None if not found.
        For the fetch/poll path only.
        """
        with self._lock:
            for account in self._accounts:
                if account.get("id") == account_id:
                    return copy.deepcopy(account)
        return None

    def delete(self, account_id: str) -> bool:
        """Remove an account by id. Returns True if it existed."""
        with self._lock:
            before = len(self._accounts)
            self._accounts = [a for a in self._accounts if a.get("id") != account_id]
            removed = len(self._accounts) != before
            if removed:
                self._write_locked()
        if removed:
            logger.success(f"Email account removed: id {account_id}")
        return removed

    @staticmethod
    def _safe_view(account: dict) -> dict:
        return {field: account.get(field) for field in _SAFE_FIELDS}


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.remove()
    logger.add(
        sys.stderr,
        level="DEBUG",
        colorize=True,
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | {message}",
    )

    # Every cipher available on this platform is round-tripped, not just the
    # default: the portable path must be exercisable on Windows, or its first
    # real run would be on the server.
    _plaintext = b"lana-cipher-smoke-test"
    for _name, _cipher in ACCOUNT_CIPHERS.items():
        print(f"\n--- {_name} round-trip ---")
        if not _cipher.available():
            print(f"  skipped: {_name} is not available on this platform.")
            continue

        _blob = encode_store(_plaintext, _name)
        assert _plaintext not in _blob, "plaintext must not survive in the blob"
        assert _blob.startswith(_MAGIC), "every new store carries a header"
        assert decode_store(_blob) == _plaintext
        print(f"  round-trip OK ({len(_blob)} bytes, header names '{_name}').")

        print(f"--- {_name} tamper detection ---")
        _tampered = bytearray(_blob)
        _tampered[-1] ^= 0xFF
        try:
            decode_store(bytes(_tampered))
            print("  UNEXPECTED: tampered ciphertext decrypted without error!")
        except OSError as exc:
            print(f"  tampered ciphertext correctly rejected: {exc}")

    print(f"\n--- default cipher on this platform: {default_cipher_name()} ---")

    print("\n--- Store smoke ---")
    store = EmailAccountStore()
    store.load()
    print(f"Accounts on disk: {len(store.list_safe())}")

    smoke_account = store.add_imap_account(
        label="__smoke__",
        host="imap.example.com",
        port=993,
        username="smoke@example.com",
        password="not-a-real-password",
    )
    print(f"Added smoke account: {smoke_account}")
    print(f"Safe list now: {store.list_safe()}")
    assert store.delete(smoke_account["id"])
    print("Smoke account deleted.")
    print(f"Safe list after delete: {store.list_safe()}")
