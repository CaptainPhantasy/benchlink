"""Bridge client for openapi.v1.json (0.2.0-mvp). Stdlib only.

Wired operations (path relative to {BENCHLINK_BRIDGE_BASE_URL}/v1):
  health          GET  /health
  authPair        POST /auth/pair              body: auth.pair.request
  sessionCreate   POST /sessions               body: session.create
  sessionAttach   POST /sessions/{id}/attach   body: session.attach
  sessionMessage  POST /sessions/{id}/messages body: session.message
  sessionEvents   GET  /sessions/{id}/events
  sessionClose    POST /sessions/{id}/close    body: session.close
  taskCreate      POST /tasks                  body: task.create
  taskGet         GET  /tasks/{task_id}

Every call can be built as a dry-run request (no network). Live calls go
through SidecarConfig.api_base, which fails closed when the base URL is unset
or is the unprovisioned placeholder.
"""
from __future__ import annotations

import json
import re
import socket
import uuid
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import quote

from .config import ConfigError, SidecarConfig
from .http_transport import open_no_redirects
from .task_result import is_task_result

DRY_RUN_BASE = "{BENCHLINK_BRIDGE_BASE_URL}/v1"
REDACTED = "<redacted>"
DRY_TOKEN = "tok_dry_run_placeholder"


@dataclass
class PreparedRequest:
    operation_id: str
    method: str
    path: str                 # OpenAPI path, e.g. /tasks/{task_id}
    url: str
    headers: dict = field(default_factory=dict)
    body: dict | None = None

    def redacted(self) -> dict:
        h = dict(self.headers)
        if "Authorization" in h:
            h["Authorization"] = "Bearer " + REDACTED
        b = dict(self.body) if self.body else None
        if b and "access_token" in b:
            b["access_token"] = REDACTED
        return {"operationId": self.operation_id, "method": self.method,
                "path": self.path, "url": self.url, "headers": h, "body": b}


class AuthNotPaired(ConfigError):
    """No bearer available and no device key to pair with (maps to auth.unpaired)."""


class RequestTimeout(TimeoutError):
    """A local RPC deadline, not proof the submitted task failed upstream."""
    def __init__(self, operation_id: str, seconds: float):
        self.operation_id = operation_id
        super().__init__(f"Bridge {operation_id} reply exceeded {seconds:g}s")

class UnacknowledgedDispatch(OSError):
    """An untyped gateway failure cannot prove task.create was rejected."""
    def __init__(self, http_status: int, code: str):
        self.http_status, self.code = http_status, code
        super().__init__(f"Task acknowledgement unavailable (HTTP {http_status}); task may exist")

def _is_frozen_error(body) -> bool:
    """Validate the complete 0.2.0-mvp error envelope before trusting rejection.

    Stdlib mirror of error.schema.json/common.schema.json; contract-oracle tests
    exercise required/extra fields, types and every frozen error class.
    A proxy's partial/unknown body is NOT proof that task.create was rejected.
    """
    fields = {"v", "type", "code", "message", "retryable", "correlation_id"}
    codes = {"auth.expired", "auth.denied", "auth.unpaired", "session.not_found",
             "task.not_found", "validation", "timeout", "offline", "routing", "internal"}
    return bool(isinstance(body, dict) and set(body) == fields
                and body["v"] == "1" and body["type"] == "error"
                and isinstance(body["code"], str) and body["code"] in codes
                and isinstance(body["message"], str) and isinstance(body["retryable"], bool)
                and isinstance(body["correlation_id"], str)
                and re.search(r"^[A-Za-z0-9_\-.:]+$", body["correlation_id"]))


