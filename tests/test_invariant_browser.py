"""Real Chromium multi-step recovery; no external origins or live services."""
import threading
from dataclasses import replace

import pytest
from test_retry_recovery import launch_browser
from benchlink_testkit import Harness
from benchlink_sidecar import server
from test_recovery_invariant import STATUSES, install


def start(h):
    sc=server.serve(replace(h.sidecar_cfg,listen_port=0))
    threading.Thread(target=sc.serve_forever,kwargs={"poll_interval":.01},daemon=True).start()
    return sc,"http://127.0.0.1:"+str(sc.server_address[1])


def assert_one(h,identity):
    assert len(h.state.tasks)==len(h.state.sessions)==len(h.adapter.transport.dispatched)==1
    assert next(iter(h.state.tasks))==identity[0]
    assert next(iter(h.state.sessions))==identity[1]


@pytest.mark.parametrize("case",["partial-error","wrong-task","wrong-session","wrong-both",
                                  "missing-summary","missing-created_at","missing-artifact_links","invalid-error_class"])
def test_browser_retry_then_send_stays_locked_across_all_recovery_statuses(case,monkeypatch):
    sync = pytest.importorskip("playwright.sync_api")
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE":"silent"}) as h:
        mode=install(h,monkeypatch,True)
        sc,base=start(h)
        try:
            with sync.sync_playwright() as p:
                browser=launch_browser(p)
                page=browser.new_page();page.set_default_timeout(7000)
                page.route("**/*",lambda r:r.continue_() if r.request.url.startswith(base+"/") else r.abort())
                page.goto(base);page.locator("#pairBtn").click()
                page.wait_for_function("loaded && pairingState==='paired' && !pairingBusy")
                page.locator("#goal").fill("Original browser-owned task")
                page.locator("#send").click()
                page.wait_for_function("(localFailure || lastSnapshot.phase==='error') && !busy")
                first=page.evaluate("[lastSnapshot.task_id,lastSnapshot.session_id]")
                for status in STATUSES:
                    mode["reply"]=(status,case)
                    page.evaluate("actions.Retry()")
                    assert page.locator("#send").is_disabled()
                    assert page.evaluate("pending() && lastSnapshot.task_in_flight")
                    assert page.evaluate("[lastSnapshot.task_id,lastSnapshot.session_id]")==first
                    page.evaluate("sendTask();document.getElementById('send').click()")
                    assert_one(h,first)
                # Explicit End is not cancellation proof and must not unlock another Send.
                page.evaluate("actions['End task']()")
                assert page.locator("#send").is_disabled()
                assert page.locator("#errPrimary").text_content()=="Retry"
                page.evaluate("actions.Retry()")
                assert_one(h,first)
                # Reload must recover authoritative ownership, not fabricate a fresh Pick.
                page.reload();page.wait_for_function("loaded && pairingState==='paired'")
                assert page.locator("#send").is_disabled()
                assert page.evaluate("[lastSnapshot.task_id,lastSnapshot.session_id]")==first
                assert_one(h,first)
                browser.close()
        finally:
            sc.shutdown();sc.server_close()


def test_lost_local_run_response_cannot_enable_another_browser_send(monkeypatch):
    sync = pytest.importorskip("playwright.sync_api")
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE":"silent"}) as h:
        mode=install(h,monkeypatch,False)
        sc,base=start(h)
        try:
            with sync.sync_playwright() as p:
                browser=launch_browser(p)
                page=browser.new_page();page.set_default_timeout(7000)
                page.route("**/*",lambda r:r.continue_() if r.request.url.startswith(base+"/") else r.abort())
                def lose_response(route):
                    route.fetch()  # actual sidecar accepts and dispatches exactly once
                    route.abort()  # browser never learns the accepted IDs
                page.route("**/_sidecar/ux/run",lose_response,times=1)
                page.goto(base);page.locator("#pairBtn").click()
                page.wait_for_function("loaded && pairingState==='paired' && !pairingBusy")
                page.locator("#goal").fill("Original local lost-response task")
                page.locator("#send").click()
                page.wait_for_function("(localFailure || lastSnapshot.phase==='error') && !busy")
                identity=next(iter(h.state.tasks)),next(iter(h.state.sessions))
                assert page.locator("#send").is_disabled(), "local lost ACK enabled another Send"
                page.evaluate("sendTask()")
                assert_one(h,identity)
                mode["reply"]=(401,"partial-error")
                page.evaluate("actions.Retry()")
                assert page.locator("#send").is_disabled()
                assert page.evaluate("[lastSnapshot.task_id,lastSnapshot.session_id]")==list(identity)
                assert_one(h,identity)
                browser.close()
        finally:
            sc.shutdown();sc.server_close()
