"""Codex T0D1: an uncertain webhook dispatch must not become a terminal failure.

A routine webhook may accept a task and keep working after the bridge stops
waiting for its HTTP response. Only a definitive rejection may fail the task at
dispatch; an uncertain outcome stays non-terminal until a result or the task
deadline. Real loopback HTTP only.
"""
import json
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from benchlink_testkit import Harness, valid

from benchlink_adapter.results import post_callback
from benchlink_sidecar import server as sidecar_server
from benchlink_sidecar.ux import BridgeControlShell, TaskInFlight

KEY = "test-sender-key"


class AcceptingReceiver:
    """Routine webhook stand-in that may accept work and answer late, early or never."""

    def __init__(self, *, status=200, respond_after=0.0, bot_after=None, drop=False):
        self.requests = []
        self.bot_go = threading.Event()
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(body)
                if bot_after is not None:  # accepted: the bot works regardless of the HTTP reply
                    threading.Thread(target=outer._be_bot, args=(body, bot_after), daemon=True).start()
                if drop:
                    self.close_connection = True
                    return  # accepted, then the connection dies without any response
                time.sleep(respond_after)
                try:
                    self.send_response(status)
                    self.send_header("Content-Length", "2")
                    self.end_headers()
                    self.wfile.write(b"{}")
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/routine-hook"

    def _be_bot(self, d, delay):
        self.bot_go.wait(5)
        time.sleep(delay)
        result = dict(d["result_template"], status="succeeded",
                      summary=f"Done by {d['target']['key']}: {d['task']['goal']}",
                      artifact_links=[], error_class=None, created_at="2026-11-05T18:00:00-05:00")
        rd = d["result_delivery"]
        if rd["mode"] == "http_callback":
            post_callback(rd, result)
        else:
            (Path(rd["folder"]) / rd["file_name"]).write_text(json.dumps(result))

    def __enter__(self):
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        return self

    def __exit__(self, *a):
        self.bot_go.set()
        self.srv.shutdown()
        self.srv.server_close()


def _env(url, **extra):
    return {"BENCHLINK_ADAPTER_TRANSPORT": "webhook", "BENCHLINK_TARGET_FLOYD_WEBHOOK_URL": url,
            "BENCHLINK_TARGET_FLOYD_SENDER_KEY": KEY, "BENCHLINK_WEBHOOK_TIMEOUT_S": "0.2", **extra}


def _create(h, task_id):
    tok = h.token()
    _, s, _ = h.session(tok)
    st, body, _ = h.task(tok, s["session_id"], task_id)
    return tok, s["session_id"], st, body


@pytest.mark.parametrize("source", ["local_folder", "http_callback"])
def test_slow_accepting_webhook_timeout_keeps_task_recoverable_without_redispatch(source):
    with AcceptingReceiver(respond_after=1.0, bot_after=0.0) as rx, \
            Harness(adapter_env=_env(rx.url, BENCHLINK_RESULT_SOURCE=source)) as h:
        tok, sid, st, err = _create(h, "task_t0d1_slow")
        assert st == 504 and err["code"] == "timeout" and not valid("error", err)  # contract unchanged
        st, view, _ = h.get_task(tok, "task_t0d1_slow")
        assert st == 200 and not valid("task.result", view)
        assert view["status"] == "accepted" and view["error_class"] is None, view
        # same-ID create retry is refused and never reaches the webhook again
        st, again, _ = h.task(tok, sid, "task_t0d1_slow")
        assert st == 422 and again["code"] == "validation"
        rx.bot_go.set()
        st, final = h.wait_final(tok, "task_t0d1_slow")
        assert final["status"] == "succeeded" and final["summary"].startswith("Done by floyd"), final
        assert len(rx.requests) == 1


def test_uncertain_dispatch_without_result_still_fails_at_task_deadline():
    with AcceptingReceiver(respond_after=1.0) as rx, \
            Harness(adapter_env=_env(rx.url, BENCHLINK_TASK_TIMEOUT_S="0.8")) as h:
        tok, _, st, _ = _create(h, "task_t0d1_silent")
        assert st == 504
        assert h.get_task(tok, "task_t0d1_silent")[1]["status"] == "accepted"
        st, final = h.wait_final(tok, "task_t0d1_silent", timeout=5)
        assert final["status"] == "failed" and final["error_class"] == "timeout", final
        assert len(rx.requests) == 1


