"""Larry lost-ack review: malformed gateway bodies never authorize redispatch."""
import io
import json
import urllib.error
import pytest
from benchlink_testkit import Harness, valid
from benchlink_sidecar import client as module
from benchlink_sidecar.ux import BridgeControlShell, Phase, TaskInFlight

FULL = {"v": "1", "type": "error", "code": "offline", "message": "Local fixture gateway failure",
        "retryable": True, "correlation_id": "req_fixture"}
BAD = [pytest.param({"type": "error"}, id="type-only"),
       pytest.param(dict(FULL, arbitrary="untrusted"), id="arbitrary-field"),
       pytest.param(dict(FULL, task_id="task_other"), id="extra-wrong-task-id"),
       pytest.param(dict(FULL, session_id="sess_other"), id="extra-wrong-session-id"),
       pytest.param(dict(FULL, v=1), id="wrong-version-type"),
       pytest.param(dict(FULL, code="unknown"), id="unknown-code"),
       pytest.param(dict(FULL, code=[]), id="list-code"),
       pytest.param(dict(FULL, message=None), id="null-message"),
       pytest.param(dict(FULL, retryable=1), id="integer-retryable"),
       pytest.param(dict(FULL, correlation_id="bad/id"), id="bad-correlation-id"),
       pytest.param(dict(FULL, correlation_id=None), id="null-correlation-id"),
       pytest.param([FULL], id="list-body"), pytest.param(None, id="null-body")]
BAD += [pytest.param({k: v for k, v in FULL.items() if k != missing}, id="missing-" + missing) for missing in FULL]


@pytest.mark.parametrize("status", [200, 400, 401, 403, 500, 501, 502, 504])
@pytest.mark.parametrize("body", BAD)
def test_malformed_create_error_ack_preserves_ids_and_only_gets_existing_task(body, status, monkeypatch):
    assert valid("error", body), "fixture must be invalid against the actual frozen contract"
    with Harness() as h:
        original_send = h.srv.RequestHandlerClass._send
        acknowledgements = []
        def send(self, st, payload, type_name=None, list_of=None):
            if self.path == "/v1/tasks" and st == 202:
                acknowledgements.append(payload["task_id"])
                raw = json.dumps(body).encode()
                self.send_response(status); self.send_header("Content-Length", str(len(raw)))
                self.end_headers(); self.wfile.write(raw)
            else:
                original_send(self, st, payload, type_name, list_of)
        monkeypatch.setattr(h.srv.RequestHandlerClass, "_send", send)
        shell = BridgeControlShell(h.sidecar_cfg, client=h.client, poll_sleep=0)
        snap = shell.start_and_wait("Dispatch once despite a malformed acknowledgement")["snapshot"]
        identity = snap["task_id"], snap["session_id"]
        assert snap["phase"] == "error" and snap["submission_uncertain"]
        assert snap["error"]["code"] == ("timeout" if status == 504 else "offline")
        assert snap["error"]["http"] == (502 if status == 200 else status) and shell.can_resume_interrupted_wait()
        with pytest.raises(TaskInFlight): shell.start_and_wait("forbidden second dispatch")
        with pytest.raises(TaskInFlight): shell.pick("ethan")
        with pytest.raises(TaskInFlight): shell.compose("forbidden replacement")
        st, finished = h.wait_final(h.client._live_token, identity[0])
        assert st == 200 and finished["status"] == "succeeded"
        shell.retry_terminal_error()
        assert shell.phase == Phase.DONE and (shell.task_id, shell.session_id) == identity
        assert acknowledgements == [identity[0]]
        assert len(h.state.tasks) == len(h.state.sessions) == len(h.adapter.transport.dispatched) == 1
        assert h.state.sessions[identity[1]]["status"] == "open"


@pytest.mark.parametrize("code", ["auth.expired", "auth.denied", "auth.unpaired", "session.not_found",
                                    "task.not_found", "validation", "timeout", "offline", "routing", "internal"])
def test_valid_complete_frozen_error_retains_its_contract_fields(code, monkeypatch):
    body = dict(FULL, code=code)
    assert not valid("error", body)
    with Harness() as h:
        def response(*args, **kwargs):
            raise urllib.error.HTTPError(h.base + "/v1/health", 502, "Local fixture", {}, io.BytesIO(json.dumps(body).encode()))
        monkeypatch.setattr(module.urllib.request, "urlopen", response)
        assert h.client.health() == (502, body)


@pytest.mark.parametrize("wrapped", [False, True])
def test_http_error_body_read_deadline_is_timeout_with_safe_same_id_retry(wrapped, monkeypatch):
    with Harness() as h:
        h.client.pair()
        real_open = module.urllib.request.urlopen
        class SlowErrorBody:
            def read(self, *args):
                error = TimeoutError("Local fixture HTTPError body deadline")
                raise urllib.error.URLError(error) if wrapped else error
            def close(self): pass
        def response(req, *args, **kwargs):
            result = real_open(req, *args, **kwargs)
            if req.get_method() == "POST" and req.full_url.endswith("/v1/tasks"):
                result.close()  # task was actually accepted/dispatched before the gateway failed
                raise urllib.error.HTTPError(req.full_url, 502, "Local fixture", {}, SlowErrorBody())
            return result
        monkeypatch.setattr(module.urllib.request, "urlopen", response)
        shell = BridgeControlShell(h.sidecar_cfg, client=h.client, poll_sleep=0)
        snap = shell.start_and_wait("Keep tracking when reading an error body times out")["snapshot"]
        identity = snap["task_id"], snap["session_id"]
        assert snap["error"]["code"] == "timeout" and snap["error"]["http"] == 504
        assert snap["submission_uncertain"] and shell.can_resume_interrupted_wait()
        with pytest.raises(TaskInFlight): shell.start_and_wait("forbidden second dispatch")
        st, finished = h.wait_final(h.client._live_token, identity[0])
        assert st == 200 and finished["status"] == "succeeded"
        shell.retry_terminal_error()
        assert shell.phase == Phase.DONE and (shell.task_id, shell.session_id) == identity
        assert len(h.state.tasks) == len(h.state.sessions) == len(h.adapter.transport.dispatched) == 1


