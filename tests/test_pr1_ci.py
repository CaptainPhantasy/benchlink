"""Locally validate JSON-syntax YAML Actions policy; never contact GitHub."""
import json
import re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def test_ci_is_pull_request_only_all_bases_and_least_privilege():
    workflow=json.loads((ROOT/'.github/workflows/pytest.yml').read_text())
    assert set(workflow['on'])=={'pull_request'}
    assert 'branches' not in workflow['on']['pull_request']
    assert workflow['permissions']=={'contents':'read'}
    assert workflow['jobs']['verify']['runs-on']=='ubuntu-latest'
    steps=workflow['jobs']['verify']['steps']
    checkout=next(s for s in steps if s.get('uses','').startswith('actions/checkout@'))
    assert checkout['with']['persist-credentials'] is False
    for step in steps:
        if 'uses' in step: assert re.fullmatch(r'[^@]+@[0-9a-f]{40}',step['uses'])
    raw=json.dumps(workflow)
    for unsafe in ['pull_request_target','secrets.','id-token','contents: write','continue-on-error']:
        assert unsafe not in raw
    for requirement in ['setuptools==84.2.1','wheel==0.48.0','build==1.6.1','playwright==1.64.1',
                        'requirements-dev.lock','pip download','--only-binary=:all:','playwright install --with-deps chromium',
                        'run_local.py','pytest','smoke_roundtrip.py','smoke_daemon.py','smoke_sidecar.py','check_node.py']:
        assert requirement in raw,requirement
    assert 'BENCHLINK_SKIP' not in raw