class BridgeClient:
    def __init__(self, cfg: SidecarConfig, timeout: float = 5.0, dispatch_timeout: float = 15.0):
        self.cfg = cfg
        self.timeout = timeout
        # Default webhook dispatch is synchronous up to 10s; leave reply margin.
        self.dispatch_timeout = dispatch_timeout
        self._live_token: str | None = cfg.access_token
        self.pair_count = 0

    # ---- D3 pairing ---------------------------------------------------------
    def can_pair(self) -> bool:
        from pathlib import Path
        return Path(self.cfg.device_key_path).expanduser().is_file()

    def pair(self) -> dict:
        """Pair with the device key; keeps the bearer in memory only."""
        from .pairing import pair
        tok = pair(self.cfg, timeout=self.timeout)
        self._live_token = tok["access_token"]
        self.pair_count += 1
        return {"token_type": tok["token_type"], "expires_at": tok["expires_at"]}

    def _apply_token(self, req: PreparedRequest) -> None:
        req.headers["Authorization"] = f"Bearer {self._live_token}"
        if req.body is not None and "access_token" in req.body:
            req.body["access_token"] = self._live_token

    # ---- request builders -------------------------------------------------
    def _base(self, dry_run: bool) -> str:
        if dry_run and not self.cfg.upstream_configured:
            return DRY_RUN_BASE
        return self.cfg.api_base if not dry_run else (self.cfg.base_url + "/v1")

    def _token(self) -> str:
        return self._live_token or DRY_TOKEN

    def _headers(self, auth: bool, json_body: bool = False) -> dict:
        h = {"Accept": "application/json", "User-Agent": "benchlink-mac-sidecar/0.2.0"}
        if auth:
            h["Authorization"] = f"Bearer {self._token()}"
        if json_body or auth:
            h["Content-Type"] = "application/json"
        return h

    def build_health(self, dry_run: bool = False) -> PreparedRequest:
        return PreparedRequest("health", "GET", "/health",
                               self._base(dry_run) + "/health", self._headers(False))

    def build_auth_pair(self, device_id: str, client_version: str = "0.2.0",
                        nonce: str = "nonce_dry_run_01",
                        tailscale_identity: str | None = None,
                        dry_run: bool = False) -> PreparedRequest:
        body = {"v": "1", "type": "auth.pair.request",
                "device_id": device_id, "client": "grok.app",
                "client_version": client_version, "nonce": nonce}
        if tailscale_identity:
            body["tailscale_identity"] = tailscale_identity
        return PreparedRequest("authPair", "POST", "/auth/pair",
                               self._base(dry_run) + "/auth/pair",
                               self._headers(False, json_body=True), body)

    def build_session_create(self, target_agent: str = "floyd",
                             metadata: dict | None = None,
                             dry_run: bool = False) -> PreparedRequest:
        body = {"v": "1", "type": "session.create",
                "access_token": self._token(),
                "intent": "staff_task", "target_agent": target_agent}
        if metadata:
            body["metadata"] = {k: str(v) for k, v in metadata.items()}
        return PreparedRequest("sessionCreate", "POST", "/sessions",
                               self._base(dry_run) + "/sessions", self._headers(True), body)

    def build_session_attach(self, session_id: str,
                             dry_run: bool = False) -> PreparedRequest:
        body = {"v": "1", "type": "session.attach",
                "access_token": self._token(), "session_id": session_id}
        path = f"/sessions/{session_id}/attach"
        return PreparedRequest(
            "sessionAttach", "POST", "/sessions/{session_id}/attach",
            self._base(dry_run) + "/sessions/" + quote(session_id, safe="") + "/attach",
            self._headers(True), body)

    def build_session_message(self, session_id: str, message_id: str, text: str,
                              dry_run: bool = False) -> PreparedRequest:
        body = {"v": "1", "type": "session.message",
                "access_token": self._token(),
                "session_id": session_id, "message_id": message_id,
                "role": "user",
                "content": [{"type": "text", "text": text}]}
        return PreparedRequest(
            "sessionMessage", "POST", "/sessions/{session_id}/messages",
            self._base(dry_run) + "/sessions/" + quote(session_id, safe="") + "/messages",
            self._headers(True), body)

    def build_session_events(self, session_id: str,
                             dry_run: bool = False) -> PreparedRequest:
        return PreparedRequest(
            "sessionEvents", "GET", "/sessions/{session_id}/events",
            self._base(dry_run) + "/sessions/" + quote(session_id, safe="") + "/events",
            self._headers(True))

    def build_session_close(self, session_id: str, reason: str = "user_done",
                            dry_run: bool = False) -> PreparedRequest:
        body = {"v": "1", "type": "session.close",
                "access_token": self._token(),
                "session_id": session_id, "reason": reason}
        return PreparedRequest(
            "sessionClose", "POST", "/sessions/{session_id}/close",
            self._base(dry_run) + "/sessions/" + quote(session_id, safe="") + "/close",
            self._headers(True), body)

    def build_task_create(self, session_id: str, task_id: str, goal: str,
                          target_agent: str = "floyd", priority: str = "normal",
                          reply_destination: str = "bridge_status",
                          context_refs: list | None = None,
                          dry_run: bool = False) -> PreparedRequest:
        body = {"v": "1", "type": "task.create",
                "access_token": self._token(),
                "session_id": session_id, "task_id": task_id,
                "target_agent": target_agent, "goal": goal,
                "priority": priority, "reply_destination": reply_destination}
        if context_refs:
            body["context_refs"] = context_refs
        return PreparedRequest("taskCreate", "POST", "/tasks",
                               self._base(dry_run) + "/tasks", self._headers(True), body)

    def build_task_get(self, task_id: str, dry_run: bool = False) -> PreparedRequest:
        return PreparedRequest("taskGet", "GET", "/tasks/{task_id}",
                               self._base(dry_run) + "/tasks/" + quote(task_id, safe=""),
                               self._headers(True))

    def all_dry_run_shapes(self) -> list[PreparedRequest]:
        """One PreparedRequest per OpenAPI operationId (bodies use dry-run placeholders)."""
        return [
            self.build_health(dry_run=True),
            self.build_auth_pair("mac-sidecar-demo", dry_run=True),
            self.build_session_create("floyd", {"origin": "mac-sidecar"}, dry_run=True),
            self.build_session_attach("sess_demo_1", dry_run=True),
            self.build_session_message("sess_demo_1", "msg_demo_1", "hello", dry_run=True),
            self.build_session_events("sess_demo_1", dry_run=True),
            self.build_session_close("sess_demo_1", dry_run=True),
            self.build_task_create("sess_demo_1", "task_demo_1",
                                   "Confirm bridge canary routing", dry_run=True),
            self.build_task_get("task_demo_1", dry_run=True),
        ]

    # ---- live send (fails closed) ------------------------------------------
    def send(self, req: PreparedRequest) -> tuple[int, dict | list]:
        api_base = self.cfg.api_base  # raises ConfigError when unset/placeholder
        if not req.url.startswith(api_base):
            raise ConfigError("Prepared request was built in dry-run mode; rebuild with dry_run=False")
        needs_auth = bool(req.headers.get("Authorization"))
        if needs_auth:
            if not self._live_token:
                if not self.can_pair():
                    raise AuthNotPaired("BENCHLINK_ACCESS_TOKEN is unset; pair the device first (D3). "
                                        "No device key at BENCHLINK_DEVICE_KEY_PATH.")
                err = self._pair_or_error()
                if err:
                    return err
            self._apply_token(req)
        status, body = self._raw_send(req)
        # re-pair ONCE on auth.expired / auth.unpaired (no refresh token in MVP)
        if (needs_auth and req.operation_id != "taskCreate" and status == 401 and isinstance(body, dict)
                and body.get("code") in {"auth.expired", "auth.unpaired"} and self.can_pair()):
            err = self._pair_or_error()
            if err:
                return err
            self._apply_token(req)
            status, body = self._raw_send(req)
        return status, body

    def _pair_or_error(self):
        from .pairing import PairingError
        try:
            self.pair()
            return None
        except PairingError as e:
            return (e.http_status or 401), {"v": "1", "type": "error", "code": e.code,
                                            "message": "pairing failed: " + e.message,
                                            "retryable": False, "correlation_id": "sc_pair"}

    def _raw_send(self, req: PreparedRequest):
        data = json.dumps(req.body).encode() if req.body is not None else None
        r = urllib.request.Request(req.url, data=data, method=req.method, headers=req.headers)
        budget = max(self.timeout, self.dispatch_timeout) if req.operation_id == "taskCreate" else self.timeout
        try:
            with open_no_redirects(r, timeout=budget) as resp:
                raw = resp.read() or b"{}"
                try:
                    body = json.loads(raw)
                except ValueError:
                    if req.operation_id == "taskCreate":
                        raise UnacknowledgedDispatch(502, "offline") from None
                    raise
                if req.operation_id == "taskCreate" and (
                        not 200 <= resp.status < 300 or not is_task_result(body)
                        or body.get("task_id") != (req.body or {}).get("task_id")
                        or body.get("session_id") != (req.body or {}).get("session_id")):
                    raise UnacknowledgedDispatch(502, "offline")
                return resp.status, body
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read() or b"{}")
            except ValueError:
                body = {}
            except (TimeoutError, socket.timeout):
                raise RequestTimeout(req.operation_id, budget) from None
            except urllib.error.URLError as body_error:
                if isinstance(body_error.reason, (TimeoutError, socket.timeout)):
                    raise RequestTimeout(req.operation_id, budget) from None
                raise
            if _is_frozen_error(body):
                return e.code, body
            code = "timeout" if e.code in {408, 504} else "offline" if e.code >= 500 and e.code != 501 else "internal"
            if req.operation_id == "taskCreate":
                # Any malformed rejection, regardless of status, may have lost
                # an accepted dispatch. Never make a fresh POST from this state.
                raise UnacknowledgedDispatch(e.code, "timeout" if e.code in {408, 504} else "offline") from None
            return e.code, {"v": "1", "type": "error", "code": code,
                            "message": "Bridge request failed without an error envelope",
                            "retryable": code == "offline", "correlation_id": "sc_" + uuid.uuid4().hex[:16]}
        except (TimeoutError, socket.timeout):
            raise RequestTimeout(req.operation_id, budget) from None
        except urllib.error.URLError as e:
            if isinstance(e.reason, (TimeoutError, socket.timeout)):
                raise RequestTimeout(req.operation_id, budget) from None
            raise

    def health(self) -> tuple[int, dict | list]:
        return self.send(self.build_health())
