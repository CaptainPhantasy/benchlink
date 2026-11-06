"""bridge-daemon HTTP server (0.2.0-mvp, MVP round trip).

Contract paths (packages/bridge-protocol/openapi.v1.json, frozen):
  GET  /v1/health                      unauthenticated; auth=paired iff valid bearer presented
  POST /v1/auth/pair                   dispatch on body type (auth.pair.request|auth.pair.complete)
  POST /v1/sessions                    session.create  -> 201 session.created
  POST /v1/sessions/{id}/attach        session.attach  -> 200 session.created (status open)
  POST /v1/sessions/{id}/messages      501 (not in MVP; Staff task uses /tasks)
  GET  /v1/sessions/{id}/events        -> 200 session.event[] (staff results)
  POST /v1/sessions/{id}/close         session.close   -> 200 {} (contract has no response schema)
  POST /v1/tasks                       task.create     -> 202 task.result(status=accepted) after dispatch
                                       (uncertain delivery -> error, task stays accepted until result/deadline)
  GET  /v1/tasks/{task_id}             -> 200 task.result (polls ResultSource; timeout -> failed/timeout)
Non-contract:
  POST /_bridge/results/{task_id}      http_callback ResultSource only (per-task short-lived token)
  GET  /_daemon/status                 loopback-only diagnostics + bridge status table

Auth on /v1/sessions* and /v1/tasks*: `Authorization: Bearer` header required; bodies that
carry `access_token` (frozen schemas) must carry the SAME token (mismatch/missing -> 403
auth.denied). Tokens in query strings are rejected. Auth runs before any side effect.
"""
from __future__ import annotations

import json
import logging
import re
import socket
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from benchlink_adapter.adapter import StaffAdapter
from benchlink_adapter.config import describe_adapter, load_adapter_config
from benchlink_adapter.errors import BridgeError
from benchlink_adapter.results import CALLBACK_PATH_PREFIX, RESULT_TOKEN_HEADER, HttpCallbackResultSource
from benchlink_adapter.targets import agent_id_for

from . import CONTRACT_VERSION, __version__
from .auth import TS_LOGIN_HEADER, AuthService
from .config import DaemonConfig, describe
from .protocol import errors_for

log = logging.getLogger("benchlink.daemon")
STARTED_AT = time.time()
RID_RE = re.compile(r"^[A-Za-z0-9_\-.:]{1,64}$")
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
PROXY_HEADERS = ("X-Forwarded-For", "Forwarded", "X-Forwarded-Host", "X-Forwarded-Proto",
                 "Tailscale-User-Login", "Tailscale-User-Name")
FINAL = {"succeeded", "failed"}
MAX_BODY = 256 * 1024


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_utc_z(value: str) -> str:
    """Normalise an RFC3339 timestamp (any offset) to UTC 'Z'; fall back to now."""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, AttributeError):
        return now_iso()


def error_envelope(code: str, message: str, retryable: bool = False, correlation_id: str | None = None) -> dict:
    return {"v": "1", "type": "error", "code": code, "message": message,
            "retryable": retryable, "correlation_id": correlation_id or "bd_" + uuid.uuid4().hex[:16]}


def health_pong(paired: bool = False) -> dict:
    return {"v": "1", "type": "health.pong", "daemon_version": __version__,
            "uptime_s": int(max(0, time.time() - STARTED_AT)),
            "auth": "paired" if paired else "unpaired",
            "runtime": "inference", "transport": "tailscale-https", "sdlc_gateway": "n/a"}


