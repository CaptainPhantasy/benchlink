"""Terminal Retry is an explicit reset, never an implicit task submission."""
import json
import threading
import urllib.error
import urllib.request
from dataclasses import replace

import pytest
from benchlink_sidecar import server
from benchlink_sidecar.client import BridgeClient
from benchlink_sidecar.config import load_config
from benchlink_sidecar.ux import BridgeControlShell, Phase, WaitStatus
from recovery_fixtures import wire_result


def post(base, path, body=None):
    req=urllib.request.Request(base+path,data=json.dumps(body or {}).encode(),headers={'Content-Type':'application/json'},method='POST')
    try:
        with urllib.request.urlopen(req,timeout=4) as r: return r.status,json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code,json.loads(e.read())


@pytest.fixture
def terminal_sidecar(monkeypatch):
    cfg=load_config({'BENCHLINK_BRIDGE_BASE_URL':'http://127.0.0.1:9','BENCHLINK_DEVICE_ID':'mac-rereview-fixture','BENCHLINK_ACCESS_TOKEN':'fixture_memory_only'})
    calls=[]
    def send(self, req):
        calls.append(req.operation_id)
        if req.operation_id=='health': return 200,{'type':'health.pong'}
        if req.operation_id=='sessionCreate': return 200,{'type':'session.created','session_id':'sess_new'}
        if req.operation_id=='taskCreate': return 200,wire_result(req.body['task_id'],req.body['session_id'],'accepted')
        if req.operation_id=='taskGet': return 200,wire_result(shell.task_id,shell.session_id,'succeeded',summary='Recovered after an explicit Send')
        return 200,{}
    monkeypatch.setattr(BridgeClient,'send',send)
    shell=BridgeControlShell(cfg)
    shell.phase=Phase.ERROR; shell.wait_status=WaitStatus.ERROR
    shell.goal='Keep this exact task text'; shell.target_key='ethan'; shell.high_priority=True
    shell.session_id='sess_terminal'; shell.task_id='task_terminal'
    shell.result=wire_result(shell.task_id,shell.session_id,'failed',error_class='internal')
    shell.error={'code':'internal','message':'fixture terminal error'}
    monkeypatch.setattr(server,'BridgeControlShell',lambda *a,**k:shell)
    sc=server.serve(replace(cfg,listen_port=0))
    threading.Thread(target=sc.serve_forever,kwargs={'poll_interval':0.05},daemon=True).start()
    try: yield shell,f'http://127.0.0.1:{sc.server_address[1]}',calls
    finally: sc.shutdown();sc.server_close()


@pytest.mark.parametrize("terminal_code", ["internal", "offline"])
def test_terminal_retry_clears_error_preserves_draft_without_dispatch(terminal_sidecar, terminal_code):
    shell,base,calls=terminal_sidecar
    shell.result["error_class"] = terminal_code
    shell.error["code"] = terminal_code
    status,snap=post(base,'/_sidecar/ux/retry')
    assert status==200
    assert snap['phase']=='pick' and snap['wait_status']=='idle'
    assert (snap['target'],snap['goal'],snap['high_priority'])==('ethan','Keep this exact task text',True)
    assert snap['task_id'] is None and snap['session_id'] is None and snap['error'] is None
    assert calls.count('sessionClose')==1
    assert calls.count('taskCreate')==0
    assert post(base,'/_sidecar/ux/retry')[0]==200
    assert calls.count('sessionClose')==1
    assert calls.count('taskCreate')==0


def test_retry_refuses_inflight_without_closing_or_recreating(terminal_sidecar):
    shell,base,calls=terminal_sidecar
    shell.phase=Phase.WAIT;shell.wait_status=WaitStatus.RUNNING
    status,body=post(base,'/_sidecar/ux/retry')
    assert status==409
    assert shell.task_id=='task_terminal' and shell.phase==Phase.WAIT
    assert calls==[]


@pytest.mark.parametrize("terminal_code", ["internal", "offline"])
def test_retry_button_actual_dom_recovers_once_then_explicit_send(terminal_sidecar, terminal_code):
    sync=pytest.importorskip('playwright.sync_api')
    shell,base,calls=terminal_sidecar
    shell.result["error_class"] = terminal_code
    shell.error["code"] = terminal_code
    with sync.sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1280,'height':800}); page.set_default_timeout(5000); errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        requests=[]
        page.on('request',lambda r:requests.append(r.url))
        page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(base+'/') else r.abort())
        page.goto(base)
        page.locator('#goal').fill(shell.goal)
        page.evaluate('(snap)=>{target=snap.target;lastSent={target:snap.target,goal:snap.goal,high_priority:snap.high_priority};show(snap)}',shell.snapshot())
        page.wait_for_function('document.getElementById("errPrimary").textContent==="Retry"')
        page.evaluate('document.getElementById("errPrimary").click(); document.getElementById("errPrimary").click()')
        page.wait_for_function('lastSnapshot.phase==="pick" && !recovering')
        assert page.locator('#goal').input_value()=='Keep this exact task text'
        assert page.evaluate('target')=='ethan'
        assert page.locator('#hp').is_checked()
        assert page.locator('#errorCard').is_hidden()
        assert calls.count('sessionClose')==1 and calls.count('taskCreate')==0
        assert calls.count('sessionCreate')==0
        assert shell.phase==Phase.PICK and shell.task_id is None and shell.session_id is None
        assert sum(url.endswith('/_sidecar/ux/retry') for url in requests)==1
        capture_review_state(page,'W-retry-ready')
        page.wait_for_function('!document.getElementById("send").disabled')
        page.locator('#send').click()
        page.wait_for_function('lastSnapshot.phase==="done"')
        assert calls.count('taskCreate')==1
        assert 'Recovered' in page.locator('#resultSummary').inner_text()
        assert not errors
        browser.close()


