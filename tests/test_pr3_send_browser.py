"""Send and target controls share task ownership across browser recovery."""
import json

import pytest
from benchlink_testkit import Harness
from test_pr1_recovery import local_sidecar, result_for
from test_retry_recovery import launch_browser


def assert_controls_locked(page):
    assert page.locator("#send").is_disabled()
    assert page.locator("#end").is_disabled()
    assert page.locator("#hp").is_disabled()
    assert page.locator("#goal").evaluate("element => element.readOnly")
    assert page.locator("#roster button:disabled").count() == page.locator("#roster button").count()


def assert_roster_unlocked(page):
    assert page.locator("#roster button:disabled").count() == 0
    assert page.locator("#hp").is_enabled()
    assert not page.locator("#goal").evaluate("element => element.readOnly")


def test_radio_keyboard_selection_updates_accessible_state_and_heading():
    sync = pytest.importorskip("playwright.sync_api")
    with Harness() as h, local_sidecar(h) as (_, _, base), sync.sync_playwright() as p:
        browser = launch_browser(p)
        page = browser.new_page()
        page.goto(base)
        page.wait_for_function("loaded && !busy && connectionKey === 'connected'")
        if page.evaluate("pairingState !== 'paired'"):
            page.locator("#pairBtn").click()
            page.wait_for_function("pairingState === 'paired' && !pairingBusy")
        group = page.get_by_role("radiogroup", name="Staff target")
        radios = group.get_by_role("radio")
        assert radios.count() == 8
        assert page.locator("#send").count() == 1
        assert group.locator('[aria-checked="true"]').count() == 1
        assert radios.nth(0).get_attribute("aria-checked") == "true"
        radios.nth(0).focus()
        page.keyboard.press("ArrowRight")
        assert page.evaluate("target") == "ethan"
        assert page.locator("#composeHeading").inner_text() == "Compose task for Ethan"
        assert group.locator('[aria-checked="true"]').get_attribute("tabindex") == "0"
        assert page.evaluate("document.activeElement.getAttribute('aria-checked')") == "true"
        page.keyboard.press("End")
        assert page.evaluate("target") == "grace"
        assert "Client Success" in group.locator('[aria-checked="true"]').inner_text()
        page.keyboard.press("Home")
        assert page.evaluate("target") == "floyd"
        assert page.locator("#composeHeading").inner_text() == "Compose task for Floyd"
        assert_roster_unlocked(page)
        browser.close()


@pytest.mark.parametrize("reply", ["rejected", "non-json"])
def test_rejected_run_recovers_all_controls_only_after_confirming_no_task(reply):
    sync = pytest.importorskip("playwright.sync_api")
    with Harness() as h, local_sidecar(h) as (_, _, base), sync.sync_playwright() as p:
        browser = launch_browser(p)
        page = browser.new_page()
        page.set_default_timeout(5000)

        def route(request):
            if not request.request.url.startswith(base + "/"):
                return request.abort()
            if request.request.url.endswith("/_sidecar/ux/run"):
                if reply == "non-json":
                    return request.fulfill(status=502, content_type="text/html", body="Bad gateway")
                return request.fulfill(status=422, content_type="application/json",
                    body=json.dumps({"type": "error", "code": "validation", "message": "Rejected fixture"}))
            return request.continue_()

        page.route("**/*", route)
        page.goto(base)
        page.wait_for_function("loaded && !busy && connectionKey === 'connected'")
        if page.evaluate("pairingState !== 'paired'"):
            page.locator("#pairBtn").click()
            page.wait_for_function("pairingState === 'paired' && !pairingBusy")
        page.locator("#goal").fill("Keep ownership until recovery confirms rejection")
        page.locator("#send").click()
        page.wait_for_function("localFailure && !busy")
        assert_controls_locked(page)
        assert not h.state.tasks and not h.state.sessions
        assert page.locator("#errPrimary").inner_text() == "Retry"
        page.locator("#errPrimary").click()
        page.wait_for_function("!busy && !localFailure && lastSnapshot.phase==='pick'")
        assert_roster_unlocked(page)
        page.get_by_role("radio", name="Ethan", exact=False).click()
        assert page.evaluate("target") == "ethan"
        page.locator("#goal").fill("A fresh task may now be sent")
        assert page.locator("#send").is_enabled()
        assert not h.state.tasks and not h.state.sessions
        browser.close()


