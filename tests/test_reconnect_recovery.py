"""An accepted task survives bridge or local poll transport interruptions."""
import threading
from dataclasses import replace

import pytest
from benchlink_sidecar import server
from benchlink_sidecar.client import BridgeClient
from benchlink_sidecar.config import load_config
from benchlink_sidecar.ux import BridgeControlShell, Phase
from test_retry_recovery import post, capture_review_state
from recovery_fixtures import wire_result


@pytest.fixture
def accepted_sidecar(monkeypatch):
    cfg=load_config({'BENCHLINK_BRIDGE_BASE_URL':'http://127.0.0.1:9','BENCHLINK_DEVICE_ID':'mac-rereview-fixture','BENCHLINK_ACCESS_TOKEN':'fixture_memory_only'})
    mode={'online':True,'break_poll':True,'finish':False,'reads':0}
    calls=[];task_ids=[]
    def send(self,req):
        calls.append(req.operation_id)
        if req.operation_id=='health':
            if not mode['online']: raise OSError('loopback fixture outage')
            return 200,{'type':'health.pong'}
        if req.operation_id=='sessionCreate': return 200,{'type':'session.created','session_id':'sess_accepted'}
        if req.operation_id=='taskCreate':
            return 200,wire_result(req.body['task_id'],req.body['session_id'],'accepted')
        if req.operation_id=='taskGet':
            if mode.get('poll_delay'):
                import time
                time.sleep(mode['poll_delay'])
            task_ids.append(req.url.rsplit('/',1)[-1]);mode['reads']+=1
            if mode['break_poll']:
                mode['break_poll']=False;mode['online']=False
                raise OSError('loopback fixture interruption after acceptance')
            if not mode['online']: raise OSError('still offline')
            return 200,wire_result(shell.task_id,shell.session_id,'succeeded' if mode['finish'] else 'running',
                                   summary='Same accepted task recovered')
        return 200,{}
    monkeypatch.setattr(BridgeClient,'send',send)
    shell=BridgeControlShell(cfg)
    monkeypatch.setattr(server,'BridgeControlShell',lambda *a,**k:shell)
    sc=server.serve(replace(cfg,listen_port=0))
    threading.Thread(target=sc.serve_forever,kwargs={'poll_interval':0.05},daemon=True).start()
    try:yield shell,f'http://127.0.0.1:{sc.server_address[1]}',mode,calls,task_ids
    finally:sc.shutdown();sc.server_close()


def accept_and_interrupt(base):
    status,j=post(base,'/_sidecar/ux/run',{'target':'ethan','goal':'Resume the original accepted task','max_ticks':1})
    assert status==200 and j['snapshot']['phase']=='error'
    return j['snapshot']


def test_poll_resumes_accepted_offline_task_without_new_create(accepted_sidecar):
    shell,base,mode,calls,ids=accepted_sidecar
    first=accept_and_interrupt(base);task_id=first['task_id'];session_id=first['session_id']
    assert first['task_accepted'] is True
    mode['online']=True
    status,running=post(base,'/_sidecar/ux/poll')
    assert status==200 and running['phase']=='wait'
    mode['finish']=True
    status,result=post(base,'/_sidecar/ux/poll')
    assert result['phase']=='done'
    assert (result['task_id'],result['session_id'])==(task_id,session_id)
    assert set(ids)=={task_id}
    assert calls.count('taskCreate')==calls.count('sessionCreate')==1
    assert calls.count('sessionClose')==0


def test_interrupted_accepted_task_blocks_new_send_and_retry_never_resets_it(accepted_sidecar):
    shell,base,mode,calls,ids=accepted_sidecar
    first=accept_and_interrupt(base)
    assert post(base,'/_sidecar/ux/run',{'goal':'must not duplicate'})[0]==409
    # Even a forced terminal Retry cannot discard an accepted interrupted task.
    _,retry=post(base,'/_sidecar/ux/retry')
    assert retry['task_id']==first['task_id']
    assert calls.count('taskCreate')==1 and calls.count('sessionClose')==0