def test_browser_malformed_ack_retry_never_sends_a_second_task(monkeypatch):
    import threading
    from dataclasses import replace
    from benchlink_sidecar import server
    sync = pytest.importorskip("playwright.sync_api")
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE": "silent"}) as h:
        Handler = h.srv.RequestHandlerClass
        original_send, original_get = Handler._send, Handler.do_GET
        allow_poll = threading.Event(); acknowledgements = []
        def send(self, status, payload, type_name=None, list_of=None):
            if self.path == "/v1/tasks" and status == 202:
                acknowledgements.append(payload["task_id"])
                raw = b'{"type":"error"}'
                self.send_response(502); self.send_header("Content-Length", str(len(raw)))
                self.end_headers(); self.wfile.write(raw)
            else: original_send(self, status, payload, type_name, list_of)
        def get(self):
            if self.path.startswith("/v1/tasks/") and not allow_poll.is_set():
                raw = b'{"type":"error"}'
                self.send_response(502); self.send_header("Content-Length", str(len(raw)))
                self.end_headers(); self.wfile.write(raw)
            else: original_get(self)
        monkeypatch.setattr(Handler, "_send", send); monkeypatch.setattr(Handler, "do_GET", get)
        sidecar = server.serve(replace(h.sidecar_cfg, listen_port=0))
        threading.Thread(target=sidecar.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        base = "http://127.0.0.1:" + str(sidecar.server_address[1])
        try:
            with sync.sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
                page = browser.new_page(); page.set_default_timeout(7000)
                page.route("**/*", lambda r: r.continue_() if r.request.url.startswith(base + "/") else r.abort())
                page.goto(base); page.locator("#pairBtn").click()
                page.wait_for_function("pairingState==='paired' && !pairingBusy")
                page.locator("#goal").fill("Only one dispatch after malformed gateway acknowledgement")
                page.locator("#send").click(); page.wait_for_function("lastSnapshot.phase==='error'")
                before = page.evaluate("lastSnapshot")
                identity = before["task_id"], before["session_id"]
                assert before["submission_uncertain"] and page.locator("#send").is_disabled()
                assert page.locator("#errPrimary").text_content() == "Retry"
                payload = h.adapter.transport.dispatched[0]
                result = dict(payload["result_template"], status="succeeded", summary="Local adversarial proof",
                              artifact_links=[], error_class=None, created_at="2026-11-05T15:00:00-05:00")
                assert not valid("task.result", result)
                h.adapter.transport.source.simulate_bot_write(identity[0], result)
                allow_poll.set(); page.evaluate("actions.Retry()")
                page.wait_for_function("lastSnapshot.phase==='done'")
                assert page.evaluate("[lastSnapshot.task_id,lastSnapshot.session_id]") == list(identity)
                assert acknowledgements == [identity[0]]
                assert len(h.state.tasks) == len(h.state.sessions) == len(h.adapter.transport.dispatched) == 1
                assert h.state.sessions[identity[1]]["status"] == "open"
                browser.close()
        finally:
            sidecar.shutdown(); sidecar.server_close()


@pytest.mark.parametrize("status", [200, 202])
@pytest.mark.parametrize("wrong", ["task_id", "session_id", "both", "invalid-json"])
def test_success_status_unknown_or_miscorrelated_ack_only_queries_original_task(status, wrong, monkeypatch):
    with Harness() as h:
        original_send = h.srv.RequestHandlerClass._send
        posts = []
        def send(self, st, payload, type_name=None, list_of=None):
            if self.path == "/v1/tasks" and st == 202:
                posts.append(payload["task_id"])
                changed = dict(payload)
                if wrong in {"task_id", "both"}: changed["task_id"] = "task_other"
                if wrong in {"session_id", "both"}: changed["session_id"] = "sess_other"
                if wrong != "invalid-json": assert not valid("task.result", changed)
                raw = b"not-json" if wrong == "invalid-json" else json.dumps(changed).encode()
                self.send_response(status); self.send_header("Content-Length", str(len(raw)))
                self.end_headers(); self.wfile.write(raw)
            else: original_send(self, st, payload, type_name, list_of)
        monkeypatch.setattr(h.srv.RequestHandlerClass, "_send", send)
        shell = BridgeControlShell(h.sidecar_cfg, client=h.client, poll_sleep=0)
        snap = shell.start_and_wait("Correlate the task acknowledgement before trusting it")["snapshot"]
        identity = snap["task_id"], snap["session_id"]
        assert snap["phase"] == "error" and snap["submission_uncertain"]
        assert snap["error"]["code"] == "offline" and shell.can_resume_interrupted_wait()
        with pytest.raises(TaskInFlight): shell.start_and_wait("forbidden second dispatch")
        st, done = h.wait_final(h.client._live_token, identity[0])
        assert st == 200 and done["status"] == "succeeded"
        shell.retry_terminal_error()
        assert shell.phase == Phase.DONE and (shell.task_id, shell.session_id) == identity
        assert posts == [identity[0]]
        assert len(h.state.tasks) == len(h.state.sessions) == len(h.adapter.transport.dispatched) == 1
