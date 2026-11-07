"""Chromium exercises only ephemeral loopback with synthetic silent bot work."""
import pytest
sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
from test_retry_recovery import launch_browser
from benchlink_testkit import Harness
from test_pr1_recovery import local_sidecar, result_for
from test_pr1_larry_replay import install_lost_ack

@pytest.mark.parametrize('scenario',['wrong-ids','malformed-401'])
def test_browser_retry_never_unlocks_original_until_matching_terminal(scenario,monkeypatch):
    with Harness(adapter_env={'BENCHLINK_FAKE_TRANSPORT_MODE':'silent'}) as h:
        mode={'terminal':False}
        def recovery(payload):
            if mode['terminal']: return 200,payload
            if scenario=='malformed-401': return 401,{'type':'error'}
            return 200,dict(payload,status='succeeded',task_id='task_other',session_id='sess_other')
        install_lost_ack(h,monkeypatch,recovery)
        with local_sidecar(h) as (call,_,base),sync_playwright() as p:
            browser=launch_browser(p)
            page=browser.new_page();page.set_default_timeout(7000)
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(base+'/') else r.abort())
            page.goto(base);page.locator('#goal').fill('one original browser task');page.locator('#send').click()
            page.wait_for_function("lastSnapshot.phase==='error' && !busy")
            assert page.locator('#send').is_disabled()
            original=call('/_sidecar/ux',method='GET')[1]
            page.locator('#errPrimary').click();page.wait_for_function("!busy && lastSnapshot.phase==='error'")
            assert page.locator('#send').is_disabled()
            assert call('/_sidecar/ux/run',{'goal':'forbidden duplicate'})[0]==409
            assert call('/_sidecar/ux/end')[0]==409
            assert len(h.state.tasks)==len(h.state.sessions)==len(h.adapter.transport.dispatched)==1
            assert page.evaluate('[lastSnapshot.task_id,lastSnapshot.session_id]')==[original['task_id'],original['session_id']]
            h.adapter.source.simulate_bot_write(original['task_id'],result_for(h));mode['terminal']=True
            page.locator('#errPrimary').click();page.wait_for_function("!busy && lastSnapshot.phase==='done'")
            assert page.locator('#send').is_enabled()
            assert len(h.state.tasks)==len(h.state.sessions)==len(h.adapter.transport.dispatched)==1
            browser.close()

def test_browser_reload_and_lost_local_ack_keep_send_guarded():
    with Harness(adapter_env={'BENCHLINK_FAKE_TRANSPORT_MODE':'silent'}) as h,local_sidecar(h) as (call,_,base),sync_playwright() as p:
        browser=launch_browser(p)
        page=browser.new_page();page.set_default_timeout(7000)
        def route(r):
            if not r.request.url.startswith(base+'/'): return r.abort()
            if r.request.url.endswith('/_sidecar/ux/run'):
                response=r.fetch();assert response.status==200
                return r.abort() # the local run took effect; its browser reply was lost
            return r.continue_()
        page.route('**/*',route);page.goto(base)
        page.locator('#goal').fill('lost browser acknowledgement');page.locator('#send').click()
        page.wait_for_function("!busy && localFailure")
        assert page.locator('#send').is_disabled() and page.locator('#errPrimary').is_visible()
        page.locator('#errPrimary').click();page.wait_for_function("!busy && lastSnapshot.phase==='wait'")
        identity=page.evaluate('[lastSnapshot.task_id,lastSnapshot.session_id]')
        page.reload();page.wait_for_function('loaded')
        assert page.locator('#send').is_disabled()
        assert page.evaluate('[lastSnapshot.task_id,lastSnapshot.session_id]')==identity
        assert call('/_sidecar/ux/run',{'goal':'forbidden reload duplicate'})[0]==409
        assert len(h.state.tasks)==len(h.state.sessions)==len(h.adapter.transport.dispatched)==1
        browser.close()

def test_browser_terminal_retry_prepares_draft_without_dispatch():
    with Harness(adapter_env={'BENCHLINK_FAKE_TRANSPORT_MODE':'silent'}) as h,local_sidecar(h) as (_,_,base),sync_playwright() as p:
        browser=launch_browser(p)
        page=browser.new_page();page.set_default_timeout(7000)
        page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(base+'/') else r.abort())
        page.goto(base);page.locator('#goal').fill('keep this draft');page.locator('#send').click()
        page.wait_for_function("!busy && lastSnapshot.phase==='wait'")
        identity=page.evaluate('lastSnapshot.task_id')
        h.adapter.source.simulate_bot_write(identity,result_for(h,'failed','internal'))
        page.wait_for_function("!busy && lastSnapshot.phase==='error'")
        page.locator('#errPrimary').click();page.wait_for_function("!busy && lastSnapshot.phase==='pick'")
        assert page.locator('#goal').input_value()=='keep this draft'
        assert page.locator('#send').is_enabled()
        assert len(h.state.tasks)==len(h.state.sessions)==len(h.adapter.transport.dispatched)==1
        page.locator('#send').click();page.wait_for_function("!busy && lastSnapshot.phase==='wait'")
        assert len(h.state.tasks)==len(h.state.sessions)==len(h.adapter.transport.dispatched)==2
        browser.close()
