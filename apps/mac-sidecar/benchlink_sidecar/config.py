"""Config contract for the Benchlink Mac sidecar.

Precedence (highest first): environment variable > TOML config file > default.

| Env var                         | TOML key             | Default          |
|---------------------------------|----------------------|------------------|
| BENCHLINK_BRIDGE_BASE_URL       | bridge.base_url      | unset (fail closed) |
| BENCHLINK_ACCESS_TOKEN          | bridge.access_token  | unset            |
| BENCHLINK_SIDECAR_LISTEN_HOST   | sidecar.listen_host  | 127.0.0.1        |
| BENCHLINK_SIDECAR_LISTEN_PORT   | sidecar.listen_port  | 8787             |
| BENCHLINK_SIDECAR_CONFIG        | (path to TOML file)  | unset            |
| BENCHLINK_ALLOW_PLACEHOLDER_HOST| bridge.allow_placeholder_host | false   |
| BENCHLINK_DEVICE_KEY_PATH       | bridge.device_key_path | ~/.config/benchlink/device_ed25519.pem (D3) |
| BENCHLINK_DEVICE_ID             | bridge.device_id     | hostname -s      |
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

ENV_BASE_URL = "BENCHLINK_BRIDGE_BASE_URL"
ENV_ACCESS_TOKEN = "BENCHLINK_ACCESS_TOKEN"
ENV_LISTEN_HOST = "BENCHLINK_SIDECAR_LISTEN_HOST"
ENV_LISTEN_PORT = "BENCHLINK_SIDECAR_LISTEN_PORT"
ENV_CONFIG_PATH = "BENCHLINK_SIDECAR_CONFIG"
ENV_ALLOW_PLACEHOLDER = "BENCHLINK_ALLOW_PLACEHOLDER_HOST"
ENV_DEVICE_KEY_PATH = "BENCHLINK_DEVICE_KEY_PATH"
ENV_DEVICE_ID = "BENCHLINK_DEVICE_ID"
DEFAULT_DEVICE_KEY_PATH = "~/.config/benchlink/device_ed25519.pem"

# Intended Tailscale HTTPS base (Larry FYI 2026-11-05): NOT live until Douglas
# clears Tailscale serve. Fail-closed unless BENCHLINK_ALLOW_PLACEHOLDER_HOST=1.
# Wrong-tailnet bridge.floyds-labs.ts.net is demoted but still refused.
PLACEHOLDER_BASE_URL = "https://inference.tail58d565.ts.net:19740"
PLACEHOLDER_HOSTS = {"inference.tail58d565.ts.net", "bridge.floyds-labs.ts.net"}

DEFAULT_LISTEN_HOST = "127.0.0.1"
DEFAULT_LISTEN_PORT = 8787
API_PREFIX = "/v1"

# Hard denylist: Grok Build Session API / Remote IM must never be an upstream.
FORBIDDEN_UPSTREAM_PORTS = {63301}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class ConfigError(Exception):
    """Raised when config is invalid or the upstream is not provisioned."""


@dataclass(frozen=True)
class SidecarConfig:
    base_url: str | None          # normalized, no trailing slash, no /v1 suffix
    access_token: str | None
    listen_host: str
    listen_port: int
    allow_placeholder_host: bool
    source: dict                  # key -> "env" | "file" | "default"
    device_key_path: str = DEFAULT_DEVICE_KEY_PATH
    device_id: str = "mac-sidecar"

    @property
    def upstream_configured(self) -> bool:
        return self.base_url is not None

    @property
    def api_base(self) -> str:
        """Return '{base}/v1' or raise ConfigError (fail closed)."""
        if not self.base_url:
            raise ConfigError(
                f"{ENV_BASE_URL} is unset. Intended once Douglas clears Tailscale "
                f"serve: {PLACEHOLDER_BASE_URL} (client appends /v1). Not live yet — "
                "refusing to call upstream (fail closed)."
            )
        host = urlparse(self.base_url).hostname or ""
        if host in PLACEHOLDER_HOSTS and not self.allow_placeholder_host:
            raise ConfigError(
                f"{ENV_BASE_URL}={self.base_url} is an unprovisioned Tailscale "
                "placeholder (intended once Douglas clears serve: "
                f"{PLACEHOLDER_BASE_URL}; client appends /v1). Refusing live call. "
                f"Set {ENV_ALLOW_PLACEHOLDER}=1 only after HTTPS serve is confirmed live."
            )
        return self.base_url + API_PREFIX


def _truthy(v) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def normalize_base_url(raw: str) -> str:
    url = raw.strip().rstrip("/")
    if url.endswith(API_PREFIX):
        url = url[: -len(API_PREFIX)]
    p = urlparse(url)
    if p.scheme not in {"http", "https"} or not p.hostname:
        raise ConfigError(f"{ENV_BASE_URL} must be an absolute http(s) URL, got {raw!r}")
    if p.port in FORBIDDEN_UPSTREAM_PORTS:
        raise ConfigError(
            f"{ENV_BASE_URL} points at port {p.port} (Grok Build Session API). Forbidden by D2."
        )
    if p.scheme == "http" and p.hostname not in LOOPBACK_HOSTS:
        raise ConfigError(
            f"{ENV_BASE_URL} must use https for non-loopback hosts (Tailscale HTTPS per D2)."
        )
    if p.query or p.fragment:
        raise ConfigError(f"{ENV_BASE_URL} must not include query/fragment")
    return url


def _load_file(path: str | None) -> dict:
    if not path:
        return {}
    p = Path(path).expanduser()
    if not p.is_file():
        raise ConfigError(f"{ENV_CONFIG_PATH}={path} does not exist")
    try:
        return tomllib.loads(p.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"Invalid TOML in {p}: {e}") from e


def load_config(env: dict | None = None) -> SidecarConfig:
    env = dict(os.environ if env is None else env)
    data = _load_file(env.get(ENV_CONFIG_PATH))
    bridge = data.get("bridge", {}) or {}
    sidecar = data.get("sidecar", {}) or {}
    source: dict = {}

    def pick(env_key, section, file_key, default):
        v = env.get(env_key)
        if v not in (None, ""):
            source[env_key] = "env"
            return v
        v = section.get(file_key)
        if v not in (None, ""):
            source[env_key] = "file"
            return v
        source[env_key] = "default"
        return default

    raw_base = pick(ENV_BASE_URL, bridge, "base_url", None)
    base_url = normalize_base_url(str(raw_base)) if raw_base else None
    token = pick(ENV_ACCESS_TOKEN, bridge, "access_token", None)
    host = str(pick(ENV_LISTEN_HOST, sidecar, "listen_host", DEFAULT_LISTEN_HOST))
    if host not in LOOPBACK_HOSTS:
        raise ConfigError(f"{ENV_LISTEN_HOST} must be loopback (127.0.0.1/localhost/::1), got {host!r}")
    try:
        port = int(pick(ENV_LISTEN_PORT, sidecar, "listen_port", DEFAULT_LISTEN_PORT))
    except ValueError as e:
        raise ConfigError(f"{ENV_LISTEN_PORT} must be an integer") from e
    if port in FORBIDDEN_UPSTREAM_PORTS or not (1 <= port <= 65535):
        raise ConfigError(f"{ENV_LISTEN_PORT}={port} is not allowed")
    allow = _truthy(pick(ENV_ALLOW_PLACEHOLDER, bridge, "allow_placeholder_host", False))
    key_path = str(pick(ENV_DEVICE_KEY_PATH, bridge, "device_key_path", DEFAULT_DEVICE_KEY_PATH))
    import socket
    device_id = str(pick(ENV_DEVICE_ID, bridge, "device_id",
                         (socket.gethostname().split(".")[0] or "mac-sidecar")))
    return SidecarConfig(base_url, token, host, port, allow, source, key_path, device_id)


def describe(cfg: SidecarConfig) -> dict:
    """Redacted, printable view of config (never prints the token)."""
    return {
        ENV_BASE_URL: cfg.base_url or "(unset — fail closed)",
        ENV_ACCESS_TOKEN: "(set, redacted)" if cfg.access_token else "(unset)",
        ENV_LISTEN_HOST: cfg.listen_host,
        ENV_LISTEN_PORT: cfg.listen_port,
        ENV_ALLOW_PLACEHOLDER: cfg.allow_placeholder_host,
        ENV_DEVICE_KEY_PATH: cfg.device_key_path,
        ENV_DEVICE_ID: cfg.device_id,
        "source": cfg.source,
    }


def ui_config(cfg: SidecarConfig) -> dict:
    """Allowlisted browser diagnostics. Keep describe() CLI-only."""
    from urllib.parse import urlsplit
    return {"bridge_host": urlsplit(cfg.base_url or "").hostname,
            "transport": "Tailscale HTTPS", "device_id": cfg.device_id}
