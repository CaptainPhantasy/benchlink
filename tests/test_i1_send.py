"""I1 send safety, including the phase-check/dispatch race."""
import threading
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor

from benchlink_sidecar.server import HTML_SHELL, serve
from benchlink_sidecar.config import load_config
from benchlink_sidecar import ux
from test_send_safety import _post


def test_run_dispatch_is_serialized(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    count = []
    def blocked(self, **kwargs):
        count.append(1); entered.set(); release.wait(3)
        return {'snapshot': {'phase':'wait','wait_status':'queued'}}
    monkeypatch.setattr(ux.BridgeControlShell, 'start_and_wait', blocked)
    sc=serve(replace(load_config({'BENCHLINK_BRIDGE_BASE_URL':'http://127.0.0.1:9'}),listen_port=0))
    threading.Thread(target=sc.serve_forever,kwargs={'poll_interval':0.05},daemon=True).start()
    base=f'http://127.0.0.1:{sc.server_address[1]}'
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(_post,base,'/_sidecar/ux/run',{'goal':'first'})
            assert entered.wait(1)
            second=pool.submit(_post,base,'/_sidecar/ux/run',{'goal':'second'})
            try:
                status,body=second.result(timeout=2)
                assert status==409
                assert body['presentation']['error']['title']=='Task in progress'
                assert len(count)==1
            finally: release.set()
            assert first.result()[0]==200
    finally:
        release.set(); sc.shutdown(); sc.server_close()


def test_task_in_flight_has_dedicated_exception():
    assert hasattr(ux,'TaskInFlight')
    shell=ux.BridgeControlShell(load_config({})); shell.phase=ux.Phase.WAIT
    import pytest
    with pytest.raises(ux.TaskInFlight): shell.start_and_wait('second')


def test_selected_target_has_a_decorative_icon():
    assert 'lucide-check' in HTML_SHELL
    assert 'aria-hidden' in HTML_SHELL
