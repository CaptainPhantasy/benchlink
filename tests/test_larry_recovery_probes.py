"""Review-only extra regressions; synthetic loopback fixtures, no source edits."""
import json
import sys
from pathlib import Path
import pytest
from benchlink_testkit import Harness, valid
from benchlink_sidecar.ux import BridgeControlShell, TaskInFlight


def install_lost_ack(h, monkeypatch, recovery):
    handler = h.srv.RequestHandlerClass
    original = handler._send
    def send(self, status, payload, type_name=None, list_of=None):
        if self.path == '/v1/tasks' and status == 202:
            raw, response_status = b'{"type":"error"}', 502
        elif self.path.startswith('/v1/tasks/') and status == 200:
            response_status, body = recovery(payload)
            raw = json.dumps(body).encode()
        else:
            return original(self, status, payload, type_name, list_of)
        self.send_response(response_status)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    monkeypatch.setattr(handler, '_send', send)


def try_second_send(shell):
    try:
        shell.start_and_wait('Second send must be blocked while original is running')
        return False
    except TaskInFlight:
        return True


@pytest.mark.parametrize('wrong', ['task_id', 'session_id', 'both'])
def test_recovery_get_cannot_cross_ack_unknown_submission(wrong, monkeypatch):
    with Harness(adapter_env={'BENCHLINK_FAKE_TRANSPORT_MODE':'silent'}) as h:
        def recovery(payload):
            body = dict(payload, status='succeeded', summary='Synthetic unrelated completed result')
            if wrong in {'task_id', 'both'}: body['task_id'] = 'task_other'
            if wrong in {'session_id', 'both'}: body['session_id'] = 'sess_other'
            assert not valid('task.result', body)
            return 200, body
        install_lost_ack(h, monkeypatch, recovery)
        shell = BridgeControlShell(h.sidecar_cfg, client=h.client, poll_sleep=0)
        shell.start_and_wait('Original must remain in flight')
        original_id = shell.task_id
        assert shell.submission_uncertain and shell.can_resume_interrupted_wait()
        assert len(h.adapter.transport.dispatched) == 1
        shell.retry_terminal_error()
        phase, uncertain = shell.phase.value, shell.submission_uncertain
        original_status = h.state.tasks[original_id]['status']
        blocked = try_second_send(shell)
        counts = len(h.state.tasks), len(h.state.sessions), len(h.adapter.transport.dispatched)
        print(json.dumps({'case':wrong, 'after_retry_phase':phase, 'after_retry_uncertain':uncertain,
                          'original_task_status':original_status, 'second_send_blocked':blocked,
                          'task_session_dispatch_counts':counts}))
        assert blocked and uncertain and counts == (1,1,1), 'cross-ACK opened another Send while original task is still in flight'


@pytest.mark.parametrize('status', [200,400,401,403,500,501,502,504])
def test_malformed_recovery_get_never_unlocks_unknown_submission(status, monkeypatch):
    with Harness(adapter_env={'BENCHLINK_FAKE_TRANSPORT_MODE':'silent'}) as h:
        install_lost_ack(h, monkeypatch, lambda payload: (status, {'type':'error'}))
        shell = BridgeControlShell(h.sidecar_cfg, client=h.client, poll_sleep=0)
        shell.start_and_wait('Original must remain in flight')
        original_id = shell.task_id
        assert shell.submission_uncertain and shell.can_resume_interrupted_wait()
        shell.retry_terminal_error()
        error_code, uncertain = shell.error['code'], shell.submission_uncertain
        resumable = shell.can_resume_interrupted_wait()
        original_status = h.state.tasks[original_id]['status']
        blocked = try_second_send(shell)
        counts = len(h.state.tasks), len(h.state.sessions), len(h.adapter.transport.dispatched)
        print(json.dumps({'case':'GET malformed '+str(status), 'after_retry_error':error_code,
                          'after_retry_uncertain':uncertain, 'after_retry_resumable':resumable,
                          'original_task_status':original_status, 'second_send_blocked':blocked,
                          'task_session_dispatch_counts':counts}))
        assert blocked and counts == (1,1,1), 'malformed recovery response opened another Send while original task is still in flight'


@pytest.mark.parametrize('scenario', ['cross-ack', 'malformed-401'])
def test_browser_recovery_must_not_enable_duplicate_send(scenario, monkeypatch):
    import threading
    import time
    from dataclasses import replace
    from playwright.sync_api import sync_playwright
    from benchlink_sidecar import server
    with Harness(adapter_env={'BENCHLINK_FAKE_TRANSPORT_MODE':'silent'}) as h:
        def recovery(payload):
            if scenario == 'malformed-401': return 401, {'type':'error'}
            body = dict(payload, status='succeeded', task_id='task_other', session_id='sess_other', summary='Unrelated fixture result')
            assert not valid('task.result', body)
            return 200, body
        install_lost_ack(h, monkeypatch, recovery)
        sidecar = server.serve(replace(h.sidecar_cfg, listen_port=0))
        threading.Thread(target=sidecar.serve_forever, kwargs={'poll_interval':.05}, daemon=True).start()
        base = 'http://127.0.0.1:' + str(sidecar.server_address[1])
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
                page = browser.new_page(); page.set_default_timeout(7000)
                page.route('**/*', lambda r: r.continue_() if r.request.url.startswith(base+'/') else r.abort())
                page.goto(base); page.locator('#pairBtn').click()
                page.wait_for_function("pairingState==='paired' && !pairingBusy")
                page.locator('#goal').fill('Browser original must remain in flight')
                page.locator('#send').click()
                page.wait_for_function("lastSnapshot.phase==='error' && !sending")
                assert page.evaluate('lastSnapshot.submission_uncertain') and page.locator('#send').is_disabled()
                original_id = page.evaluate('lastSnapshot.task_id')
                page.evaluate('actions.Retry()')
                # Safe implementation rejects cross-ACK rather than entering the old bug's Done state.
                page.wait_for_function("lastSnapshot.phase==='error' && !recovering")
                enabled = page.locator('#send').is_enabled()
                original_status = h.state.tasks[original_id]['status']
                if enabled:
                    page.locator('#goal').fill('Duplicate browser submission must be blocked')
                    page.locator('#send').click()
                    page.wait_for_function("!sending && lastSnapshot.phase==='error'")
                counts = len(h.state.tasks), len(h.state.sessions), len(h.adapter.transport.dispatched)
                print(json.dumps({'browser_case':scenario, 'send_enabled_after_recovery':enabled,
                                  'original_task_status':original_status, 'task_session_dispatch_counts':counts}))
                browser.close()
                assert not enabled and counts == (1,1,1), 'browser Retry made duplicate Send available without End'
        finally:
            sidecar.shutdown(); sidecar.server_close()
