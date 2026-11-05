"""Config for bridge-daemon (inference runtime).

| Env var                            | Default              | Notes |
|------------------------------------|----------------------|-------|
| BENCHLINK_BRIDGE_LISTEN_HOST       | 127.0.0.1            | Loopback on Computer; inference may bind broader later |
| BENCHLINK_BRIDGE_LISTEN_PORT       | 8790                 | Avoids Mac sidecar 8787; not 63301 (Build Session API) |
| BENCHLINK_BRIDGE_PUBLIC_BASE_URL   | unset                | Intended: https://inference.tail58d565.ts.net:19740 (unset until serve live) |
| BENCHLINK_ALLOW_PLACEHOLDER_HOST   | false                | Same placeholder policy as sidecar |
| BENCHLINK_DEVICE_ALLOWLIST_PATH    | ~/.config/benchlink/device_allowlist.json | D3 enrollment (outside repo) |
| BENCHLINK_REQUIRE_TAILSCALE_IDENTITY | false              | MUST be 1 in prod (Tailscale-User-Login header) |
| BENCHLINK_ACCESS_TTL_SECONDS       | 900                  | Short-lived bearer TTL (no refresh in MVP) |
Staff-adapter env (transport / result source / timeouts): see benchlink_adapter.config.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

ENV_LISTEN_HOST = "BENCHLINK_BRIDGE_LISTEN_HOST"
ENV_LISTEN_PORT = "BENCHLINK_BRIDGE_LISTEN_PORT"
ENV_PUBLIC_BASE = "BENCHLINK_BRIDGE_PUBLIC_BASE_URL"
ENV_ALLOW_PLACEHOLDER = "BENCHLINK_ALLOW_PLACEHOLDER_HOST"
ENV_ALLOWLIST = "BENCHLINK_DEVICE_ALLOWLIST_PATH"
ENV_REQUIRE_TS = "BENCHLINK_REQUIRE_TAILSCALE_IDENTITY"
ENV_ACCESS_TTL = "BENCHLINK_ACCESS_TTL_SECONDS"
DEFAULT_ALLOWLIST = "~/.config/benchlink/device_allowlist.json"

PLACEHOLDER_BASE_URL = "https://inference.tail58d565.ts.net:19740"
PLACEHOLDER_HOSTS = {"inference.tail58d565.ts.net", "bridge.floyds-labs.ts.net"}
FORBIDDEN_PORTS = {63301}
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8790


class ConfigError(Exception):
    pass


def _truthy(v) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class DaemonConfig:
    listen_host: str
    listen_port: int
    public_base_url: str | None
    allow_placeholder_host: bool
    device_allowlist_path: str = DEFAULT_ALLOWLIST
    require_tailscale_identity: bool = False
    access_ttl_s: float = 900.0

    @property
    def public_configured(self) -> bool:
        if not self.public_base_url:
            return False
        host = urlparse(self.public_base_url).hostname or ""
        if host in PLACEHOLDER_HOSTS and not self.allow_placeholder_host:
            return False
        return True


def normalize_public_base(raw: str) -> str:
    url = raw.strip().rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3]
    p = urlparse(url)
    if p.scheme not in {"http", "https"} or not p.hostname:
        raise ConfigError(f"{ENV_PUBLIC_BASE} must be absolute http(s) URL, got {raw!r}")
    if p.port in FORBIDDEN_PORTS:
        raise ConfigError(f"{ENV_PUBLIC_BASE} forbids port {p.port} (Build Session API)")
    return url


def load_config(env: dict | None = None) -> DaemonConfig:
    env = dict(os.environ if env is None else env)
    host = env.get(ENV_LISTEN_HOST) or DEFAULT_HOST
    try:
        port = int(env.get(ENV_LISTEN_PORT) or DEFAULT_PORT)
    except ValueError as e:
        raise ConfigError(f"{ENV_LISTEN_PORT} must be an integer") from e
    if port in FORBIDDEN_PORTS or not (1 <= port <= 65535):
        raise ConfigError(f"{ENV_LISTEN_PORT}={port} is not allowed")
    raw_pub = env.get(ENV_PUBLIC_BASE) or None
    if raw_pub is not None and str(raw_pub).strip() == "":
        raw_pub = None
    public = normalize_public_base(str(raw_pub)) if raw_pub else None
    allow = _truthy(env.get(ENV_ALLOW_PLACEHOLDER, "0"))
    allowlist = env.get(ENV_ALLOWLIST) or DEFAULT_ALLOWLIST
    req_ts = _truthy(env.get(ENV_REQUIRE_TS, "0"))
    try:
        ttl = float(env.get(ENV_ACCESS_TTL) or 900)
    except ValueError as e:
        raise ConfigError(f"{ENV_ACCESS_TTL} must be a number") from e
    if ttl <= 0:
        raise ConfigError(f"{ENV_ACCESS_TTL} must be > 0")
    return DaemonConfig(host, port, public, allow, allowlist, req_ts, ttl)


def describe(cfg: DaemonConfig) -> dict:
    return {
        ENV_LISTEN_HOST: cfg.listen_host,
        ENV_LISTEN_PORT: cfg.listen_port,
        ENV_PUBLIC_BASE: cfg.public_base_url or "(unset — until Larry provisions Tailscale DNS)",
        ENV_ALLOW_PLACEHOLDER: cfg.allow_placeholder_host,
        "public_configured": cfg.public_configured,
        ENV_ALLOWLIST: cfg.device_allowlist_path,
        ENV_REQUIRE_TS: cfg.require_tailscale_identity,
        ENV_ACCESS_TTL: cfg.access_ttl_s,
        "placeholder_logical": PLACEHOLDER_BASE_URL,
    }