@pytest.mark.parametrize("kwargs,http,code", [
    ({"status": 504}, 504, "timeout"),        # gateway timed out waiting on the receiver
    ({"status": 408}, 504, "timeout"),        # proxy/server timeout after receiving bytes
    ({"status": 500}, 503, "offline"),        # handler error after the task was received
    ({"status": 502}, 503, "offline"),        # reverse proxy lost the upstream reply
    ({"status": 503}, 503, "offline"),
    ({"drop": True}, 503, "offline"),         # request delivered, connection died before a reply
])
def test_other_post_send_ambiguity_is_also_nonterminal(kwargs, http, code):
    with AcceptingReceiver(bot_after=0.0, **kwargs) as rx, Harness(adapter_env=_env(rx.url)) as h:
        tok, _, st, err = _create(h, "task_t0d1_amb")
        assert (st, err["code"]) == (http, code)
        assert h.get_task(tok, "task_t0d1_amb")[1]["status"] == "accepted"
        rx.bot_go.set()
        assert h.wait_final(tok, "task_t0d1_amb")[1]["status"] == "succeeded"


@pytest.mark.parametrize("status,http,code", [
    (401, 403, "auth.denied"), (403, 403, "auth.denied"), (404, 422, "routing"),
    (501, 503, "offline"), (505, 503, "offline"),
])
def test_definitive_webhook_rejection_still_fails_the_task(status, http, code):
    with AcceptingReceiver(status=status) as rx, Harness(adapter_env=_env(rx.url)) as h:
        tok, _, st, err = _create(h, "task_t0d1_reject")
        assert (st, err["code"]) == (http, code)
        view = h.get_task(tok, "task_t0d1_reject")[1]
        assert view["status"] == "failed" and view["error_class"] == code, view


def test_unreachable_or_unwired_webhook_still_fails_the_task():
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    for env in (_env(f"http://127.0.0.1:{port}/h"), {"BENCHLINK_ADAPTER_TRANSPORT": "webhook"}):
        with Harness(adapter_env=env) as h:
            tok, _, st, err = _create(h, "task_t0d1_down")
            assert st == 503 and err["code"] == "offline"
            assert h.get_task(tok, "task_t0d1_down")[1]["status"] == "failed"


def test_sidecar_keeps_ownership_through_uncertain_dispatch_and_recovers_result():
    with AcceptingReceiver(respond_after=1.0, bot_after=0.0) as rx, Harness(adapter_env=_env(rx.url)) as h:
        shell = BridgeControlShell(h.sidecar_cfg, client=h.client, poll_sleep=0.05, max_wait_ticks=100)
        snap = shell.start_and_wait("slow but accepted")["snapshot"]
        assert snap["phase"] == "error" and snap["error"]["code"] == "timeout"
        assert shell.submission_uncertain and shell.has_unfinished_task()
        with pytest.raises(TaskInFlight):
            shell.end_task()
        shell.resume_interrupted_wait()  # recovery GET: original task is still in progress
        assert shell.phase.value == "wait" and shell.has_unfinished_task() and shell.result is None
        rx.bot_go.set()
        out = shell.wait_until_done()
        assert out["phase"] == "done" and out["result"]["status"] == "succeeded", out
        assert out["result"]["task_id"] == snap["task_id"] and not shell.has_unfinished_task()
        assert len(rx.requests) == 1 and len(h.state.tasks) == 1


def test_sidecar_http_run_is_blocked_until_original_task_result_is_recovered():
    import urllib.error
    import urllib.request
    with AcceptingReceiver(respond_after=1.0, bot_after=0.0) as rx, Harness(adapter_env=_env(rx.url)) as h:
        sc = sidecar_server.serve(replace(h.sidecar_cfg, listen_port=0))
        threading.Thread(target=sc.serve_forever, kwargs={"poll_interval": .01}, daemon=True).start()
        base = f"http://127.0.0.1:{sc.server_address[1]}"

        def call(path, body=None):
            req = urllib.request.Request(base + path, data=json.dumps(body or {}).encode(), method="POST",
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())
        try:
            st, out = call("/_sidecar/ux/run", {"goal": "first", "max_ticks": 1})
            assert st == 200 and out["snapshot"]["error"]["code"] == "timeout"
            assert call("/_sidecar/ux/run", {"goal": "duplicate", "max_ticks": 1})[0] == 409
            st, snap = call("/_sidecar/ux/poll")
            assert st == 200 and snap["phase"] == "wait", snap
            assert call("/_sidecar/ux/run", {"goal": "duplicate", "max_ticks": 1})[0] == 409
            rx.bot_go.set()
            deadline = time.monotonic() + 5
            while snap["phase"] == "wait" and time.monotonic() < deadline:
                time.sleep(0.05)
                snap = call("/_sidecar/ux/poll")[1]
            assert snap["phase"] == "done", snap
            assert len(rx.requests) == 1 and len(h.state.tasks) == 1
        finally:
            sc.shutdown()
            sc.server_close()


