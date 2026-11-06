"""Webhook transport against a local fake routine receiver that then plays the staff bot."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from benchlink_testkit import Harness

from benchlink_adapter.config import AdapterConfigError, load_adapter_config
from benchlink_adapter.results import post_callback
from benchlink_adapter.targets import ETHAN_ID
from benchlink_sidecar.ux import BridgeControlShell


class Receiver:
    """Stands in for a Grok Bot routine webhook. Records requests; optionally acts as the bot."""

    def __init__(self, status=200, delay=0.0, act=True, expect_header="Authorization",
                 expect_value="Bearer test-sender-key"):
        self.requests = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append({"headers": dict(self.headers), "body": body})
                if delay:
                    time.sleep(delay)
                ok_key = self.headers.get(expect_header) == expect_value
                code = status if ok_key else 401
                try:
                    self.send_response(code)
                    self.send_header("Content-Length", "2")
                    self.end_headers()
                    self.wfile.write(b"{}")
                except (BrokenPipeError, ConnectionResetError):
                    return  # client (bridge) gave up — the timeout case
                if code == 200 and act:
                    threading.Thread(target=outer.be_bot, args=(body,), daemon=True).start()

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/routine-hook"

    def be_bot(self, d):
        """What the staff bot's routine prompt does: do the task, write task.result."""
        result = dict(d["result_template"], status="succeeded",
                      summary=f"Done by {d['target']['key']}: {d['task']['goal']}",
                      artifact_links=[{"title": "receipt", "url": "https://example.invalid/receipt"}],
                      error_class=None, created_at="2026-11-05T12:00:00-05:00")
        rd = d["result_delivery"]
        if rd["mode"] == "http_callback":
            post_callback(rd, result)
        elif rd["mode"] == "local_folder":
            (Path(rd["folder"]) / rd["file_name"]).write_text(json.dumps(result))

    def __enter__(self):
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        return self

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()


def _env(url, target="ETHAN", **extra):
    return {"BENCHLINK_ADAPTER_TRANSPORT": "webhook", f"BENCHLINK_TARGET_{target}_WEBHOOK_URL": url,
            f"BENCHLINK_TARGET_{target}_SENDER_KEY": "test-sender-key", **extra}


@pytest.mark.parametrize("source", ["http_callback", "local_folder"])
def test_webhook_round_trip_receiver_then_result(source):
    with Receiver() as rx, Harness(adapter_env=_env(rx.url, BENCHLINK_RESULT_SOURCE=source)) as h:
        shell = BridgeControlShell(h.sidecar_cfg, client=h.client, poll_sleep=0.05, max_wait_ticks=100)
        snap = shell.start_and_wait("Canary via webhook", target_key="ethan")["snapshot"]
        assert snap["phase"] == "done", snap
        assert snap["result"]["summary"] == "Done by ethan: Canary via webhook"
        assert snap["result"]["agent_id"] == ETHAN_ID
        req = rx.requests[0]
        assert req["headers"]["Authorization"] == "Bearer test-sender-key"
        assert req["headers"]["X-Benchlink-Task-Id"] == snap["task_id"] and req["headers"]["X-Request-Id"]
        body = req["body"]
        assert body["type"] == "benchlink.dispatch" and body["target"]["key"] == "ethan"
        assert "access_token" not in body["task"] and body["task"]["goal"] == "Canary via webhook"
        assert body["result_delivery"]["mode"] == source


def test_webhook_custom_sender_key_header_raw():
    env = _env("", BENCHLINK_WEBHOOK_SENDER_KEY_HEADER="X-Routine-Key", BENCHLINK_WEBHOOK_SENDER_KEY_SCHEME="")
    with Receiver(expect_header="X-Routine-Key", expect_value="test-sender-key") as rx:
        env[f"BENCHLINK_TARGET_ETHAN_WEBHOOK_URL"] = rx.url
        with Harness(adapter_env=env) as h:
            tok = h.token()
            _, s, _ = h.session(tok, "ethan")
            st, acc, _ = h.task(tok, s["session_id"], "task_hdr", target="ethan")
            assert st == 202, acc
            assert "Authorization" not in rx.requests[0]["headers"]


@pytest.mark.parametrize("status,code", [(404, "routing"), (500, "offline"), (403, "auth.denied")])
def test_webhook_http_errors_map_to_classes(status, code):
    with Receiver(status=status, act=False) as rx, Harness(adapter_env=_env(rx.url)) as h:
        tok = h.token()
        _, s, _ = h.session(tok, "ethan")
        st, err, _ = h.task(tok, s["session_id"], f"task_{status}", target="ethan")
        assert err["code"] == code


def test_webhook_missing_sender_key_fails_closed():
    with Receiver() as rx:
        env = {"BENCHLINK_ADAPTER_TRANSPORT": "webhook", "BENCHLINK_TARGET_FLOYD_WEBHOOK_URL": rx.url}
        with Harness(adapter_env=env) as h:
            tok = h.token()
            _, s, _ = h.session(tok)
            st, err, _ = h.task(tok, s["session_id"], "task_nokey")
            assert st == 503 and "SENDER_KEY" in err["message"] and rx.requests == []


@pytest.mark.parametrize("url", ["https://inference.tail58d565.ts.net:19740/hook",
                                 "https://bridge.floyds-labs.ts.net/hook",
                                 "http://example.com/hook", "http://127.0.0.1:63301/x", "not a url"])
def test_webhook_url_validation(url):
    with pytest.raises(AdapterConfigError):
        load_adapter_config({"BENCHLINK_TARGET_FLOYD_WEBHOOK_URL": url})