def test_dispatch_and_wait_keep_all_controls_locked_until_matching_result():
    sync = pytest.importorskip("playwright.sync_api")
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE": "silent"}) as h, local_sidecar(h) as (call, _, base), sync.sync_playwright() as p:
        browser = launch_browser(p)
        page = browser.new_page()
        page.set_default_timeout(5000)
        page.goto(base)
        page.wait_for_function("loaded && !busy && connectionKey === 'connected'")
        if page.evaluate("pairingState !== 'paired'"):
            page.locator("#pairBtn").click()
            page.wait_for_function("pairingState === 'paired' && !pairingBusy")
        page.locator("#goal").fill("One accepted task")
        page.evaluate("""() => {
            const originalPost=post;
            window.releaseRun=null;
            post=async (path,body) => {
                if (path.endsWith('/run')) await new Promise(resolve => { window.releaseRun=resolve; });
                return originalPost(path,body);
            };
        }""")
        page.locator("#send").click()
        page.wait_for_function("window.releaseRun !== null")
        assert_controls_locked(page)
        page.evaluate("window.releaseRun()")
        page.wait_for_function("!busy && lastSnapshot.phase==='wait'")
        identity = page.evaluate("lastSnapshot.task_id")
        assert_controls_locked(page)
        assert call("/_sidecar/ux/end")[0] == 409
        page.evaluate("document.getElementById('end').click(); document.getElementById('send').click()")
        assert len(h.state.tasks) == len(h.state.sessions) == 1
        h.adapter.source.simulate_bot_write(identity, result_for(h))
        page.evaluate("retryPoll()")
        page.wait_for_function("!busy && lastSnapshot.phase==='done'")
        assert_roster_unlocked(page)
        assert page.locator("#send").is_enabled()
        assert len(h.adapter.transport.dispatched) == 1
        browser.close()


def test_prior_poll_timer_cannot_unlock_a_newer_dispatch():
    sync = pytest.importorskip("playwright.sync_api")
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE": "silent"}) as h, local_sidecar(h) as (_, _, base), sync.sync_playwright() as p:
        browser = launch_browser(p)
        page = browser.new_page()
        page.set_default_timeout(5000)
        page.clock.install()
        requests = []
        page.on("request", lambda request: requests.append(request.url))
        page.goto(base)
        page.wait_for_function("loaded && !busy && connectionKey === 'connected'")
        if page.evaluate("pairingState !== 'paired'"):
            page.locator("#pairBtn").click()
            page.wait_for_function("pairingState === 'paired' && !pairingBusy")
        page.locator("#goal").fill("Original task")
        page.locator("#send").click()
        page.wait_for_function("!busy && lastSnapshot.phase==='wait'")
        identity = page.evaluate("lastSnapshot.task_id")
        h.adapter.source.simulate_bot_write(identity, result_for(h))
        # Poll explicitly before the original two-second timer wakes, then end
        # the confirmed terminal task and hold the next dispatch open.
        page.evaluate("retryPoll()")
        page.wait_for_function("!busy && lastSnapshot.phase==='done'")
        page.locator("#end").click()
        page.wait_for_function("!busy && lastSnapshot.phase==='pick'")
        page.locator("#goal").fill("New task owns the controls")
        page.evaluate("""() => {
            const originalPost=post;
            window.releaseRun=null;
            post=async (path,body) => {
                if (path.endsWith('/run')) await new Promise(resolve => { window.releaseRun=resolve; });
                return originalPost(path,body);
            };
        }""")
        polls_before = sum(url.endswith("/_sidecar/ux/poll") for url in requests)
        page.locator("#send").click()
        page.wait_for_function("window.releaseRun !== null")
        page.clock.fast_forward(3000)
        assert page.evaluate("busy")
        assert_controls_locked(page)
        assert sum(url.endswith("/_sidecar/ux/poll") for url in requests) == polls_before
        page.evaluate("document.getElementById('send').click()")
        page.evaluate("window.releaseRun()")
        page.wait_for_function("!busy && lastSnapshot.phase==='wait'")
        assert page.evaluate("lastSnapshot.task_id") != identity
        assert len(h.state.tasks) == len(h.state.sessions) == len(h.adapter.transport.dispatched) == 2
        browser.close()