def capture_review_state(page,name):
    """Optional screenshot refresh when explicitly requested by the evidence run."""
    import os
    from pathlib import Path
    gate=os.environ.get('BENCHLINK_REVIEW_SCREENSHOTS')
    if gate not in {'01','02','03'}: return
    import sys
    root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root/'tools/ui'))
    from capture_i1 import vectorize
    png=Path('/workspace/benchlink-i1-rereview/png')/gate/(name+'.png')
    png.parent.mkdir(parents=True,exist_ok=True)
    svg=root/f'docs/screenshots/beta-{gate}'/(name+'.svg')
    page.screenshot(path=str(png));vectorize(png,svg)


def test_retry_refuses_while_dispatch_lock_held(terminal_sidecar,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    shell,base,calls=terminal_sidecar
    entered,release=threading.Event(),threading.Event()
    def blocked(**kw):
        entered.set();release.wait(3)
        return {'snapshot':shell.snapshot()}
    monkeypatch.setattr(shell,'start_and_wait',blocked)
    shell.phase=Phase.PICK
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            first=pool.submit(post,base,'/_sidecar/ux/run',{'goal':'pending'})
            assert entered.wait(1)
            assert post(base,'/_sidecar/ux/retry')[0]==409
            assert calls==[]
            release.set();assert first.result()[0]==200
    finally: release.set()


def test_stale_error_retry_409_keeps_send_locked(terminal_sidecar):
    sync=pytest.importorskip('playwright.sync_api')
    shell,base,calls=terminal_sidecar
    stale=shell.snapshot()
    shell.phase=Phase.WAIT;shell.wait_status=WaitStatus.RUNNING
    with sync.sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
        page=browser.new_page();page.set_default_timeout(5000)
        page.goto(base);page.wait_for_function('taskStateLoaded')
        # Deliberately restore the captured stale client view after loading live state.
        page.evaluate('(snap)=>show(snap)',stale)
        page.locator('#goal').fill(shell.goal)
        page.locator('#errPrimary').click()
        page.wait_for_function('!recovering && document.getElementById("errTitle").textContent==="Task in progress"')
        assert page.locator('#send').is_disabled()
        page.evaluate('document.getElementById("send").click()')
        assert calls.count('taskCreate')==0 and calls.count('sessionClose')==0
        assert shell.phase==Phase.WAIT and shell.task_id=='task_terminal'
        browser.close()


@pytest.mark.parametrize("result_status", [None, "accepted", "running"])
def test_offline_retry_never_resets_uncertain_nonterminal_task(terminal_sidecar, result_status):
    """Only a known failed result may take the terminal-offline reset path."""
    sync = pytest.importorskip("playwright.sync_api")
    shell, base, calls = terminal_sidecar
    shell.error["code"] = "offline"
    shell.task_accepted = True
    shell.result = {"status": result_status} if result_status else None
    with sync.sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        page.set_default_timeout(5000)
        page.route("**/*", lambda r: r.continue_() if r.request.url.startswith(base + "/") else r.abort())
        page.goto(base)
        page.wait_for_function("taskStateLoaded")
        page.evaluate("snap => show(snap)", shell.snapshot())
        # Await the complete action. Beta 03 resumes this accepted task; lower
        # branches only health-check. Neither is allowed to reset or replace it.
        page.evaluate("actions.Retry()")
        resumes = hasattr(shell, "can_resume_interrupted_wait")
        assert page.evaluate("lastSnapshot.phase") == ("done" if resumes else "error")
        assert page.evaluate("lastSnapshot.task_id") == "task_terminal"
        assert shell.phase == (Phase.DONE if resumes else Phase.ERROR)
        assert shell.session_id == "sess_terminal"
        assert calls.count("sessionClose") == calls.count("taskCreate") == 0
        assert calls.count("taskGet") == (1 if resumes else 0)
        assert calls.count("health") >= 1
        browser.close()
