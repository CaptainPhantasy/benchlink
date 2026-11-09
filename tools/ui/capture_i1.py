"""Loopback-only I1 DOM checks and screenshot receipts.

Optional evidence tools: pip install playwright pillow; playwright install chromium.
Run with the repo venv: python tools/ui/capture_i1.py 01 (or 02 / 03).
Use --output-dir /tmp/benchlink-capture to validate without changing tracked images.
SVGs are faithful vectorizations of Chromium screenshots so text-only push_files
can transport them without corrupting binary PNGs. No external requests allowed.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/smoke'))
import benchlink_testkit  # noqa: E402,F401
from benchlink_sidecar.config import load_config  # noqa: E402
from benchlink_sidecar.server import serve  # noqa: E402
from benchlink_sidecar.ui.copy import error_card, result_card, status_token  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from PIL import Image  # noqa: E402


def vectorize(png, svg):
    """Encode screenshot pixels as grouped SVG paths (64-color palette)."""
    im = Image.open(png).convert('RGB').quantize(colors=64)
    palette = im.getpalette()
    paths = {}
    for y in range(im.height):
        x = 0
        while x < im.width:
            color = im.getpixel((x,y)); end=x+1
            while end < im.width and im.getpixel((end,y)) == color:
                end += 1
            paths.setdefault(color, []).append(f'M{x} {y}h{end-x}v1h-{end-x}z')
            x=end
    body=''.join(f'<path fill="#{palette[c*3]:02x}{palette[c*3+1]:02x}{palette[c*3+2]:02x}" d="{"".join(data)}"/>' for c,data in paths.items())
    svg.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="{im.width}" height="{im.height}" viewBox="0 0 {im.width} {im.height}" role="img"><title>{svg.stem}: Chromium screenshot, local fixture</title>{body}</svg>')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('gate', choices=('01', '02', '03'))
    parser.add_argument('--output-dir', type=Path, help='Write SVG and PNG evidence here instead of checked-in screenshots')
    args = parser.parse_args()
    gate = args.gate
    out = args.output_dir or ROOT / f'docs/screenshots/beta-{gate}'
    out.mkdir(parents=True, exist_ok=True)
    pngs = out / 'png' if args.output_dir else Path('/workspace/benchlink-i1/png') / gate
    pngs.mkdir(parents=True, exist_ok=True)
    cfg = load_config({'BENCHLINK_BRIDGE_BASE_URL':'http://127.0.0.1:9', 'BENCHLINK_DEVICE_ID':'mac-i1-fixture'})
    server=serve(replace(cfg,listen_port=0))
    threading.Thread(target=server.serve_forever,kwargs={'poll_interval':0.05},daemon=True).start()
    base=f'http://127.0.0.1:{server.server_address[1]}'
    state={'connection':{'key':'connected','label':'Connected','tone':'success'}, 'pairing':{'state':'paired','label':'Paired','device_id':'mac-i1-fixture'}, 'config':{'bridge_host':'127.0.0.1','transport':'Tailscale HTTPS'}, 'upstream_configured':True}
    calls=[]; mode={'run':'error','pair':'success','poll':'wait'}
    def snapshot(**changes):
        value = {
            'phase':'pick', 'wait_status':'idle', 'target':'floyd', 'goal':'',
            'high_priority':False, 'session_id':None, 'task_id':None,
            'task_accepted':False, 'submission_uncertain':False,
            'can_resume':False, 'task_in_flight':False, 'result':None, 'error':None,
        }
        value.update(changes)
        value.setdefault('presentation', {'status':status_token(value['wait_status'])})
        return value
    live = {'snapshot':snapshot()}
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
        context=browser.new_context(viewport={'width':1280,'height':800}, permissions=['clipboard-read','clipboard-write'])
        page=context.new_page(); errors=[]; page.on('pageerror',lambda e:errors.append(str(e)))
        def route(r):
            u=urlparse(r.request.url)
            assert u.hostname == '127.0.0.1', 'External egress refused'
            path=u.path
            if path == '/': return r.continue_()
            calls.append((path,r.request.post_data))
            if path=='/_sidecar/status': return r.fulfill(json=state)
            if path=='/_sidecar/ux': return r.fulfill(json={'phase':'pick','wait_status':'idle','task_in_flight':False})
            if path=='/_sidecar/pair':
                if mode['pair']=='success':
                    state['pairing']={'state':'paired','label':'Paired','device_id':'mac-i1-fixture'}
                    return r.fulfill(json={'state':'paired','device_id':'mac-i1-fixture'})
                state['pairing']={'state':'not_allowed','label':"This Mac isn't allowed",'device_id':'mac-i1-fixture','error_code':'auth.denied'}
                if mode['pair']=='expirefail': state['pairing']={'state':'expired','label':'Pairing expired','device_id':'mac-i1-fixture','error_code':'auth.expired'}
                return r.fulfill(status=403,json={'code':'auth.expired' if mode['pair']=='expirefail' else 'auth.denied','message':'RAW_PRIVATE_PATH', 'presentation':{'error':error_card('auth.expired' if mode['pair']=='expirefail' else 'auth.denied',device_id='mac-i1-fixture')}})
            if path=='/_sidecar/ux': return r.fulfill(json=live['snapshot'])
            if path=='/_sidecar/ux/run':
                if mode['run']=='wait':
                    body=json.loads(r.request.post_data or '{}')
                    live['snapshot']=snapshot(phase='wait', wait_status='running',
                        target=body.get('target','floyd'), goal=body.get('goal',''),
                        high_priority=bool(body.get('high_priority')), session_id='sess_fixture',
                        task_id='task_fixture', task_accepted=True, task_in_flight=True)
                    return r.fulfill(json={'snapshot':live['snapshot']})
                return r.fulfill(status=422,json={'code':'validation','message':'RAW_SERVER_ERROR'})
            if path=='/_sidecar/ux/end':
                if live['snapshot']['task_in_flight']:
                    return r.fulfill(status=409,json={'code':'validation',
                        'presentation':{'error':error_card('task_in_flight')}})
                live['snapshot']=snapshot(target=live['snapshot']['target'])
                return r.fulfill(json=live['snapshot'])
            if path=='/_sidecar/ux/poll':
                if mode['poll']=='done' and live['snapshot']['task_in_flight']:
                    current=live['snapshot']
                    result={'v':'1','type':'task.result','status':'succeeded',
                        'session_id':current['session_id'],'task_id':current['task_id'],
                        'summary':'Staff task completed. The local fixture returned this report.',
                        'created_at':'2026-11-05T18:05:00Z','agent_id':current['target'],
                        'artifact_links':[],'error_class':None}
                    live['snapshot']=snapshot(**{**current, 'phase':'done', 'wait_status':'result',
                        'task_in_flight':False, 'result':result,
                        'presentation':{'status':status_token('result'),
                            'result':result_card(result,target_name=current['target'])}})
                return r.fulfill(json=live['snapshot'])
            return r.fulfill(json={})
        page.route('**/*',route)
        def shot(name):
            page.screenshot(path=str(pngs/(name+'.png')))
            vectorize(pngs/(name+'.png'),out/(name+'.svg'))
        def show_error(code):
            card=error_card(code,target_name='Ethan',device_id='mac-i1-fixture',correlation_id='fixture_corr',http=502)
            page.evaluate('(snap)=>show(snap)',snapshot(phase='error',wait_status='error',target='ethan',
                error={'code':code},presentation={'status':status_token('error'),'error':card}))
        page.goto(base)
        page.wait_for_function('loaded && !busy')
        page.locator('#goal').fill('Confirm the staff task result.')
        if gate=='01':
            page.locator('#send').click(); page.wait_for_function('document.getElementById("errorCard").style.display === "block"')
            assert 'RAW_SERVER_ERROR' not in page.locator('body').inner_text()
            assert page.locator('#errTitle').evaluate('(e)=>document.activeElement===e')
            assert page.locator('#errPrimary').inner_text()=='Retry'
            page.locator('#errPrimary').click()
            page.wait_for_function('!busy && !localFailure && !pending() && lastSnapshot.phase==="pick"')
            assert page.locator('#errorCard').is_hidden()
            for code,name in [('offline','W-offline'),('timeout','W-timeout'),('routing','W-routing'),('internal','W-internal'),('auth.denied','W-denied'),('auth.expired','W-expired')]:
                show_error(code); shot(name)
            result={'v':'1','type':'task.result','status':'succeeded','agent_id':'floyd',
                'summary':'Staff task completed. The local fixture returned this report.',
                'session_id':'sess_fixture','task_id':'task_fixture','created_at':'2026-11-05T18:05:00Z',
                'artifact_links':[{'title':'Staff report','url':'https://example.test/staff/report'}],
                'error_class':None}
            page.evaluate('(snap)=>show(snap)',snapshot(phase='done',wait_status='result',
                session_id='sess_fixture',task_id='task_fixture',task_accepted=True,result=result,
                presentation={'status':status_token('result'),'result':result_card(result,target_name='Floyd')}))
            page.locator('#resultDetails').evaluate('(e)=>e.open=true'); shot('W-result')
            assert page.locator('#resultSession').inner_text()=='sess_fixture'
        elif gate=='02':
            page.locator('#goal').fill(''); shot('P-default'); shot('C-empty')
            first=page.locator('[role=radio]').first; first.focus(); page.keyboard.press('ArrowDown')
            assert page.locator('[role=radio][aria-checked=true]').inner_text().strip().startswith('Ethan')
            assert page.locator('[role=radio][tabindex="0"]').count()==1
            shot('P-selected'); mode['run']='wait'; page.locator('#goal').fill('Keep this task in flight')
            page.evaluate("""() => {
                const originalPost=post;
                window.releaseCaptureRun=null;
                post=async (path,body) => {
                    if (path.endsWith('/run')) await new Promise(resolve => { window.releaseCaptureRun=resolve; });
                    return originalPost(path,body);
                };
                document.getElementById('send').click(); document.getElementById('send').click();
            }""")
            page.wait_for_function('busy && window.releaseCaptureRun !== null')
            assert page.locator('#send').is_disabled(); shot('C-sending')
            page.evaluate('window.releaseCaptureRun()')
            page.wait_for_function('!busy && pending() && lastSnapshot.phase==="wait"')
            assert page.locator('#goal').get_attribute('readonly') is not None
            assert len([c for c in calls if c[0]=='/_sidecar/ux/run'])==1
            assert page.locator('#end').is_disabled()
            assert page.locator('#roster button:disabled').count()==page.locator('#roster button').count()
            shot('P-locked'); shot('C-inflight')
            page.evaluate('document.getElementById("end").click()')
            assert not [c for c in calls if c[0]=='/_sidecar/ux/end']
            mode['poll']='done'; page.evaluate('retryPoll()')
            page.wait_for_function('!busy && !pending() && lastSnapshot.phase==="done"')
            assert page.locator('#end').is_enabled()
            page.locator('#end').click(); page.wait_for_function('!busy && lastSnapshot.phase==="pick"')
            assert len([c for c in calls if c[0]=='/_sidecar/ux/end'])==1
        else:
            # Actual status -> Pair -> Send-enabled flow, without another keystroke.
            state['pairing']={'state':'not_paired','label':'Not paired','device_id':'mac-i1-fixture'}
            page.evaluate('refreshStatus()'); assert page.locator('#send').is_disabled(); shot('S1-empty')
            page.locator('#pairBtn').click(); page.wait_for_function('pairingState==="paired" && !pairingBusy')
            assert page.locator('#send').is_enabled(); assert page.locator('#setup').is_hidden(); shot('S1-paired')
            state['pairing']['expires_at']=page.evaluate('new Date(Date.now()+90000).toISOString()')
            page.evaluate('refreshStatus()')
            assert 'warning-text' in page.locator('#s4PairingLabel').get_attribute('class')
            page.locator('#connPill').click(); shot('S4-paired'); page.locator('#s4Close').click()
            # Denial from a real Pair click: no raw message reaches the DOM.
            state['pairing']={'state':'not_paired','label':'Not paired','device_id':'mac-i1-fixture'}
            mode['pair']='denied'; page.evaluate('refreshStatus()'); page.locator('#pairBtn').click()
            page.wait_for_function('pairingState==="not_allowed" && !pairingBusy')
            assert page.locator('#send').is_disabled()
            assert 'RAW_PRIVATE_PATH' not in page.locator('body').inner_text(); shot('S1-denied')
            # Expiry performs one automatic Re-pair only; a failure disables Send.
            state['pairing']={'state':'expired','label':'Pairing expired','device_id':'mac-i1-fixture'}
            mode['pair']='expirefail'; before=len([c for c in calls if c[0]=='/_sidecar/pair'])
            page.evaluate('autoRepairAttempted=false; refreshStatus()')
            page.evaluate('refreshStatus()')
            assert len([c for c in calls if c[0]=='/_sidecar/pair'])==before+1
            assert page.locator('#send').is_disabled()
            assert page.locator('#sendHint').inner_text()=='Pairing expired. Re-pair to send.'
            shot('S1-expired')
            # Successful automatic renewal also enables Send without editing the goal.
            mode['pair']='success'; page.evaluate('autoRepairAttempted=false; refreshStatus()')
            assert page.locator('#send').is_enabled()
            for st,name in [('failed','S1-pairfail'),('not_paired','S1-partial')]:
                state['pairing']={'state':st,'label':{'failed':'Pairing failed','expired':'Pairing expired','not_paired':'Not paired'}[st],'device_id':'mac-i1-fixture','error_code':'auth.expired' if st=='expired' else 'internal'}
                page.evaluate('autoRepairAttempted=true; refreshStatus()'); shot(name)
            page.evaluate('pairingBusy=true; repairing=true; renderPairingBusy()'); shot('S1-pairing')
            page.evaluate('pairingBusy=false; repairing=false')
            state['connection']={'key':'disconnected','label':'Disconnected','tone':'danger'}
            page.evaluate('refreshStatus()'); assert page.locator('#send').is_disabled(); shot('S1-offline')
            page.locator('#connPill').click(); shot('S4-offline')
            assert page.locator('#connectionHelp').get_attribute('href').endswith('/docs/PAIRING_PROOF_RUNBOOK.md')
            page.unroute('**/*',route)
            # New server with URL explicitly unset; no fake success accepted.
            preview=serve(replace(load_config({'BENCHLINK_DEVICE_ID':'mac-i1-fixture'}),listen_port=0))
            threading.Thread(target=preview.serve_forever,kwargs={'poll_interval':0.05},daemon=True).start()
            pb=f'http://127.0.0.1:{preview.server_address[1]}'
            try:
                page.route('**/*',lambda r:r.continue_() if urlparse(r.request.url).hostname=='127.0.0.1' else r.abort())
                page.goto(pb); page.wait_for_function('connectionKey==="not_configured"'); page.locator('#goal').fill('Preview only')
                assert page.locator('#send').is_disabled(); assert page.locator('#pairBtn').is_hidden(); shot('S1-preview'); shot('C-preview')
                page.locator('#connPill').click(); shot('S4-preview')
            finally: preview.shutdown(); preview.server_close()
        assert not errors, errors
        browser.close()
    server.shutdown(); server.server_close()
    print(f'beta-{gate}: DOM checks PASS; {len(list(out.glob("*.svg")))} screenshots; no external requests')


if __name__=='__main__': main()
