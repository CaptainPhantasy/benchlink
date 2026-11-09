"""Reload and transient initialization failures recover through the visible UI."""
import pytest

from test_reconnect_recovery import accepted_sidecar
from test_retry_recovery import launch_browser, post


def test_reloaded_running_task_resumes_polling_without_another_send(accepted_sidecar):
    sync = pytest.importorskip('playwright.sync_api')
    shell, base, mode, calls, task_ids = accepted_sidecar
    mode['break_poll'] = False
    status, response = post(base, '/_sidecar/ux/run', {'goal': 'Finish after reload', 'max_ticks': 1})
    assert status == 200 and response['snapshot']['phase'] == 'wait'
    identity = shell.session_id, shell.task_id
    with sync.sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page()
        page.set_default_timeout(7000)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(base)
        page.wait_for_function('loaded && lastSnapshot.phase === "wait"')
        page.reload()
        page.wait_for_function('loaded && lastSnapshot.phase === "wait"')
        assert page.locator('#send').is_disabled()
        mode['finish'] = True
        page.wait_for_function('lastSnapshot.phase === "done" && !busy')
        assert page.locator('#statusPill').inner_text() == 'Result'
        assert (shell.session_id, shell.task_id) == identity
        assert set(task_ids) == {identity[1]}
        assert calls.count('taskCreate') == calls.count('sessionCreate') == 1
        assert calls.count('sessionClose') == 0
        assert not errors
        browser.close()


@pytest.mark.parametrize('recovery', ['error_retry', 'connection_retry', 'status_refresh'])
def test_failed_initial_snapshot_can_be_reloaded_without_refreshing_page(accepted_sidecar, recovery):
    sync = pytest.importorskip('playwright.sync_api')
    _, base, _, calls, _ = accepted_sidecar
    fail = {'active': True, 'reads': 0}
    with sync.sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page()
        page.set_default_timeout(7000)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))

        def initial_snapshot(route):
            fail['reads'] += 1
            if fail['active']:
                route.fulfill(status=503, content_type='application/json', body='{"code":"offline"}')
            else:
                route.continue_()

        page.route('**/_sidecar/ux', initial_snapshot)
        page.goto(base)
        page.wait_for_function('!loaded && !busy && localFailure && connectionKey === "connected"')
        assert page.locator('#send').is_disabled()
        reads = fail['reads']
        fail['active'] = False
        if recovery == 'error_retry':
            page.locator('#errPrimary').click()
        elif recovery == 'connection_retry':
            page.locator('#connPill').click()
            page.locator('#s4Retry').click()
        else:
            page.evaluate('refreshStatus()')
        page.wait_for_function('loaded && !busy && !recovering && !localFailure')
        page.locator('#goal').fill('Send remains an explicit action')
        page.wait_for_function('!document.getElementById("send").disabled')
        assert fail['reads'] > reads
        assert calls.count('sessionCreate') == calls.count('taskCreate') == calls.count('sessionClose') == 0
        assert not errors
        browser.close()
