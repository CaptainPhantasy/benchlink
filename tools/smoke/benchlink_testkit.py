"""Shared loopback harness for pytest + smoke. No Tailscale, no Drive, no live hosts.

Never calls https://inference.tail58d565.ts.net:19740 or bridge.floyds-labs.ts.net.
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for sub in ("apps/staff-adapter", "apps/bridge-daemon", "apps/mac-sidecar"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

from benchlink_adapter.adapter import StaffAdapter  # noqa: E402
from benchlink_adapter.config import load_adapter_config  # noqa: E402
from benchlink_adapter.results import InMemoryDriveClient  # noqa: E402
from benchlink_daemon.config import load_config as load_daemon_config  # noqa: E402
from benchlink_daemon.protocol import errors_for  # noqa: E402
from benchlink_daemon.server import BridgeState, listen_url, serve  # noqa: E402
from benchlink_sidecar.client import BridgeClient  # noqa: E402
from benchlink_sidecar.config import load_config as load_sidecar_config  # noqa: E402
from benchlink_sidecar.pairing import allowlist_snippet, generate_key  # noqa: E402

DEVICE_ID = "mac-smoke-01"


class Harness:
    """Daemon on 127.0.0.1:<ephemeral> + enrolled device key + sidecar client."""

    def __init__(self, adapter_env: dict | None = None, daemon_env: dict | None = None,
                 enroll: bool = True, drive_client=None, ts_identity: str | None = None,
                 scopes: list | None = None, auth_clock=None):
        self.tmp = tempfile.TemporaryDirectory(prefix="benchlink_")
        self.dir = Path(self.tmp.name)
        self.results_dir = self.dir / "results"
        self.results_dir.mkdir()
        self.key_path = self.dir / "device_ed25519.pem"
        self.allowlist = self.dir / "device_allowlist.json"
        key = generate_key(str(self.key_path))
        entries = {}
        if enroll:
            entries = allowlist_snippet(DEVICE_ID, key, ts_identity)
            if scopes is not None:
                entries[DEVICE_ID]["scopes"] = scopes
        self.allowlist.write_text(json.dumps(entries))
        self.key = key
        denv = {"BENCHLINK_DEVICE_ALLOWLIST_PATH": str(self.allowlist), **(daemon_env or {})}
        aenv = {"BENCHLINK_ADAPTER_TRANSPORT": "fake", "BENCHLINK_RESULT_SOURCE": "local_folder",
                "BENCHLINK_RESULTS_LOCAL_DIR": str(self.results_dir), **(adapter_env or {})}
        self.daemon_cfg = replace(load_daemon_config(denv), listen_port=0)
        self.drive = drive_client or InMemoryDriveClient()
        self.adapter = StaffAdapter(load_adapter_config(aenv), drive_client=self.drive)
        self.state = BridgeState(self.daemon_cfg, adapter=self.adapter)
        if auth_clock is not None:
            self.state.auth.clock = auth_clock
        self.srv = serve(self.daemon_cfg, state=self.state)
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        host, port = self.srv.server_address[:2]
        self.base = listen_url(host, port)
        self.sidecar_cfg = load_sidecar_config({
            "BENCHLINK_BRIDGE_BASE_URL": self.base,
            "BENCHLINK_DEVICE_KEY_PATH": str(self.key_path),
            "BENCHLINK_DEVICE_ID": DEVICE_ID})
        self.client = BridgeClient(self.sidecar_cfg)

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # -------------------------------------------------------------- raw HTTP
    def http(self, method, path, body=None, headers=None, raw=None):
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        h = {"Content-Type": "application/json", "Accept": "application/json", **(headers or {})}
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read() or b"null"), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null"), dict(e.headers)

    def pair_raw(self, scopes=None, ts_login=None, sign=True, nonce="nonce_0123456789"):
        from benchlink_sidecar.pairing import b64url
        h = {"Tailscale-User-Login": ts_login} if ts_login else {}
        st, ch, _ = self.http("POST", "/v1/auth/pair", {
            "v": "1", "type": "auth.pair.request", "device_id": DEVICE_ID, "client": "grok.app",
            "client_version": "test", "nonce": nonce}, h)
        if st != 200:
            return st, ch, None
        proof = b64url(self.key.sign(f"{ch['challenge']}.{ch['nonce']}".encode())) if sign else "AAAA"
        st, tok, _ = self.http("POST", "/v1/auth/pair", {
            "v": "1", "type": "auth.pair.complete", "device_id": DEVICE_ID, "proof": proof,
            "scopes": scopes or ["session.create", "session.message", "session.close", "task.create"]}, h)
        return st, tok, ch

    def token(self, **kw) -> str:
        st, tok, _ = self.pair_raw(**kw)
        assert st == 200, tok
        return tok["access_token"]

    def session(self, token, target="floyd"):
        return self.http("POST", "/v1/sessions", {
            "v": "1", "type": "session.create", "access_token": token, "intent": "staff_task",
            "target_agent": target}, {"Authorization": f"Bearer {token}"})

    def task(self, token, session_id, task_id, target="floyd", goal="Confirm bridge round trip"):
        return self.http("POST", "/v1/tasks", {
            "v": "1", "type": "task.create", "access_token": token, "session_id": session_id,
            "task_id": task_id, "target_agent": target, "goal": goal, "priority": "normal",
            "reply_destination": "bridge_status"}, {"Authorization": f"Bearer {token}"})

    def get_task(self, token, task_id):
        return self.http("GET", f"/v1/tasks/{task_id}", headers={"Authorization": f"Bearer {token}"})

    def wait_final(self, token, task_id, timeout=5.0):
        import time
        end = time.monotonic() + timeout
        while True:
            st, body, _ = self.get_task(token, task_id)
            if body.get("status") in {"succeeded", "failed"} or time.monotonic() > end:
                return st, body
            time.sleep(0.05)


def valid(type_name, data) -> list[str]:
    return errors_for(type_name, data)
