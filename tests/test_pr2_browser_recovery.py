"""Recovery actions cannot race End or unlock another window's accepted task."""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from benchlink_sidecar.client import BridgeClient
from benchlink_testkit import Harness
from test_pr1_recovery import local_sidecar
from test_retry_recovery import launch_browser, terminal_sidecar


@pytest.mark.parametrize("action,target", [("Send again", "ethan"), ("Send to Floyd", "floyd")])
def test_resend_locks_before_end_and_double_click_dispatches_once(terminal_sidecar, action, target):
    sync = pytest.importorskip("playwright.sync_api")
    shell, base, calls = terminal_sidecar
    with sync.sync_playwright() as p:
        browser = launch_browser(p)
        page = browser.new_page()
        page.set_default_timeout(5000)
        page.route("**/*", lambda r: r.continue_() if r.request.url.startswith(base + "/") else r.abort())
        requests = []
        page.on("request", lambda r: requests.append(r.url))
        page.goto(base)
        page.wait_for_function("loaded")
        page.evaluate("""snap => {
            show(snap);
            lastSent={goal:snap.goal,target:snap.target,high_priority:snap.high_priority};
            const originalPost=post;
            window.releaseEnd=null;
            post=async (path,body) => {
                if (path.endsWith('/end')) await new Promise(resolve => { window.releaseEnd=resolve; });
                return originalPost(path,body);
            };
        }""", shell.snapshot())
        page.evaluate("name => { actions[name](); actions[name](); }", action)
        page.wait_for_function("window.releaseEnd !== null")
        assert page.evaluate("recovering")
        assert page.locator("#send").is_disabled()
        page.evaluate("document.getElementById('send').click()")
        assert calls == []
        page.evaluate("window.releaseEnd()")
        page.wait_for_function("lastSnapshot.phase==='done' && !recovering && !busy")
        assert calls.count("sessionClose") == 1
        assert calls.count("sessionCreate") == calls.count("taskCreate") == 1
        assert sum(url.endswith("/_sidecar/ux/end") for url in requests) == 1
        assert sum(url.endswith("/_sidecar/ux/run") for url in requests) == 1
        assert page.evaluate("lastSnapshot.target") == target
        assert shell.goal == "Keep this exact task text"
        assert shell.high_priority
        browser.close()


def test_run_409_keeps_send_locked_after_other_window_finishes_dispatch(monkeypatch):
    sync = pytest.importorskip("playwright.sync_api")
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE": "silent"}) as h, local_sidecar(h) as (call, _, base):
        entered, release = threading.Event(), threading.Event()
        original_send = BridgeClient.send

        def delayed_send(self, request):
            result = original_send(self, request)
            if request.operation_id == "taskCreate":
                entered.set()
                assert release.wait(10)
            return result

        monkeypatch.setattr(BridgeClient, "send", delayed_send)
        with sync.sync_playwright() as p:
            browser = launch_browser(p)
            page = browser.new_page()
            page.set_default_timeout(5000)
            page.route("**/*", lambda r: r.continue_() if r.request.url.startswith(base + "/") else r.abort())
            requests = []
            page.on("request", lambda r: requests.append(r.url))
            page.goto(base)
            page.wait_for_function("loaded")
            page.locator("#goal").fill("second window must not replace the task")
            with ThreadPoolExecutor(1) as pool:
                first = pool.submit(call, "/_sidecar/ux/run", {"goal": "original window", "max_ticks": 1})
                try:
                    assert entered.wait(5)
                    page.locator("#send").click()
                    page.wait_for_function("!busy && document.getElementById('errTitle').textContent==='Task in progress'")
                    assert page.locator("#send").is_disabled()
                finally:
                    release.set()
                status, out = first.result(timeout=5)
            assert status == 200 and out["snapshot"]["task_in_flight"]
            assert page.locator("#send").is_disabled()
            page.evaluate("document.getElementById('send').click()")
            assert sum(url.endswith("/_sidecar/ux/run") for url in requests) == 1
            assert len(h.state.tasks) == len(h.state.sessions) == len(h.adapter.transport.dispatched) == 1
            assert call("/_sidecar/ux", method="GET")[1]["task_id"] == out["snapshot"]["task_id"]
            assert page.locator("#errPrimary").inner_text() == "Retry"
            page.locator("#errPrimary").click()
            page.wait_for_function("!busy && lastSnapshot.phase==='wait'")
            assert page.evaluate("lastSnapshot.task_id") == out["snapshot"]["task_id"]
            assert page.locator("#send").is_disabled()
            assert len(h.state.tasks) == len(h.state.sessions) == len(h.adapter.transport.dispatched) == 1
            browser.close()


def test_initial_status_failure_retry_loads_original_task_before_unlocking_send():
    sync = pytest.importorskip("playwright.sync_api")
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE": "silent"}) as h, local_sidecar(h) as (call, _, base):
        original = call("/_sidecar/ux/run", {"goal": "existing task before reload", "max_ticks": 1})[1]["snapshot"]
        status_requests = []
        with sync.sync_playwright() as p:
            browser = launch_browser(p)
            page = browser.new_page()
            page.set_default_timeout(5000)

            def route(request):
                if not request.request.url.startswith(base + "/"):
                    return request.abort()
                if request.request.url == base + "/_sidecar/ux":
                    status_requests.append(request.request.url)
                    if len(status_requests) == 1:
                        return request.abort()
                return request.continue_()

            page.route("**/*", route)
            page.goto(base)
            page.wait_for_function("!busy && localFailure")
            assert not page.evaluate("loaded")
            assert page.locator("#send").is_disabled()
            assert page.locator("#errPrimary").inner_text() == "Retry"
            page.locator("#errPrimary").click()
            page.wait_for_function("loaded && !busy && lastSnapshot.phase==='wait'")
            assert len(status_requests) == 2
            assert page.evaluate("lastSnapshot.task_id") == original["task_id"]
            assert page.locator("#send").is_disabled()
            assert len(h.state.tasks) == len(h.state.sessions) == len(h.adapter.transport.dispatched) == 1
            browser.close()
