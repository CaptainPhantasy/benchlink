"""D3 auth (Claire spec, acked 2026-11-05).

POST /v1/auth/pair is ONE endpoint dispatching on body `type`:
  auth.pair.request  -> auth.pair.challenge  (TTL 60 s, single-use, one per device)
  auth.pair.complete -> auth.session.token   (opaque secrets.token_urlsafe(32),
                                              in-memory, TTL BENCHLINK_ACCESS_TTL_SECONDS=900,
                                              NO refresh token: client re-pairs with device key)
proof = base64url(no padding) Ed25519 signature over UTF-8 bytes of
        challenge + "." + nonce   (nonce = the challenge's nonce; we echo the request nonce)
Enrollment out-of-band: allowlist JSON {device_id: {public_key_b64url, tailscale_identity?, scopes?}}
at BENCHLINK_DEVICE_ALLOWLIST_PATH (default outside the repo).
BENCHLINK_REQUIRE_TAILSCALE_IDENTITY=1 -> `Tailscale-User-Login` header (set by tailscale serve)
must match the allowlist entry (MUST be on in prod; off for loopback tests).
Logs never contain Authorization / access_token / proof / challenge; only device_id,
sha256(token)[:6], expires_at, outcome.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from benchlink_adapter.errors import BridgeError

log = logging.getLogger("benchlink.daemon.auth")

ALL_SCOPES = ("session.create", "session.message", "session.close", "task.create")
CHALLENGE_TTL_S = 60.0
TS_LOGIN_HEADER = "Tailscale-User-Login"
QUERY_TOKEN_KEYS = {"access_token", "token", "bearer", "auth"}


def b64url_decode(s: str) -> bytes:
    s = s.strip()
    if "=" in s:
        raise ValueError("padding not allowed")
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:6]


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def signing_input(challenge: str, nonce: str) -> bytes:
    return f"{challenge}.{nonce}".encode("utf-8")


@dataclass
class Principal:
    device_id: str
    scopes: tuple
    expires_at: float
    tailscale_identity: str | None
    fingerprint: str


class AuthService:
    def __init__(self, allowlist_path: str, require_tailscale_identity: bool = False,
                 access_ttl_s: float = 900.0, challenge_ttl_s: float = CHALLENGE_TTL_S,
                 clock=time.time):
        self.allowlist_path = Path(allowlist_path).expanduser()
        self.require_ts = require_tailscale_identity
        self.access_ttl_s = access_ttl_s
        self.challenge_ttl_s = challenge_ttl_s
        self.clock = clock
        self._pending: dict[str, dict] = {}   # device_id -> {challenge, nonce, expires_at, ts}
        self._tokens: dict[str, Principal] = {}
        self._lock = threading.Lock()

    # -------------------------------------------------------------- allowlist
    def _allowlist(self) -> dict:
        """Re-read on every pair so out-of-band enrollment needs no restart."""
        if not self.allowlist_path.is_file():
            return {}
        try:
            data = json.loads(self.allowlist_path.read_text())
        except ValueError:
            log.error("device allowlist unreadable path=%s", self.allowlist_path)
            raise BridgeError("internal", "device allowlist is not valid JSON") from None
        return data if isinstance(data, dict) else {}

    def _entry(self, device_id: str) -> dict:
        entry = self._allowlist().get(device_id)
        if not isinstance(entry, dict) or not entry.get("public_key_b64url"):
            raise BridgeError("auth.denied", "device not enrolled")
        return entry

    def _check_ts(self, entry: dict, ts_login: str | None, device_id: str):
        if not self.require_ts:
            return
        want = entry.get("tailscale_identity")
        if not ts_login:
            raise BridgeError("auth.denied", f"{TS_LOGIN_HEADER} header required (Tailscale serve identity)")
        if not want or not hmac.compare_digest(want.encode(), ts_login.encode("utf-8", "replace")):
            raise BridgeError("auth.denied", "Tailscale identity does not match enrolled device")

    def _audit(self, event, device_id, outcome, fp=None, exp=None):
        log.info("auth event=%s device_id=%s tok=%s expires_at=%s outcome=%s",
                 event, device_id, fp or "-", iso(exp) if exp else "-", outcome)

    # -------------------------------------------------------------- pairing
    def pair_request(self, body: dict, ts_login: str | None) -> dict:
        did = body["device_id"]
        try:
            entry = self._entry(did)
            self._check_ts(entry, ts_login, did)
        except BridgeError as e:
            self._audit("pair.request", did, e.code)
            raise
        now = self.clock()
        ch = {"challenge": secrets.token_urlsafe(32), "nonce": body["nonce"],
              "expires_at": now + self.challenge_ttl_s}
        with self._lock:
            self._pending[did] = ch   # replaces any outstanding challenge
        self._audit("pair.request", did, "challenge_issued", exp=ch["expires_at"])
        return {"v": "1", "type": "auth.pair.challenge", "nonce": ch["nonce"],
                "challenge": ch["challenge"], "expires_at": iso(ch["expires_at"])}

    def pair_complete(self, body: dict, ts_login: str | None) -> dict:
        did = body["device_id"]
        try:
            with self._lock:
                ch = self._pending.pop(did, None)     # single-use, consumed on ANY attempt
            if ch is None:
                raise BridgeError("auth.denied", "no pending challenge (never issued, replayed, or consumed)")
            if self.clock() > ch["expires_at"]:
                raise BridgeError("auth.expired", "pairing challenge expired; request a new one")
            entry = self._entry(did)
            self._check_ts(entry, ts_login, did)
            try:
                pub = Ed25519PublicKey.from_public_bytes(b64url_decode(entry["public_key_b64url"]))
            except (ValueError, binascii.Error):
                raise BridgeError("internal", "enrolled public key is malformed") from None
            try:
                sig = b64url_decode(body["proof"])
                pub.verify(sig, signing_input(ch["challenge"], ch["nonce"]))
            except (InvalidSignature, ValueError, binascii.Error):
                raise BridgeError("auth.denied", "proof signature invalid") from None
            allowed = set(entry.get("scopes") or ALL_SCOPES)
            granted = tuple(s for s in ALL_SCOPES if s in set(body["scopes"]) and s in allowed)
            if not granted:
                raise BridgeError("auth.denied", "none of the requested scopes are allowed for this device")
        except BridgeError as e:
            self._audit("pair.complete", did, e.code)
            raise
        token = secrets.token_urlsafe(32)
        exp = self.clock() + self.access_ttl_s
        fp = token_fingerprint(token)
        with self._lock:
            # identity bound at pairing = the Tailscale-User-Login seen now (None on loopback)
            self._tokens[token] = Principal(did, granted, exp, ts_login or None, fp)
        self._audit("pair.complete", did, "token_issued scopes=" + ",".join(granted), fp, exp)
        return {"v": "1", "type": "auth.session.token", "token_type": "short_lived",
                "access_token": token, "expires_at": iso(exp)}

    # -------------------------------------------------------------- bearer
    def authenticate(self, authorization: str | None, query_keys: set, required_scope: str | None,
                     ts_login: str | None = None) -> tuple[Principal, str]:
        if query_keys & QUERY_TOKEN_KEYS:
            self._audit("bearer", "-", "auth.denied(query_token)")
            raise BridgeError("auth.denied", "tokens in the query string are rejected; use Authorization: Bearer")
        if not authorization or not authorization.startswith("Bearer ") or len(authorization) <= 7:
            raise BridgeError("auth.unpaired", "Authorization: Bearer <short-lived token> required; pair first")
        token = authorization[7:].strip()
        with self._lock:
            p = self._tokens.get(token)
        if p is None:
            self._audit("bearer", "-", "auth.unpaired(unknown_token)", token_fingerprint(token))
            raise BridgeError("auth.unpaired", "token not recognised by this bridge; re-pair")
        if self.clock() > p.expires_at:
            with self._lock:
                self._tokens.pop(token, None)
            self._audit("bearer", p.device_id, "auth.expired", p.fingerprint, p.expires_at)
            raise BridgeError("auth.expired", "access token expired; re-pair with device key")
        if self.require_ts:
            if not ts_login or not p.tailscale_identity or not hmac.compare_digest(
                    p.tailscale_identity.encode(), ts_login.encode("utf-8", "replace")):
                self._audit("bearer", p.device_id, "auth.denied(ts_identity)", p.fingerprint)
                raise BridgeError("auth.denied", "Tailscale identity does not match token's device")
        if required_scope and required_scope not in p.scopes:
            self._audit("bearer", p.device_id, f"auth.denied(scope {required_scope})", p.fingerprint)
            raise BridgeError("auth.denied", f"token lacks scope {required_scope!r}")
        return p, token

    def has_active_tokens(self) -> bool:
        now = self.clock()
        with self._lock:
            return any(p.expires_at > now for p in self._tokens.values())
