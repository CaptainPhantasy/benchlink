"""Credentialed HTTP must never follow even a same-origin redirect. Loopback only."""
import json
import threading
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from benchlink_adapter.config import AdapterConfig, TargetEndpoint
from benchlink_adapter.errors import BridgeError
from benchlink_adapter.results import post_callback
from benchlink_adapter.transports import WebhookTransport
from benchlink_sidecar.client import BridgeClient
from benchlink_sidecar.config import load_config
from benchlink_sidecar.pairing import _post


@contextmanager
def receiver(status=200, location=None):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def respond(self):
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n)
            requests.append((self.command, self.path, dict(self.headers), body))
            self.send_response(status)
            if location:
                self.send_header("Location", location)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        do_POST = do_GET = respond

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", requests
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("kind", ["sidecar", "pair", "webhook", "custom_webhook", "callback"])
def test_redirect_cannot_forward_headers_proof_or_payload(status, kind):
    with receiver() as (destination, sink), receiver(status, destination + "/sink") as (source, sent):
        if kind == "sidecar":
            client = BridgeClient(load_config({"BENCHLINK_BRIDGE_BASE_URL": source,
                                               "BENCHLINK_ACCESS_TOKEN": "not-a-real-test-bearer"}))
            code, _ = client.send(client.build_session_create("floyd", dry_run=False))
            assert code == status
        elif kind == "pair":
            code, _ = _post(source + "/pair", {"proof": "synthetic-proof-not-a-credential"}, 1)
            assert code == status
        elif kind in {"webhook", "custom_webhook"}:
            config = AdapterConfig(targets={"floyd": TargetEndpoint("floyd", source, "fake-test-sender-key")})
            if kind == "custom_webhook":
                config = replace(config, sender_key_header="X-Routine-Key", sender_key_scheme="")
            with pytest.raises(BridgeError) as error:
                WebhookTransport(config).dispatch("floyd", {"task": {"task_id": "task_redirect"}}, "req_redirect")
            assert error.value.code == "offline"
        else:
            code = post_callback({"url": source, "token_header": "X-Benchlink-Result-Token",
                                  "token": "fake-test-callback-token"}, {"summary": "private fixture task"})
            assert code == status
        assert len(sent) == 1
        assert len(sink) == 0, "redirect destination received credentials or task data"


def test_authenticated_nonredirected_rpc_still_works():
    with receiver() as (url, seen):
        client = BridgeClient(load_config({"BENCHLINK_BRIDGE_BASE_URL": url,
                                          "BENCHLINK_ACCESS_TOKEN": "not-a-real-test-bearer"}))
        assert client.send(client.build_session_create("floyd", dry_run=False))[0] == 200
        assert len(seen) == 1
        assert seen[0][2]["Authorization"] == "Bearer not-a-real-test-bearer"
        assert json.loads(seen[0][3])["access_token"] == "not-a-real-test-bearer"