@pytest.mark.parametrize('local_poll_failure',[False,True])
def test_reconnect_retry_actual_dom_resumes_same_id(accepted_sidecar,local_poll_failure):
    sync=pytest.importorskip('playwright.sync_api')
    shell,base,mode,calls,ids=accepted_sidecar
    if local_poll_failure:mode['break_poll']=False
    with sync.sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1280,'height':800});page.set_default_timeout(7000)
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(base+'/') else r.abort())
        if local_poll_failure:page.route('**/_sidecar/ux/poll',lambda r:r.abort(),times=1)
        page.goto(base)
        page.locator('#goal').fill('Resume the original accepted task')
        page.wait_for_function('!document.getElementById("send").disabled')
        page.locator('#send').click()
        page.wait_for_function('lastSnapshot.error && lastSnapshot.error.code==="offline" && !sending')
        original=shell.task_id;session=shell.session_id
        assert page.evaluate('lastSnapshot.task_id')==original
        assert page.locator('#send').is_disabled()
        capture_review_state(page,'W-interrupted-local' if local_poll_failure else 'W-interrupted')
        mode['online']=True;mode['finish']=True
        page.evaluate('document.getElementById("errPrimary").click();document.getElementById("errPrimary").click()')
        page.wait_for_function('lastSnapshot.phase==="done" && !polling && !recovering')
        assert page.evaluate('lastSnapshot.task_id')==original
        assert page.evaluate('lastSnapshot.session_id')==session
        assert 'Same accepted task' in page.locator('#resultSummary').inner_text()
        assert calls.count('taskCreate')==calls.count('sessionCreate')==1
        assert calls.count('sessionClose')==0 and set(ids)=={original}
        capture_review_state(page,'W-reconnected-local' if local_poll_failure else 'W-reconnected')
        assert not errors
        browser.close()


def test_health_refresh_alone_resumes_interrupted_task(accepted_sidecar):
    sync=pytest.importorskip('playwright.sync_api')
    shell,base,mode,calls,ids=accepted_sidecar
    with sync.sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
        page=browser.new_page();page.set_default_timeout(5000)
        page.goto(base);page.locator('#goal').fill('Resume without another Send')
        page.wait_for_function('!document.getElementById("send").disabled');page.locator('#send').click()
        page.wait_for_function('lastSnapshot.error && lastSnapshot.error.code==="offline" && !sending')
        original=shell.task_id;mode['online']=True;mode['finish']=True
        page.evaluate('refreshStatus()');page.wait_for_function('lastSnapshot.phase==="done"')
        assert shell.task_id==original and calls.count('taskCreate')==1
        browser.close()


def test_overlapping_health_refreshes_issue_only_one_resume_poll(accepted_sidecar):
    sync=pytest.importorskip('playwright.sync_api')
    shell,base,mode,calls,ids=accepted_sidecar
    interrupted=accept_and_interrupt(base)
    with sync.sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
        page=browser.new_page();page.set_default_timeout(5000)
        page.goto(base);page.wait_for_function('taskStateLoaded');page.evaluate('(snap)=>show(snap)',interrupted)
        mode['online']=True;mode['finish']=True;mode['poll_delay']=0.3
        before=mode['reads'];poll_requests=[]
        page.on('request',lambda r:poll_requests.append(r.url) if r.url.endswith('/_sidecar/ux/poll') else None)
        page.evaluate('Promise.all([refreshStatus(),refreshStatus()])')
        assert page.evaluate('lastSnapshot.phase')=='done'
        assert mode['reads']==before+1
        assert len(poll_requests)==1
        assert calls.count('taskCreate')==1
        browser.close()


def test_terminal_offline_result_is_not_mistaken_for_transport_interruption(accepted_sidecar):
    shell,base,mode,calls,ids=accepted_sidecar
    snap=accept_and_interrupt(base)
    shell.result=wire_result(shell.task_id,shell.session_id,'failed',error_class='offline')
    mode['online']=True
    status,ready=post(base,'/_sidecar/ux/retry')
    assert status==200 and ready['phase']=='pick'
    assert ready['task_id'] is None and ready['task_accepted'] is False
    assert calls.count('taskCreate')==1 and calls.count('sessionClose')==1
