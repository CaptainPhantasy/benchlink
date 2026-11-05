"""Staff-adapter config contract (env only; names here, never values).

| Env var | Default | Notes |
|---|---|---|
| BENCHLINK_ADAPTER_TRANSPORT | webhook | webhook \\| fake (fake = smoke/tests only) |
| BENCHLINK_FAKE_TRANSPORT_MODE | echo | echo \\| fail \\| silent \\| offline \\| reject |
| BENCHLINK_TARGET_<KEY>_WEBHOOK_URL | unset | Grok Bot routine webhook URL per target (FLOYD, ETHAN, ...) |
| BENCHLINK_TARGET_<KEY>_SENDER_KEY | unset | Routine webhook sender key per target (secret; Douglas copies on inference) |
| BENCHLINK_WEBHOOK_SENDER_KEY_HEADER | Authorization | **TBD** — routine sender-key header name convention not confirmed |
| BENCHLINK_WEBHOOK_SENDER_KEY_SCHEME | Bearer | Prefix for the header value; empty = raw key. **TBD** with header |
| BENCHLINK_WEBHOOK_TIMEOUT_S | 10 | Per-dispatch HTTP timeout -> `timeout` |
| BENCHLINK_TASK_TIMEOUT_S | 600 | No final task.result by then -> task failed, error_class `timeout` |
| BENCHLINK_RESULT_SOURCE | drive_folder | drive_folder \\| local_folder \\| http_callback |
| BENCHLINK_RESULTS_DRIVE_FOLDER_ID | unset | Drive folder the staff bot writes `<task_id>.json` into |
| BENCHLINK_DRIVE_SA_KEY_PATH | unset | Dedicated Drive service-account JSON key (inference, outside repo, mode 0600); drive_folder refuses to start without it |
| BENCHLINK_RESULTS_LOCAL_DIR | unset | local_folder dir (tests/smoke; mimics Drive layout) |
| BENCHLINK_RESULTS_POLL_INTERVAL_S | 5 | Min seconds between result-source polls per task (0 for local) |
| BENCHLINK_CALLBACK_BASE_URL | derived | http_callback only: base URL the bot can reach (daemon listen addr if unset) |
| BENCHLINK_CALLBACK_TOKEN_TTL_S | 900 | http_callback per-task result token lifetime |
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlparse

from .targets import ROSTER

TRANSPORTS = {"webhook", "fake"}
FAKE_MODES = {"echo", "fail", "silent", "offline", "reject"}
RESULT_SOURCES = {"drive_folder", "local_folder", "http_callback"}

# Tailnet hosts that are not routine webhooks and must never be dispatched to
PLACEHOLDER_HOSTS = {"inference.tail58d565.ts.net", "bridge.floyds-labs.ts.net"}
FORBIDDEN_PORTS = {63301}
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class AdapterConfigError(Exception):
    pass


def webhook_url_env(key: str) -> str:
    return f"BENCHLINK_TARGET_{key.upper()}_WEBHOOK_URL"


def sender_key_env(key: str) -> str:
    return f"BENCHLINK_TARGET_{key.upper()}_SENDER_KEY"


@dataclass(frozen=True)
class TargetEndpoint:
    key: str
    webhook_url: str | None
    sender_key: str | None


@dataclass(frozen=True)
class AdapterConfig:
    transport: str = "webhook"
    fake_mode: str = "echo"
    targets: dict = field(default_factory=dict)          # key -> TargetEndpoint
    sender_key_header: str = "Authorization"
    sender_key_scheme: str = "Bearer"
    webhook_timeout_s: float = 10.0
    task_timeout_s: float = 600.0
    result_source: str = "drive_folder"
    drive_folder_id: str | None = None
    drive_sa_key_path: str | None = None
    local_dir: str | None = None
    poll_interval_s: float = 5.0
    callback_base_url: str | None = None
    callback_token_ttl_s: float = 900.0


def _validate_webhook(env_name: str, raw: str) -> str:
    url = raw.strip()
    p = urlparse(url)
    if p.scheme not in {"http", "https"} or not p.hostname:
        raise AdapterConfigError(f"{env_name} must be an absolute http(s) URL")
    if p.scheme == "http" and p.hostname not in LOOPBACK:
        raise AdapterConfigError(f"{env_name} must be https for non-loopback hosts")
    if p.hostname in PLACEHOLDER_HOSTS:
        raise AdapterConfigError(f"{env_name} points at a bridge tailnet host, not a routine webhook")
    if p.port in FORBIDDEN_PORTS:
        raise AdapterConfigError(f"{env_name} forbids port {p.port} (Build Session API)")
    return url


def _float(env, name, default):
    v = env.get(name)
    if v in (None, ""):
        return default
    try:
        f = float(v)
    except ValueError as e:
        raise AdapterConfigError(f"{name} must be a number") from e
    if f < 0:
        raise AdapterConfigError(f"{name} must be >= 0")
    return f


def load_adapter_config(env: dict | None = None) -> AdapterConfig:
    env = dict(os.environ if env is None else env)
    transport = (env.get("BENCHLINK_ADAPTER_TRANSPORT") or "webhook").strip().lower()
    if transport not in TRANSPORTS:
        raise AdapterConfigError(f"BENCHLINK_ADAPTER_TRANSPORT={transport!r} not in {sorted(TRANSPORTS)}")
    fake_mode = (env.get("BENCHLINK_FAKE_TRANSPORT_MODE") or "echo").strip().lower()
    if fake_mode not in FAKE_MODES:
        raise AdapterConfigError(f"BENCHLINK_FAKE_TRANSPORT_MODE={fake_mode!r} not in {sorted(FAKE_MODES)}")
    targets = {}
    for key in ROSTER:
        url = env.get(webhook_url_env(key)) or None
        if url:
            url = _validate_webhook(webhook_url_env(key), url)
        targets[key] = TargetEndpoint(key, url, env.get(sender_key_env(key)) or None)
    src = (env.get("BENCHLINK_RESULT_SOURCE") or "drive_folder").strip().lower()
    if src not in RESULT_SOURCES:
        raise AdapterConfigError(f"BENCHLINK_RESULT_SOURCE={src!r} not in {sorted(RESULT_SOURCES)}")
    header = env.get("BENCHLINK_WEBHOOK_SENDER_KEY_HEADER")
    header = "Authorization" if header in (None, "") else header.strip()
    scheme = env.get("BENCHLINK_WEBHOOK_SENDER_KEY_SCHEME")
    scheme = "Bearer" if scheme is None else scheme.strip()
    poll_default = 0.0 if src in {"local_folder", "http_callback"} else 5.0
    return AdapterConfig(
        transport=transport, fake_mode=fake_mode, targets=targets,
        sender_key_header=header, sender_key_scheme=scheme,
        webhook_timeout_s=_float(env, "BENCHLINK_WEBHOOK_TIMEOUT_S", 10.0),
        task_timeout_s=_float(env, "BENCHLINK_TASK_TIMEOUT_S", 600.0),
        result_source=src,
        drive_folder_id=env.get("BENCHLINK_RESULTS_DRIVE_FOLDER_ID") or None,
        drive_sa_key_path=env.get("BENCHLINK_DRIVE_SA_KEY_PATH") or None,
        local_dir=env.get("BENCHLINK_RESULTS_LOCAL_DIR") or None,
        poll_interval_s=_float(env, "BENCHLINK_RESULTS_POLL_INTERVAL_S", poll_default),
        callback_base_url=(env.get("BENCHLINK_CALLBACK_BASE_URL") or None),
        callback_token_ttl_s=_float(env, "BENCHLINK_CALLBACK_TOKEN_TTL_S", 900.0),
    )


def describe_adapter(cfg: AdapterConfig) -> dict:
    """Redacted view: URLs reduced to host, keys only as set/unset."""
    def host(u):
        return urlparse(u).hostname if u else None
    return {
        "transport": cfg.transport,
        "fake_mode": cfg.fake_mode if cfg.transport == "fake" else None,
        "targets": {k: {"webhook_host": host(t.webhook_url),
                        "sender_key": "(set, redacted)" if t.sender_key else "(unset)"}
                    for k, t in cfg.targets.items() if t.webhook_url or t.sender_key},
        "sender_key_header": cfg.sender_key_header + " (TBD convention)",
        "sender_key_scheme": cfg.sender_key_scheme or "(raw)",
        "webhook_timeout_s": cfg.webhook_timeout_s,
        "task_timeout_s": cfg.task_timeout_s,
        "result_source": cfg.result_source,
        "drive_folder_id": "(set)" if cfg.drive_folder_id else "(unset)",
        "drive_sa_key_path": "(set)" if cfg.drive_sa_key_path else "(unset)",
        "local_dir": cfg.local_dir,
        "poll_interval_s": cfg.poll_interval_s,
        "callback_base_url": cfg.callback_base_url or "(derived from listen addr)",
    }
