"""No live-host probe: audit events simulate forbidden addresses without I/O."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
def test_test_runner_drops_every_inherited_webhook_auth_and_proxy(monkeypatch,tmp_path):
    for key in ['BENCHLINK_TARGET_FLOYD_WEBHOOK_URL','BENCHLINK_TARGET_ETHAN_SENDER_KEY',
                'UNRELATED_ROUTINE_WEBHOOK_URL','BENCHLINK_ACCESS_TOKEN','HTTPS_PROXY']:
        monkeypatch.setenv(key,'synthetic-sentinel-not-a-credential')
    spec=importlib.util.spec_from_file_location('local_runner',ROOT/'tools/testing/run_local.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    env=module.clean_environment(tmp_path)
    assert not any('WEBHOOK' in k or 'SENDER_KEY' in k or 'PROXY' in k for k in env)
    assert set(k for k in env if k.startswith('BENCHLINK_'))=={'BENCHLINK_TEST_WHEELHOUSE'}
    assert env['PIP_NO_INDEX']=='1' and env['PIP_CONFIG_FILE']==os.devnull
    code="""
import json,socket,sys
blocked=[]
s=socket.socket()
for event,args in [('socket.connect',(s,('192.0.2.1',443))),('socket.bind',(s,('0.0.0.0',0))),('socket.getaddrinfo',('remote.invalid',443,0,0,0))]:
    try: sys.audit(event,*args)
    except PermissionError: blocked.append(event)
print(json.dumps(blocked))
"""
    result=subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,text=True,check=True)
    assert json.loads(result.stdout)==['socket.connect','socket.bind','socket.getaddrinfo']
