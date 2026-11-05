"""Grace A4/B2/B4: local synthetic receipts, not live beta sign-off."""
import json
import threading
import time
import urllib.error
from dataclasses import replace

import pytest
from benchlink_testkit import Harness, valid
from benchlink_adapter.results import InMemoryDriveClient
from benchlink_sidecar import server
from benchlink_sidecar.client import BridgeClient
from benchlink_sidecar.ux import BridgeControlShell, Phase, TaskInFlight
from grace_beta_helpers import ArtifactReceiver
from test_webhook import Receiver, _env


@pytest.mark.parametrize("source", ["local_folder", "drive_folder", "http_callback"])
def test_a4_real_fixture_artifact_reaches_bridge_control(source, tmp_path):
    sync = pytest.importorskip("playwright.sync_api")
    drive = InMemoryDriveClient()
    with ArtifactReceiver(tmp_path) as rx, Harness(adapter_env=_env(rx.url,
            BENCHLINK_RESULT_SOURCE=source, BENCHLINK_RESULTS_DRIVE_FOLDER_ID="fixture-folder",
            BENCHLINK_RESULTS_POLL_INTERVAL_S="0"), drive_client=drive) as h:
        rx.drive = drive
        sidecar = server.serve(replace(h.sidecar_cfg, listen_port=0))
        threading.Thread(target=sidecar.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        base = "http://127.0.0.1:" + str(sidecar.server_address[1])
        try:
            with sync.sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
                context = browser.new_context()
                allowed = [base + "/", rx.url.rsplit("/", 1)[0] + "/"]
                context.route("**/*", lambda r: r.continue_() if any(r.request.url.startswith(a) for a in allowed) else r.abort())
                page = context.new_page(); page.set_default_timeout(8000)
                page.goto(base); page.locator("#pairBtn").click()
                page.wait_for_function("pairingState==='paired' && !pairingBusy")
                page.locator("button.opt").filter(has_text="Ethan").click()
                page.locator("#goal").fill("Create a local synthetic artifact receipt")
                page.wait_for_function("!document.getElementById('send').disabled")
                page.locator("#send").click(); page.wait_for_function("lastSnapshot.phase==='done'")
                snap = page.evaluate("lastSnapshot")
                assert snap["result"]["artifact_links"] and not valid("task.result", snap["result"])
                artifact = rx.artifacts[0]
                assert page.locator("#resultLinks a").first.get_attribute("href") == artifact["url"]
                with page.expect_popup() as popup:
                    page.locator("#resultLinks a").first.click()
                receipt = popup.value; receipt.wait_for_load_state()
                assert receipt.url == artifact["url"] and snap["task_id"] in receipt.locator("body").inner_text()
                assert len(h.state.tasks) == len(rx.requests) == 1
                task = h.state.tasks[snap["task_id"]]
                assert task["session_id"] == snap["session_id"] and task["request_id"]
                print(json.dumps({"item": "A4", "evidence": "local synthetic, not live", "source": source,
                                  "task_id": snap["task_id"], "session_id": snap["session_id"],
                                  "request_id": task["request_id"], "artifact": artifact}))
                browser.close()
        finally:
            sidecar.shutdown(); sidecar.server_close()


@pytest.mark.parametrize("raw", [b"<html>Bad Gateway</html>", b"{}", b'{"type":"task.result","status":"running"}'])
def test_b2_non_envelope_502_is_offline_and_retry_only_gets_same_task(raw, monkeypatch):
    with Harness() as h:
        bad = [True]
        Handler = h.srv.RequestHandlerClass; original_get = Handler.do_GET
        def get(self):
            if self.path.startswith("/v1/tasks/") and bad[0]:
                self.send_response(502); self.send_header("Content-Length", str(len(raw)))
                self.end_headers(); self.wfile.write(raw)
            else:
                original_get(self)
        monkeypatch.setattr(Handler, "do_GET", get)
        shell = BridgeControlShell(h.sidecar_cfg, client=h.client)
        snap = shell.start_and_wait("B2 local gateway failure")["snapshot"]
        original = snap["task_id"], snap["session_id"]
        assert snap["phase"] == "error" and snap["error"]["code"] == "offline"
        assert snap["error"]["http"] == 502 and shell.can_resume_interrupted_wait()
        assert snap["presentation"]["error"]["primary"] == "Retry"
        bad[0] = False; shell.retry_terminal_error()
        assert shell.phase == Phase.DONE and (shell.task_id, shell.session_id) == original
        assert len(h.state.tasks) == len(h.state.sessions) == len(h.adapter.transport.dispatched) == 1
        assert h.state.sessions[shell.session_id]["status"] == "open"


def test_b4_six_second_dispatch_survives_five_second_read_budget(tmp_path):
    with ArtifactReceiver(tmp_path, delay=6.0) as rx, Harness(adapter_env=_env(rx.url)) as h:
        assert h.client.timeout == 5.0 and h.adapter.cfg.webhook_timeout_s == 10.0
        shell = BridgeControlShell(h.sidecar_cfg, client=h.client, poll_sleep=0.01, max_wait_ticks=50)
        started = time.monotonic(); snap = shell.start_and_wait("Slow local dispatch", target_key="ethan")["snapshot"]
        elapsed = time.monotonic() - started
        assert snap["phase"] == "done" and snap["result"]["status"] == "succeeded", snap["error"]
        assert 5 < elapsed < 10 and len(rx.requests) == len(h.state.tasks) == 1
        print(json.dumps({"item": "B4", "evidence": "real loopback delay, not live", "delay_s": 6, "client_read_s": 5,
                          "webhook_s": 10, "elapsed_s": round(elapsed, 3), "phase": snap["phase"]}))


def test_b4_ten_second_webhook_deadline_reaches_client_as_504_timeout():
    with Receiver(delay=10.5, act=False) as rx, Harness(adapter_env=_env(rx.url)) as h:
        shell = BridgeControlShell(h.sidecar_cfg, client=h.client)
        started = time.monotonic(); snap = shell.start_and_wait("Exceed local webhook deadline", target_key="ethan")["snapshot"]
        elapsed = time.monotonic() - started
        assert snap["phase"] == "error" and snap["error"]["code"] == "timeout", snap["error"]
        assert snap["error"]["http"] == 504 and 9.5 <= elapsed < 15
        task = h.state.tasks[snap["task_id"]]
        assert task["status"] == "failed" and task["error_class"] == "timeout"
        assert shell.can_resume_interrupted_wait() and snap["submission_uncertain"]
        identity = shell.task_id, shell.session_id
        shell.retry_terminal_error()  # only a full matching terminal GET releases ownership
        assert not shell.can_resume_interrupted_wait() and not shell.submission_uncertain
        assert (shell.task_id, shell.session_id) == identity and len(rx.requests) == 1
        assert len(h.state.tasks) == len(h.state.sessions) == 1
        print(json.dumps({"item": "B4", "evidence": "real loopback deadline, not live", "webhook_s": 10,
                          "elapsed_s": round(elapsed, 3), "code": snap["error"]["code"], "http": snap["error"]["http"]}))


def test_b4_five_second_poll_timeout_is_explicit_and_resumes_same_id(monkeypatch):
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE": "silent"}) as h:
        shell = BridgeControlShell(h.sidecar_cfg, client=h.client, max_wait_ticks=1, poll_sleep=0)
        snap = shell.start_and_wait("Keep the accepted task")["snapshot"]
        original = snap["task_id"], snap["session_id"]
        entered, release = threading.Event(), threading.Event()
        Handler = h.srv.RequestHandlerClass; original_get = Handler.do_GET
        def get(self):
            if self.path.startswith("/v1/tasks/"):
                entered.set(); release.wait(8)
            try: original_get(self)
            except (BrokenPipeError, ConnectionResetError): pass
        monkeypatch.setattr(Handler, "do_GET", get)
        try:
            started = time.monotonic(); shell.wait_once(); elapsed = time.monotonic() - started
            assert entered.is_set() and 4.5 <= elapsed < 7
            snap = shell.snapshot()
            assert snap["phase"] == "error" and snap["error"]["code"] == "timeout"
            assert shell.can_resume_interrupted_wait() and (shell.task_id, shell.session_id) == original
            assert snap["presentation"]["error"]["primary"] == "Retry"
            with pytest.raises(TaskInFlight): shell.start_and_wait("must not duplicate")
        finally: release.set()
        monkeypatch.setattr(Handler, "do_GET", original_get)
        shell.retry_terminal_error()
        assert shell.phase == Phase.WAIT and (shell.task_id, shell.session_id) == original
        assert len(h.state.tasks) == len(h.adapter.transport.dispatched) == 1
        print(json.dumps({"item": "B4", "evidence": "real loopback poll timeout, not live", "read_s": 5,
                          "elapsed_s": round(elapsed, 3), "resumed_same_id": True}))


def test_b4_uncertain_dispatch_timeout_retry_never_creates_a_second_task(tmp_path, monkeypatch):
    with ArtifactReceiver(tmp_path, delay=0.2) as rx, Harness(adapter_env=_env(rx.url)) as h:
        client = BridgeClient(h.sidecar_cfg, timeout=0.05)
        client.dispatch_timeout = 0.05
        client.pair()
        shell = BridgeControlShell(h.sidecar_cfg, client=client, poll_sleep=0)
        snap = shell.start_and_wait("Do not resend an unknown dispatch", target_key="ethan")["snapshot"]
        original = snap["task_id"], snap["session_id"]
        assert snap["error"]["code"] == "timeout" and snap["submission_uncertain"] is True
        assert snap["task_accepted"] is False and shell.can_resume_interrupted_wait()
        with pytest.raises(TaskInFlight): shell.start_and_wait("must not duplicate")
        with pytest.raises(TaskInFlight): shell.pick("floyd")
        with pytest.raises(TaskInFlight): shell.compose("must not replace goal")
        client.timeout = 5
        deadline = time.monotonic() + 2
        while not rx.artifacts and time.monotonic() < deadline:
            threading.Event().wait(0.01)
        assert rx.artifacts
        shell.retry_terminal_error()
        assert shell.phase == Phase.DONE and (shell.task_id, shell.session_id) == original
        assert shell.task_accepted and not shell.submission_uncertain
        assert len(h.state.tasks) == len(rx.requests) == 1


@pytest.mark.parametrize("wrapped", [False, True])
def test_b4_pair_and_request_timeout_classifier_handles_wrapped_socket_timeout(monkeypatch, wrapped):
    with Harness() as h:
        from benchlink_sidecar import client as module
        reason = TimeoutError("local fixture deadline")
        error = urllib.error.URLError(reason) if wrapped else reason
        def expired(*args, **kwargs): raise error
        monkeypatch.setattr(module.urllib.request, "urlopen", expired)
        with pytest.raises((TimeoutError, urllib.error.URLError)) as caught: h.client.health()
        assert h.client.pairing_failure_code(caught.value) == "timeout"


@pytest.mark.parametrize("acknowledged", [True, False])
def test_b4_browser_timeout_retry_preserves_existing_or_uncertain_task(acknowledged, monkeypatch):
    from test_reconnect_recovery import accepted_sidecar, accept_and_interrupt
    generator = accepted_sidecar.__wrapped__(monkeypatch)
    shell, base, mode, calls, ids = next(generator)
    try:
        accept_and_interrupt(base)
        shell.error["code"] = "timeout"
        shell.task_accepted = acknowledged
        shell.submission_uncertain = not acknowledged
        snap = shell.snapshot(); original = shell.task_id, shell.session_id
        sync = pytest.importorskip("playwright.sync_api")
        with sync.sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page(); page.set_default_timeout(7000)
            page.route("**/*", lambda r: r.continue_() if r.request.url.startswith(base + "/") else r.abort())
            page.goto(base); page.wait_for_function("taskStateLoaded && connectionKey==='disconnected'")
            page.evaluate("snap => show(snap)", snap)
            assert page.locator("#send").is_disabled()
            assert page.locator("#errPrimary").text_content() == "Retry"
            assert page.evaluate("[lastSnapshot.task_id,lastSnapshot.session_id]") == list(original)
            mode["online"], mode["finish"] = True, True
            page.evaluate("actions.Retry()")
            page.wait_for_function("lastSnapshot.phase==='done'")
            assert page.evaluate("[lastSnapshot.task_id,lastSnapshot.session_id]") == list(original)
            assert calls.count("taskCreate") == calls.count("sessionCreate") == 1
            assert calls.count("sessionClose") == 0 and set(ids) == {original[0]}
            browser.close()
    finally:
        generator.close()


@pytest.mark.parametrize("status,raw,code", [(502, b"<html>Bad Gateway</html>", "offline"), (504, b"{}", "timeout")])
def test_b2_b4_gateway_lost_create_ack_retry_queries_same_task(status, raw, code, monkeypatch):
    # The daemon already dispatched; only its acknowledgement is replaced by a gateway.
    with Harness() as h:
        Handler = h.srv.RequestHandlerClass; original_send = Handler._send
        def send(self, st, payload, type_name=None, list_of=None):
            if self.path == "/v1/tasks" and st == 202:
                self.send_response(status); self.send_header("Content-Length", str(len(raw)))
                self.end_headers(); self.wfile.write(raw)
            else:
                original_send(self, st, payload, type_name, list_of)
        monkeypatch.setattr(Handler, "_send", send)
        shell = BridgeControlShell(h.sidecar_cfg, client=h.client, poll_sleep=0)
        snap = shell.start_and_wait("Never resend an unacknowledged dispatch")["snapshot"]
        original = snap["task_id"], snap["session_id"]
        assert snap["phase"] == "error" and snap["error"]["code"] == code
        assert snap["error"]["http"] == status and snap["submission_uncertain"]
        assert not snap["task_accepted"] and shell.can_resume_interrupted_wait()
        assert snap["presentation"]["error"]["primary"] == "Retry"
        with pytest.raises(TaskInFlight): shell.start_and_wait("must not duplicate")
        shell.retry_terminal_error()
        assert shell.phase == Phase.DONE and (shell.task_id, shell.session_id) == original
        assert len(h.state.tasks) == len(h.state.sessions) == len(h.adapter.transport.dispatched) == 1
