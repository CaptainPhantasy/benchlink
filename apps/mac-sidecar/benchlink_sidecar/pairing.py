"""D3 device-key pairing client (Claire spec).

Key: Ed25519 PKCS8 PEM at BENCHLINK_DEVICE_KEY_PATH (0600). The private key is
never printed or logged; `generate-key` prints only device_id + public key
(base64url, no padding) as an allowlist snippet for out-of-band enrollment.
Flow: auth.pair.request -> auth.pair.challenge -> sign UTF-8 "challenge.nonce"
-> auth.pair.complete -> auth.session.token (short_lived, no refresh).
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import urllib.error
import urllib.request
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .config import ConfigError, SidecarConfig

ALL_SCOPES = ["session.create", "session.message", "session.close", "task.create"]


class PairingError(Exception):
    def __init__(self, code: str, message: str, http_status: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def public_key_b64url(key: Ed25519PrivateKey) -> str:
    return b64url(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


def generate_key(path: str, force: bool = False) -> Ed25519PrivateKey:
    p = Path(path).expanduser()
    if p.exists() and not force:
        raise ConfigError(f"{p} already exists (use --force to overwrite)")
    p.parent.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    os.chmod(p, 0o600)
    return key


def load_key(path: str) -> Ed25519PrivateKey:
    p = Path(path).expanduser()
    if not p.is_file():
        raise ConfigError(f"device key not found at BENCHLINK_DEVICE_KEY_PATH={p}; run generate-key and enroll")
    key = serialization.load_pem_private_key(p.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ConfigError("BENCHLINK_DEVICE_KEY_PATH is not an Ed25519 private key")
    return key


def allowlist_snippet(device_id: str, key: Ed25519PrivateKey, tailscale_identity: str | None = None) -> dict:
    entry = {"public_key_b64url": public_key_b64url(key)}
    if tailscale_identity:
        entry["tailscale_identity"] = tailscale_identity
    return {device_id: entry}


def _post(url: str, body: dict, timeout: float) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def pair(cfg: SidecarConfig, scopes: list[str] | None = None, timeout: float = 5.0,
         client_version: str = "0.2.0-mvp") -> dict:
    """Return auth.session.token dict. Raises PairingError(code=frozen error class)."""
    api = cfg.api_base  # fail closed
    key = load_key(cfg.device_key_path)
    nonce = secrets.token_hex(16)
    st, ch = _post(api + "/auth/pair", {"v": "1", "type": "auth.pair.request", "device_id": cfg.device_id,
                                        "client": "grok.app", "client_version": client_version,
                                        "nonce": nonce}, timeout)
    if st != 200 or ch.get("type") != "auth.pair.challenge":
        raise PairingError(ch.get("code", "internal"), ch.get("message", f"pair request HTTP {st}"), st)
    proof = b64url(key.sign(f"{ch['challenge']}.{ch.get('nonce') or nonce}".encode("utf-8")))
    st, tok = _post(api + "/auth/pair", {"v": "1", "type": "auth.pair.complete", "device_id": cfg.device_id,
                                         "proof": proof, "scopes": scopes or ALL_SCOPES}, timeout)
    if st != 200 or tok.get("type") != "auth.session.token":
        raise PairingError(tok.get("code", "internal"), tok.get("message", f"pair complete HTTP {st}"), st)
    return tok