@pytest.mark.parametrize("raised,code,uncertain", [
    (lambda: __import__("urllib.error").error.URLError(TimeoutError("connect")), "timeout", False),
    (lambda: __import__("urllib.error").error.URLError(ConnectionRefusedError()), "offline", False),
    (lambda: TimeoutError("read"), "timeout", True),
    (lambda: ConnectionResetError("reset"), "offline", True),
])
def test_transport_marks_only_post_send_failures_uncertain(monkeypatch, raised, code, uncertain):
    from benchlink_adapter import transports
    from benchlink_adapter.config import load_adapter_config
    from benchlink_adapter.errors import BridgeError

    def boom(request, *, timeout):
        raise raised()
    monkeypatch.setattr(transports, "open_no_redirects", boom)
    cfg = load_adapter_config(_env("http://127.0.0.1:9/h"))
    payload = {"task": {"task_id": "task_unit"}}
    with pytest.raises(BridgeError) as info:
        transports.WebhookTransport(cfg).dispatch("floyd", payload, "req_unit")
    assert info.value.code == code and info.value.delivery_uncertain is uncertain


def test_definitive_failure_revokes_unused_callback_token():
    with AcceptingReceiver(status=401) as rx, \
            Harness(adapter_env=_env(rx.url, BENCHLINK_RESULT_SOURCE="http_callback")) as h:
        tok, _, st, err = _create(h, "task_t0d1_revoke")
        assert st == 403 and err["code"] == "auth.denied"
        assert "task_t0d1_revoke" not in h.adapter.source._tokens
        d = rx.requests[0]
        late = dict(d["result_template"], status="succeeded", summary="late", artifact_links=[],
                    error_class=None, created_at="2026-11-05T18:00:00-05:00")
        assert post_callback(d["result_delivery"], late) == 403  # dead token cannot publish
        assert h.get_task(tok, "task_t0d1_revoke")[1]["status"] == "failed"


def test_uncertain_failure_keeps_callback_token_live():
    with AcceptingReceiver(status=502) as rx, \
            Harness(adapter_env=_env(rx.url, BENCHLINK_RESULT_SOURCE="http_callback")) as h:
        tok, _, st, _ = _create(h, "task_t0d1_keep")
        assert st == 503 and "task_t0d1_keep" in h.adapter.source._tokens


def test_fast_callback_result_survives_later_webhook_timeout():
    rx = AcceptingReceiver(respond_after=1.0, bot_after=0.0)
    rx.bot_go.set()  # the bot answers through the callback before the webhook reply
    with rx, Harness(adapter_env=_env(rx.url, BENCHLINK_RESULT_SOURCE="http_callback",
                                      BENCHLINK_WEBHOOK_TIMEOUT_S="0.5")) as h:
        tok, _, st, err = _create(h, "task_t0d1_fast")
        assert st == 504 and err["code"] == "timeout"
        view = h.get_task(tok, "task_t0d1_fast")[1]
        assert view["status"] == "succeeded" and view["summary"].startswith("Done by floyd"), view


def test_fast_callback_result_survives_later_definitive_rejection():
    rx = AcceptingReceiver(status=401, respond_after=0.5, bot_after=0.0)
    rx.bot_go.set()
    with rx, Harness(adapter_env=_env(rx.url, BENCHLINK_RESULT_SOURCE="http_callback",
                                      BENCHLINK_WEBHOOK_TIMEOUT_S="3")) as h:
        tok, _, st, _ = _create(h, "task_t0d1_fast_reject")
        assert st == 403
        view = h.get_task(tok, "task_t0d1_fast_reject")[1]
        assert view["status"] == "succeeded", view


def test_running_progress_summary_is_not_overwritten_by_uncertain_dispatch():
    class RunningReceiver(AcceptingReceiver):
        def _be_bot(self, d, delay):
            running = dict(d["result_template"], status="running", summary="bot is working",
                           artifact_links=[], error_class=None, created_at="2026-11-05T18:00:00-05:00")
            post_callback(d["result_delivery"], running)
    rx = RunningReceiver(respond_after=1.0, bot_after=0.0)
    with rx, Harness(adapter_env=_env(rx.url, BENCHLINK_RESULT_SOURCE="http_callback",
                                      BENCHLINK_WEBHOOK_TIMEOUT_S="0.5")) as h:
        tok, _, st, _ = _create(h, "task_t0d1_running")
        assert st == 504
        view = h.get_task(tok, "task_t0d1_running")[1]
        assert view["status"] == "running" and view["summary"] == "bot is working", view
