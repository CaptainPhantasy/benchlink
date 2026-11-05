"""I1 pairing boundaries: safe UI views, no live simulation, stable bearer."""
import json
import threading
from dataclasses import replace

import pytest
from benchlink_sidecar.client import BridgeClient
from benchlink_sidecar.config import ConfigError, load_config
from benchlink_sidecar.server import HTML_SHELL, serve
from benchlink_sidecar.ux import BridgeControlShell, Phase, WaitStatus
from benchlink_sidecar.pairing import PairingError
from test_connection_pairing import _get, _post


def start(cfg):
    sc=serve(replace(cfg,listen_port=0))
    threading.Thread(target=sc.serve_forever,kwargs={'poll_interval':0.05},daemon=True).start()
    return sc,f'http://127.0.0.1:{sc.server_address[1]}'


def test_ui_status_and_ux_never_expose_key_path():
    cfg=replace(load_config({'BENCHLINK_BRIDGE_BASE_URL':'http://127.0.0.1:9'}),device_key_path='/private/i1-fixture.pem')
    sc,base=start(cfg)
    try:
        for path in ('/_sidecar/status','/_sidecar/ux'):
            _,j=_get(base,path)
            assert cfg.device_key_path not in json.dumps(j)
            assert 'BENCHLINK_DEVICE_KEY_PATH' not in json.dumps(j)
        assert _get(base,'/_sidecar/status')[1]['config']['bridge_host']=='127.0.0.1'
    finally: sc.shutdown(); sc.server_close()


@pytest.mark.parametrize('failure,code',[(ConfigError('private key at /private/i1-fixture.pem'),'internal'),(OSError('private path /private/i1-fixture.pem'),'offline'),(PairingError('auth.denied','raw bridge /private/i1-fixture.pem',403),'auth.denied')])
def test_pair_failures_are_sanitized(monkeypatch,failure,code):
    import benchlink_sidecar.pairing as pairing
    def fail(*a,**k): raise failure
    monkeypatch.setattr(pairing,'pair',fail)
    sc,base=start(load_config({'BENCHLINK_BRIDGE_BASE_URL':'http://127.0.0.1:9'}))
    try:
        _,first=_post(base,'/_sidecar/pair')
        _,second=_post(base,'/_sidecar/pair')
        assert first['code']==code
        assert 'presentation' in first
        assert first['correlation_id']!=second['correlation_id']
        _,status=_get(base,'/_sidecar/status')
        assert status['pairing']['error_code']==code
        assert '/private/' not in json.dumps([first,second,status])
        assert 'raw bridge' not in json.dumps([first,second,status])
    finally: sc.shutdown(); sc.server_close()


@pytest.mark.parametrize('status,body',[(501,{}),(500,{}),(503,{'type':'task.result','status':'succeeded'}),(502,{'type':'error','code':'offline','message':'raw server'}),(200,{})])
def test_live_wait_never_simulates_success(status,body):
    cfg=load_config({'BENCHLINK_BRIDGE_BASE_URL':'http://127.0.0.1:9'})
    client=BridgeClient(cfg); client.send=lambda req:(status,body)
    shell=BridgeControlShell(cfg,client=client,phase=Phase.WAIT,wait_status=WaitStatus.RUNNING,task_id='task_fixture')
    shell.wait_once()
    assert shell.phase==Phase.ERROR
    assert shell.wait_status==WaitStatus.ERROR
    assert shell.result is None
    assert '[offline demo]' not in json.dumps(shell.snapshot())


def test_failed_manual_repair_preserves_working_bearer(monkeypatch):
    import benchlink_sidecar.pairing as pairing
    cfg=load_config({'BENCHLINK_BRIDGE_BASE_URL':'http://127.0.0.1:9'})
    client=BridgeClient(cfg); client._live_token='fixture-memory-only'; client.pairing_state='paired'
    def fail(*a,**k): raise OSError('offline')
    monkeypatch.setattr(pairing,'pair',fail)
    with pytest.raises(OSError): client.pair()
    assert client._live_token=='fixture-memory-only'
    assert client.pairing_snapshot()['state']=='paired'


def test_pair_ui_refresh_and_help_contract():
    for value in ('id="connectionHelp"', 'id="s4PairingLabel"', '/docs/PAIRING_PROOF_RUNBOOK.md', 'id="copyDiagnostics"', 'aria-label="Copy device ID"', 'aria-live="polite"', 'Re-pairing…', 'Pairing…', 'renderPairingBusy', 'autoRepairAttempted', 'Reconnect to send.', 'Set the bridge address first.'):
        assert value in HTML_SHELL