class BridgeState:
    """In-memory session + task store (MVP; O4 session-only retention)."""

    def __init__(self, cfg: DaemonConfig, adapter: StaffAdapter | None = None,
                 auth: AuthService | None = None, adapter_env: dict | None = None):
        self.cfg = cfg
        self.auth = auth or AuthService(cfg.device_allowlist_path, cfg.require_tailscale_identity,
                                        cfg.access_ttl_s)
        self.adapter = adapter or StaffAdapter(load_adapter_config(adapter_env))
        self.sessions: dict[str, dict] = {}
        self.tasks: dict[str, dict] = {}
        self.lock = threading.RLock()
        self.request_log: deque = deque(maxlen=500)

    # ------------------------------------------------------------- tasks
    def task_view(self, t: dict) -> dict:
        return {"v": "1", "type": "task.result", "session_id": t["session_id"], "task_id": t["task_id"],
                "status": t["status"], "summary": t["summary"], "artifact_links": t["artifact_links"],
                "error_class": t["error_class"], "agent_id": t["agent_id"], "created_at": t["created_at"]}

    def _finish(self, t, status, summary, error_class=None, links=None, agent_id=None, created_at=None):
        t.update(status=status, summary=summary, error_class=error_class,
                 artifact_links=links if links is not None else t["artifact_links"],
                 agent_id=agent_id or t["agent_id"], created_at=to_utc_z(created_at) if created_at else now_iso(),
                 updated_mono=time.monotonic())
        log.info("task.update task_id=%s session_id=%s status=%s error_class=%s",
                 t["task_id"], t["session_id"], status, error_class or "-")

    def refresh(self, t: dict) -> None:
        with self.lock:
            if t["status"] in FINAL:
                return
            now = time.monotonic()
            interval = self.adapter.cfg.poll_interval_s
            if t["last_poll"] and now - t["last_poll"] < interval and now < t["deadline"]:
                return
            t["last_poll"] = now
        try:
            raw = self.adapter.fetch_result(t["task_id"])
        except BridgeError as e:
            if e.code == "validation":
                with self.lock:
                    self._finish(t, "failed", f"staff result unreadable: {e.message}", "validation")
                return
            log.warning("result_source task_id=%s code=%s (will retry until timeout)", t["task_id"], e.code)
            raw = None
        with self.lock:
            if t["status"] in FINAL:
                return
            if raw is not None:
                errs = errors_for("task.result", raw)
                if errs:
                    self._finish(t, "failed", "staff result failed task_result schema: " + "; ".join(errs[:3]),
                                 "validation")
                    return
                if raw["task_id"] != t["task_id"] or raw["session_id"] != t["session_id"]:
                    self._finish(t, "failed", "staff result task_id/session_id mismatch", "validation")
                    return
                if raw["agent_id"] != t["agent_id"]:
                    log.warning("task_id=%s result agent_id differs from target agent", t["task_id"])
                st = raw["status"]
                if st in FINAL:
                    self._finish(t, st, raw["summary"], raw.get("error_class"), raw["artifact_links"],
                                 raw["agent_id"], raw["created_at"])
                    self._emit_event(t)
                    return
                t.update(status=st, summary=raw["summary"] or t["summary"])
            if time.monotonic() > t["deadline"]:
                self._finish(t, "failed",
                             f"no final result from {t['target_key']} within "
                             f"{self.adapter.cfg.task_timeout_s:g}s", "timeout")

    def _emit_event(self, t):
        s = self.sessions.get(t["session_id"])
        if not s:
            return
        s["events"].append({"v": "1", "type": "session.event", "session_id": t["session_id"],
                            "event_id": "evt_" + uuid.uuid4().hex[:12], "role": "assistant",
                            "agent_id": t["agent_id"], "agent_name": t["target_key"],
                            "content": [{"type": "text", "text": t["summary"] or "(no summary)"}],
                            "created_at": now_iso()})

    def status_table(self) -> list[dict]:
        with self.lock:
            tasks = list(self.tasks.values())
        for t in tasks:
            self.refresh(t)
        return [{"task_id": t["task_id"], "session_id": t["session_id"], "target": t["target_key"],
                 "status": t["status"], "error_class": t["error_class"], "summary": t["summary"],
                 "artifact_links": t["artifact_links"], "request_id": t["request_id"]} for t in tasks]


