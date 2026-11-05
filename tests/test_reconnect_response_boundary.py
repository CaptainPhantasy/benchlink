"""Operation completion, not response transport, controls the task lock."""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from benchlink_sidecar import server
from benchlink_sidecar.client import BridgeClient
from test_reconnect_recovery import (
    accepted_sidecar as original_accepted_sidecar,
    accept_and_interrupt,
)
from test_retry_recovery import post


@pytest.fixture
def response_gate(monkeypatch):
    """Pause a real handler before/after writing JSON, never during task work."""
    gate = {"route": None, "status": None, "before_write": False,
            "entered": threading.Event(), "release": threading.Event(),
            "finished": threading.Event(), "run_finished": threading.Event()}
    original_make = server.make_handler

    def make(cfg):
        handler = original_make(cfg)
        write, handle_post = handler._json, handler.do_POST

        def gated_write(self, status, payload):
            held = self.path == gate["route"] and status == gate["status"]
            if held:
                gate["route"] = None
                self._test_response_held = True
            if not held or not gate["before_write"]:
                write(self, status, payload)
            if held:
                gate["entered"].set()
                if not gate["release"].wait(12):
                    raise RuntimeError("response scheduler gate was not released")
                if gate["before_write"]:
                    write(self, status, payload)

        def observed_post(self):
            try:
                return handle_post(self)
            finally:
                if self.path == "/_sidecar/ux/run":
                    gate["run_finished"].set()
                if getattr(self, "_test_response_held", False):
                    gate["finished"].set()

        handler._json, handler.do_POST = gated_write, observed_post
        return handler

    monkeypatch.setattr(server, "make_handler", make)
    try:
        yield gate
    finally:
        gate["release"].set()


@pytest.fixture
def accepted_sidecar(response_gate, monkeypatch):
    yield from original_accepted_sidecar.__wrapped__(monkeypatch)


@pytest.mark.parametrize("route,status,before_write", [
    ("/_sidecar/ux/run", 200, False),
    ("/_sidecar/ux/run", 409, False),
    ("/_sidecar/ux/poll", 200, False),
    ("/_sidecar/ux/retry", 200, False),
    ("/_sidecar/ux/poll", 200, True),
])
def test_finished_operation_releases_lock_before_response_transport(
        response_gate, accepted_sidecar, route, status, before_write):
    shell, base, mode, calls, ids = accepted_sidecar
    gate = response_gate
    if not (route.endswith("run") and status == 200):
        accept_and_interrupt(base)
        assert gate["run_finished"].wait(2)
        if status == 200:
            mode["online"] = True
    gate.update(route=route, status=status, before_write=before_write)
    body = {"target": "ethan", "goal": "Resume the original accepted task", "max_ticks": 1}
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(post, base, route, body)
        try:
            assert gate["entered"].wait(2)
            original = shell.task_id, shell.session_id
            assert all(original)
            if not before_write:
                # The entire previous response has already reached the client.
                assert first.result(timeout=2)[0] == status
            mode["online"], mode["finish"] = True, True
            next_route = "/_sidecar/ux/retry" if route.endswith("run") else "/_sidecar/ux/poll"
            next_status, result = post(base, next_route)
            assert next_status == 200, result
            assert result["phase"] == "done"
            assert (result["task_id"], result["session_id"]) == original
            assert calls.count("taskCreate") == calls.count("sessionCreate") == 1
            assert calls.count("sessionClose") == 0 and set(ids) == {original[0]}
        finally:
            gate["release"].set()
        assert first.result(timeout=2)[0] == status
        assert gate["finished"].wait(2)


def test_actual_upstream_operation_still_refuses_overlapping_mutations(
        response_gate, accepted_sidecar, monkeypatch):
    shell, base, mode, calls, ids = accepted_sidecar
    initial = accept_and_interrupt(base)
    assert response_gate["run_finished"].wait(2)
    original = initial["task_id"], initial["session_id"]
    entered, release = threading.Event(), threading.Event()
    send = BridgeClient.send

    def blocked(self, request):
        if request.operation_id == "taskGet":
            entered.set()
            if not release.wait(5):
                raise RuntimeError("upstream test gate was not released")
        return send(self, request)

    monkeypatch.setattr(BridgeClient, "send", blocked)
    mode["online"], mode["finish"] = True, True
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(post, base, "/_sidecar/ux/poll")
        try:
            assert entered.wait(2)
            for route in ["run", "retry", "poll"]:
                assert post(base, "/_sidecar/ux/" + route, {"goal": "must not duplicate"})[0] == 409
            assert (shell.task_id, shell.session_id) == original
            assert calls.count("taskCreate") == 1 and calls.count("sessionClose") == 0
        finally:
            release.set()
        status, result = first.result(timeout=2)
        assert status == 200 and result["phase"] == "done"
        assert (result["task_id"], result["session_id"]) == original
        assert calls.count("taskCreate") == calls.count("sessionCreate") == 1
        assert calls.count("sessionClose") == 0 and set(ids) == {original[0]}


def test_browser_reconnect_survives_completed_response_preemption(
        response_gate, accepted_sidecar):
    sync = pytest.importorskip("playwright.sync_api")
    shell, base, mode, calls, ids = accepted_sidecar
    initial = accept_and_interrupt(base)
    original = initial["task_id"], initial["session_id"]
    gate = response_gate
    assert gate["run_finished"].wait(2)
    with sync.sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        page.set_default_timeout(7000)
        page.route("**/*", lambda r: r.continue_() if r.request.url.startswith(base + "/") else r.abort())
        responses, errors = [], []
        page.on("response", lambda r: responses.append(r.status) if r.url.endswith("/_sidecar/ux/poll") else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        try:
            page.goto(base)
            page.wait_for_function("connectionKey==='disconnected'")
            page.locator("#goal").fill(initial["goal"])
            page.evaluate("snap => show(snap)", initial)
            gate.update(route="/_sidecar/ux/poll", status=200)
            mode["online"] = True
            page.evaluate("void actions.Retry()")
            page.wait_for_function("lastSnapshot.phase==='wait' && polling")
            assert gate["entered"].is_set()
            mode["finish"] = True
            page.wait_for_function("lastSnapshot.phase==='done' || (lastSnapshot.error || {}).code==='validation'")
            assert page.evaluate("lastSnapshot.phase") == "done"
            assert page.evaluate("[lastSnapshot.task_id,lastSnapshot.session_id]") == list(original)
            assert "Same accepted task" in page.locator("#resultSummary").inner_text()
            assert responses == [200, 200] and not errors
            assert calls.count("taskCreate") == calls.count("sessionCreate") == 1
            assert calls.count("sessionClose") == 0 and set(ids) == {original[0]}
        finally:
            gate["release"].set()
            browser.close()
