"""Transport — how a bridge task reaches a staff bot.

  webhook  POST the dispatch payload to the target's Grok Bot routine webhook
           (BENCHLINK_TARGET_<KEY>_WEBHOOK_URL + BENCHLINK_TARGET_<KEY>_SENDER_KEY).
           Sender-key header convention is TBD: header name from
           BENCHLINK_WEBHOOK_SENDER_KEY_HEADER (default Authorization) and value
           prefix BENCHLINK_WEBHOOK_SENDER_KEY_SCHEME (default Bearer).
  fake     smoke/tests only: behaves like a staff bot in-process and writes the
           result through the configured ResultSource (echo|fail|silent|offline|reject).
"""
from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from .config import AdapterConfig, sender_key_env, webhook_url_env
from .errors import BridgeError
from .results import ResultSource, post_callback


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Transport(ABC):
    name = "abstract"

    @abstractmethod
    def dispatch(self, target_key: str, payload: dict, request_id: str) -> dict:
        """Deliver payload; return a non-secret receipt or raise BridgeError."""


class WebhookTransport(Transport):
    name = "webhook"

    def __init__(self, cfg: AdapterConfig):
        self.cfg = cfg

    def _endpoint(self, key):
        ep = self.cfg.targets.get(key)
        if not ep or not ep.webhook_url:
            raise BridgeError("offline", f"{webhook_url_env(key)} is unset — target {key!r} "
                              "not wired (fail closed)")
        if not ep.sender_key:
            raise BridgeError("offline", f"{sender_key_env(key)} is unset — refusing unauthenticated "
                              "webhook dispatch (fail closed)")
        return ep

    def dispatch(self, target_key, payload, request_id):
        ep = self._endpoint(target_key)
        val = f"{self.cfg.sender_key_scheme} {ep.sender_key}" if self.cfg.sender_key_scheme else ep.sender_key
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "User-Agent": "benchlink-staff-adapter/0.2.0",
                   "X-Request-Id": request_id,
                   "X-Benchlink-Task-Id": payload["task"]["task_id"],
                   self.cfg.sender_key_header: val}
        req = urllib.request.Request(ep.webhook_url, data=json.dumps(payload).encode(),
                                     method="POST", headers=headers)
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.webhook_timeout_s) as r:
                status = r.status
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise BridgeError("auth.denied", f"routine webhook for {target_key!r} rejected "
                                  f"sender key (HTTP {e.code})") from None
            if e.code == 404:
                raise BridgeError("routing", f"routine webhook for {target_key!r} not found (HTTP 404)") from None
            if e.code in (408, 504):
                raise BridgeError("timeout", f"routine webhook for {target_key!r} timed out (HTTP {e.code})") from None
            raise BridgeError("offline", f"routine webhook for {target_key!r} HTTP {e.code}",
                              retryable=e.code >= 500) from None
        except (TimeoutError, socket.timeout):
            raise BridgeError("timeout", f"routine webhook for {target_key!r} exceeded "
                              f"{self.cfg.webhook_timeout_s}s") from None
        except urllib.error.URLError as e:
            if isinstance(e.reason, (TimeoutError, socket.timeout)):
                raise BridgeError("timeout", f"routine webhook for {target_key!r} exceeded "
                                  f"{self.cfg.webhook_timeout_s}s") from None
            raise BridgeError("offline", f"routine webhook for {target_key!r} unreachable: {e.reason}") from None
        except OSError as e:
            raise BridgeError("offline", f"routine webhook for {target_key!r} unreachable: {e}") from None
        return {"transport": self.name, "http_status": status,
                "elapsed_ms": int((time.monotonic() - t0) * 1000)}


class FakeTransport(Transport):
    """In-process stand-in for a staff bot. Never for production."""
    name = "fake"

    def __init__(self, mode: str, source: ResultSource, delay_s: float = 0.0):
        self.mode = mode
        self.source = source
        self.delay_s = delay_s
        self.dispatched: list[dict] = []

    def dispatch(self, target_key, payload, request_id):
        if self.mode == "offline":
            raise BridgeError("offline", f"fake transport: target {target_key!r} unreachable")
        if self.mode == "reject":
            raise BridgeError("auth.denied", f"fake transport: target {target_key!r} rejected sender key")
        self.dispatched.append(payload)
        if self.mode in {"echo", "fail"}:
            threading.Thread(target=self._be_the_bot, args=(payload,), daemon=True).start()
        return {"transport": self.name, "mode": self.mode}

    def _be_the_bot(self, payload):
        if self.delay_s:
            time.sleep(self.delay_s)
        task = payload["task"]
        result = dict(payload["result_template"])
        ok = self.mode == "echo"
        result.update({
            "status": "succeeded" if ok else "failed",
            "summary": (f"[fake {payload['target']['key']}] echoed: {task['goal']}" if ok
                        else f"[fake {payload['target']['key']}] could not complete task"),
            "artifact_links": [{"title": "fake artifact", "url": "https://example.invalid/fake"}] if ok else [],
            "error_class": None if ok else "internal",
            "created_at": now_iso(),
        })
        delivery = payload["result_delivery"]
        if delivery["mode"] == "http_callback":
            post_callback(delivery, result)
        else:
            self.source.simulate_bot_write(task["task_id"], result)


def build_transport(cfg: AdapterConfig, source: ResultSource) -> Transport:
    if cfg.transport == "fake":
        return FakeTransport(cfg.fake_mode, source)
    return WebhookTransport(cfg)