def make_handler(state: BridgeState):
    cfg = state.cfg

    class Handler(BaseHTTPRequestHandler):
        server_version = f"benchlink-bridge-daemon/{__version__}"

        # -------------------------------------------------------- plumbing
        def _begin(self):
            rid = self.headers.get("X-Request-Id") or ""
            self.rid = rid if RID_RE.match(rid) else "req_" + uuid.uuid4().hex[:16]
            parts = urlsplit(self.path)
            self.route = parts.path
            self.query = parse_qs(parts.query)
            self.ctx = {}
            self.extra_headers = {}
            self.raw_body = b""
            if self.command == "POST":
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    n = 0
                # always drain (bounded) so auth failures never leave a half-read request
                self.raw_body = self.rfile.read(min(n, MAX_BODY + 1)) if n > 0 else b""

        def _send(self, status: int, payload, type_name: str | None = None, list_of: str | None = None):
            # schema-validate everything going out
            errs = []
            if type_name:
                errs = errors_for(type_name, payload)
            elif list_of:
                for item in payload:
                    errs += errors_for(list_of, item)
            if errs:
                log.error("outbound schema violation rid=%s type=%s errs=%s", self.rid, type_name or list_of, errs)
                status, payload = 500, error_envelope("internal", "bridge produced an invalid message",
                                                      False, self.rid)
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-Benchlink-Contract", CONTRACT_VERSION)
            self.send_header("X-Request-Id", self.rid)
            for k, v in self.extra_headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            entry = {"at": now_iso(), "request_id": self.rid, "method": self.command, "path": self.route,
                     "status": status, "code": payload.get("code") if isinstance(payload, dict)
                     and payload.get("type") == "error" else None, **self.ctx}
            state.request_log.append(entry)
            log.info("req rid=%s %s %s -> %s code=%s session_id=%s task_id=%s device_id=%s tok=%s",
                     self.rid, self.command, self.route, status, entry["code"] or "-",
                     self.ctx.get("session_id", "-"), self.ctx.get("task_id", "-"),
                     self.ctx.get("device_id", "-"), self.ctx.get("tok", "-"))

        def _err(self, e: BridgeError):
            return self._send(e.http_status, error_envelope(e.code, e.message, e.retryable, self.rid), "error")

        def _body(self) -> dict:
            if len(self.raw_body) > MAX_BODY:
                raise BridgeError("validation", "body too large")
            raw = self.raw_body
            try:
                data = json.loads(raw or b"null")
            except ValueError:
                raise BridgeError("validation", "body is not valid JSON") from None
            if not isinstance(data, dict):
                raise BridgeError("validation", "body must be a JSON object")
            return data

        def _validate(self, type_name: str, body: dict):
            if body.get("type") != type_name:
                raise BridgeError("validation", f"expected type {type_name!r}, got {body.get('type')!r}")
            errs = errors_for(type_name, body)
            if errs:
                raise BridgeError("validation", f"{type_name} schema: " + "; ".join(errs[:5]))

        def _authn(self, scope: str | None):
            p, token = state.auth.authenticate(self.headers.get("Authorization"), set(self.query),
                                               scope, self.headers.get(TS_LOGIN_HEADER))
            self.ctx.update(device_id=p.device_id, tok=p.fingerprint)
            return p, token

        @staticmethod
        def _body_token_must_match(body: dict, token: str):
            bt = body.get("access_token")
            import hmac
            if not isinstance(bt, str) or not hmac.compare_digest(bt.encode("utf-8", "replace"), token.encode()):
                raise BridgeError("auth.denied", "body access_token must be present and match the Authorization bearer")

        def _session(self, sid: str, principal) -> dict:
            with state.lock:
                s = state.sessions.get(sid)
            if not s or s["device_id"] != principal.device_id:
                raise BridgeError("session.not_found", f"session {sid!r} not found")
            self.ctx["session_id"] = sid
            return s

        # -------------------------------------------------------- GET
        def do_GET(self):  # noqa: N802
            self._begin()
            try:
                r = self.route
                if r in {"/v1/health", "/health"}:
                    paired = False
                    if self.headers.get("Authorization"):
                        try:
                            p, _ = self._authn(None)
                            paired = True
                            self.extra_headers["X-Benchlink-Tailscale-Identity"] = p.tailscale_identity or "none"
                        except BridgeError:
                            paired = False
                    return self._send(200, health_pong(paired), "health.pong")
                if r == "/_daemon/status":
                    # tailscale serve proxies from 127.0.0.1, so loopback peer address alone is
                    # not enough: any proxy/identity header means the caller is remote.
                    proxied = any(self.headers.get(hh) for hh in PROXY_HEADERS)
                    if self.client_address[0] not in LOOPBACK or proxied:
                        raise BridgeError("auth.denied", "diagnostics are loopback-only (direct, unproxied)")
                    return self._send(200, {"daemon_version": __version__, "contract": CONTRACT_VERSION,
                                            "config": describe(cfg),
                                            "adapter": describe_adapter(state.adapter.cfg),
                                            "result_source": state.adapter.source.describe(),
                                            "bridge_status": state.status_table(),
                                            "recent_requests": list(state.request_log)[-50:]})
                m = re.fullmatch(r"/v1/sessions/([^/]+)/events", r)
                if m:
                    p, _ = self._authn(None)
                    s = self._session(unquote(m.group(1)), p)
                    for t in [t for t in list(state.tasks.values()) if t["session_id"] == s["session_id"]]:
                        state.refresh(t)
                    with state.lock:
                        events = list(s["events"])
                    return self._send(200, events, list_of="session.event")
                m = re.fullmatch(r"/v1/tasks/([^/]+)", r)
                if m:
                    p, _ = self._authn(None)
                    tid = unquote(m.group(1))
                    self.ctx["task_id"] = tid
                    with state.lock:
                        t = state.tasks.get(tid)
                    if not t or t["device_id"] != p.device_id:
                        raise BridgeError("task.not_found", f"task {tid!r} not found")
                    self.ctx["session_id"] = t["session_id"]
                    state.refresh(t)
                    with state.lock:
                        view = state.task_view(t)
                    return self._send(200, view, "task.result")
                raise BridgeError("validation", f"no route GET {r}")
            except BridgeError as e:
                return self._err(e)
            except Exception:  # noqa: BLE001
                log.exception("unhandled rid=%s", self.rid)
                return self._err(BridgeError("internal", "unexpected bridge error"))

        # -------------------------------------------------------- POST
        def do_POST(self):  # noqa: N802
            self._begin()
            try:
                r = self.route
                if r == "/v1/auth/pair":
                    return self._pair()
                if r == "/v1/sessions":
                    return self._session_create()
                if r == "/v1/tasks":
                    return self._task_create()
                m = re.fullmatch(r"/v1/sessions/([^/]+)/(attach|messages|close)", r)
                if m:
                    return self._session_op(unquote(m.group(1)), m.group(2))
                if r.startswith(CALLBACK_PATH_PREFIX):
                    return self._callback(unquote(r[len(CALLBACK_PATH_PREFIX):]))
                self._body()
                raise BridgeError("validation", f"no route POST {r}")
            except BridgeError as e:
                return self._err(e)
            except Exception:  # noqa: BLE001
                log.exception("unhandled rid=%s", self.rid)
                return self._err(BridgeError("internal", "unexpected bridge error"))

        def _pair(self):
            body = self._body()
            ts_login = self.headers.get(TS_LOGIN_HEADER)
            t = body.get("type")
            self.ctx["device_id"] = str(body.get("device_id", "-"))[:64]
            if t == "auth.pair.request":
                self._validate("auth.pair.request", body)
                return self._send(200, state.auth.pair_request(body, ts_login), "auth.pair.challenge")
            if t == "auth.pair.complete":
                self._validate("auth.pair.complete", body)
                out = state.auth.pair_complete(body, ts_login)
                from .auth import token_fingerprint
                self.ctx["tok"] = token_fingerprint(out["access_token"])
                return self._send(200, out, "auth.session.token")
            raise BridgeError("validation", "auth/pair body type must be auth.pair.request or auth.pair.complete")

        def _session_create(self):
            p, token = self._authn("session.create")
            body = self._body()
            self._body_token_must_match(body, token)
            self._validate("session.create", body)
            key = state.adapter.resolve_target(body["target_agent"])
            sid = "sess_" + uuid.uuid4().hex[:16]
            s = {"session_id": sid, "target_key": key, "target_agent": body["target_agent"],
                 "device_id": p.device_id, "status": "open", "created_at": now_iso(),
                 "metadata": body.get("metadata") or {}, "events": []}
            with state.lock:
                state.sessions[sid] = s
            self.ctx["session_id"] = sid
            return self._send(201, {"v": "1", "type": "session.created", "session_id": sid,
                                    "target_agent": body["target_agent"], "status": "open",
                                    "created_at": s["created_at"]}, "session.created")

        def _session_op(self, sid, op):
            scope = {"attach": "session.create", "messages": "session.message", "close": "session.close"}[op]
            p, token = self._authn(scope)
            body = self._body()
            self._body_token_must_match(body, token)
            tname = {"attach": "session.attach", "messages": "session.message", "close": "session.close"}[op]
            self._validate(tname, body)
            if body["session_id"] != sid:
                raise BridgeError("validation", "body session_id does not match path")
            s = self._session(sid, p)
            if op == "messages":
                raise BridgeError("internal", "session.message not in MVP; send a Staff task via POST /v1/tasks")
            if op == "close":
                with state.lock:
                    s["status"] = "closed"
                return self._send(200, {})
            if s["status"] != "open":
                raise BridgeError("session.not_found", f"session {sid!r} is closed")
            return self._send(200, {"v": "1", "type": "session.created", "session_id": sid,
                                    "target_agent": s["target_agent"], "status": "open",
                                    "created_at": s["created_at"]}, "session.created")

        def _task_create(self):
            p, token = self._authn("task.create")
            body = self._body()
            self._body_token_must_match(body, token)
            self._validate("task.create", body)
            tid, sid = body["task_id"], body["session_id"]
            self.ctx["task_id"] = tid
            s = self._session(sid, p)
            if s["status"] != "open":
                raise BridgeError("session.not_found", f"session {sid!r} is closed")
            key = state.adapter.resolve_target(body["target_agent"])
            if key != s["target_key"]:
                raise BridgeError("routing", f"task target {key!r} differs from session target "
                                  f"{s['target_key']!r} (O1: no mid-task handoff)")
            with state.lock:
                if tid in state.tasks:
                    raise BridgeError("validation", f"task_id {tid!r} already exists")
                t = {"task_id": tid, "session_id": sid, "target_key": key, "agent_id": agent_id_for(key),
                     "device_id": p.device_id, "status": "accepted", "summary": f"queued for {key}",
                     "artifact_links": [], "error_class": None, "created_at": now_iso(),
                     "request_id": self.rid, "priority": body["priority"],
                     "reply_destination": body["reply_destination"],
                     "deadline": time.monotonic() + state.adapter.cfg.task_timeout_s,
                     "last_poll": 0.0, "updated_mono": time.monotonic()}
                state.tasks[tid] = t
            try:
                receipt = state.adapter.dispatch(body, key, self.rid)
            except BridgeError as e:
                with state.lock:
                    if e.delivery_uncertain:
                        # The bot may already be working. Stay non-terminal so GET keeps
                        # checking for its result until the task deadline (no redispatch).
                        # A result that already landed (fast callback) is left untouched.
                        if t["status"] == "accepted":
                            t["summary"] = f"dispatch to {key} unconfirmed ({e.code}); waiting for its result"
                            t["updated_mono"] = time.monotonic()
                    elif t["status"] not in FINAL:
                        state._finish(t, "failed", e.message, e.code)
                        if isinstance(state.adapter.source, HttpCallbackResultSource):
                            state.adapter.source.revoke(tid)  # the bot never got this callback token
                log.warning("dispatch rid=%s task_id=%s target=%s code=%s uncertain=%s",
                            self.rid, tid, key, e.code, e.delivery_uncertain)
                raise
            log.info("dispatch rid=%s task_id=%s session_id=%s target=%s receipt=%s",
                     self.rid, tid, sid, key, receipt)
            with state.lock:
                if t["status"] == "accepted":   # result may already have landed (fast bot)
                    t["summary"] = f"dispatched to {key}"
                view = state.task_view(t)
            return self._send(202, view, "task.result")

        def _callback(self, tid):
            if not isinstance(state.adapter.source, HttpCallbackResultSource):
                self._body()
                raise BridgeError("validation", "result callback disabled (BENCHLINK_RESULT_SOURCE != http_callback)")
            self.ctx["task_id"] = tid
            body = self._body()
            self._validate("task.result", body)
            with state.lock:
                t = state.tasks.get(tid)
            if not t:
                raise BridgeError("task.not_found", f"task {tid!r} not found")
            if body["task_id"] != tid:
                raise BridgeError("validation", "body task_id does not match path")
            state.adapter.source.accept(tid, self.headers.get(RESULT_TOKEN_HEADER), body)
            t["last_poll"] = 0.0
            state.refresh(t)
            with state.lock:
                view = state.task_view(t)
            return self._send(202, view, "task.result")

        def log_message(self, fmt, *args):
            pass

    return Handler


class _IPv6ThreadingHTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def listen_url(host: str, port: int) -> str:
    """HTTP base URL for a listen address; IPv6 literals need brackets."""
    return f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}"


def serve(cfg: DaemonConfig, state: BridgeState | None = None, **state_kwargs) -> ThreadingHTTPServer:
    state = state or BridgeState(cfg, **state_kwargs)
    cls = _IPv6ThreadingHTTPServer if ":" in cfg.listen_host else ThreadingHTTPServer
    srv = cls((cfg.listen_host, cfg.listen_port), make_handler(state))
    src = state.adapter.source
    if isinstance(src, HttpCallbackResultSource) and not src.base_url:
        host, port = srv.server_address[:2]
        src.base_url = listen_url(host, port)
    srv.state = state  # type: ignore[attr-defined]
    return srv
