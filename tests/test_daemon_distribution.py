"""Mandatory wheel/sdist install proof outside checkout; no schema/PYTHONPATH escape.

Provision setuptools, wheel and build in the test runner. For offline install set
BENCHLINK_TEST_WHEELHOUSE to `pip download --only-binary=:all: -r requirements.lock -d ...`.
"""
import json
import os
import shutil
import subprocess
import sys
import tarfile
import venv
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command, *, cwd, env):
    completed = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True, timeout=90)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return completed.stdout


INSTALLED_PROOF = r'''
import json, threading, time
from dataclasses import replace
from pathlib import Path
from importlib.resources import files
from benchlink_adapter.adapter import StaffAdapter
from benchlink_adapter.config import load_adapter_config
from benchlink_daemon.config import load_config as daemon_config
from benchlink_daemon.protocol import errors_for, SCHEMAS_DIR
from benchlink_daemon.server import BridgeState, serve
from benchlink_sidecar.client import BridgeClient
from benchlink_sidecar.config import load_config as sidecar_config
from benchlink_sidecar.pairing import generate_key, allowlist_snippet
from benchlink_sidecar.ux import BridgeControlShell
root = Path.cwd()
assert str(SCHEMAS_DIR).startswith(str(files("benchlink_daemon")))
assert errors_for("task.create", {"v":"1"})
keypath = root / "generated.pem"
key = generate_key(str(keypath))
allowlist = root / "allowlist.json"
allowlist.write_text(json.dumps(allowlist_snippet("installed-wheel", key)))
results = root / "results"
results.mkdir()
adapter = StaffAdapter(load_adapter_config({"BENCHLINK_ADAPTER_TRANSPORT":"fake",
    "BENCHLINK_RESULT_SOURCE":"local_folder", "BENCHLINK_RESULTS_LOCAL_DIR":str(results)}))
cfg = replace(daemon_config({"BENCHLINK_DEVICE_ALLOWLIST_PATH":str(allowlist)}), listen_port=0)
server = serve(cfg, state=BridgeState(cfg, adapter=adapter))
threading.Thread(target=server.serve_forever, kwargs={"poll_interval":.01}, daemon=True).start()
try:
    base = "http://127.0.0.1:" + str(server.server_address[1])
    scfg = sidecar_config({"BENCHLINK_BRIDGE_BASE_URL":base, "BENCHLINK_DEVICE_ID":"installed-wheel",
                           "BENCHLINK_DEVICE_KEY_PATH":str(keypath)})
    client = BridgeClient(scfg)
    status, health = client.health()
    assert status == 200 and not errors_for("health.pong", health)
    snapshot = BridgeControlShell(scfg, client=client, max_wait_ticks=100, poll_sleep=.01).start_and_wait(
        "installed package roundtrip")["snapshot"]
    assert snapshot["phase"] == "done", snapshot
    assert not errors_for("task.result", snapshot["result"])
    assert client.pair_count == 1 and len(adapter.transport.dispatched) == 1
    print(json.dumps({"installed_health":True,"frozen_validation":True,"paired_roundtrip":True,"dispatches":1}))
finally:
    server.shutdown()
    server.server_close()
'''


def test_daemon_wheel_and_sdist_ship_frozen_schemas_and_work_outside_checkout(tmp_path):
    env = {k:v for k,v in os.environ.items() if k != "PYTHONPATH" and not k.startswith("BENCHLINK_")}
    source = tmp_path / "source"
    for app in ["staff-adapter", "mac-sidecar", "bridge-daemon"]:
        shutil.copytree(ROOT / "apps" / app, source / "apps" / app,
                        ignore=shutil.ignore_patterns("__pycache__", "build", "dist", "*.egg-info"))
    canonical = ROOT / "packages/bridge-protocol/schemas"
    shutil.copytree(canonical, source / "packages/bridge-protocol/schemas")
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    for app in ["staff-adapter", "mac-sidecar", "bridge-daemon"]:
        run([sys.executable, "-m", "pip", "wheel", "--no-index", "--no-deps", "--no-build-isolation",
             "-w", str(wheels), str(source / "apps" / app)], cwd=tmp_path, env=env)
    daemon_wheel = next(wheels.glob("benchlink_bridge_daemon-*.whl"))
    expected = {"benchlink_daemon/schemas/" + p.name: p.read_bytes() for p in canonical.glob("*.schema.json")}
    with zipfile.ZipFile(daemon_wheel) as wheel:
        assert set(expected) <= set(wheel.namelist()), "wheel omits frozen daemon schema resources"
        for name, data in expected.items():
            assert wheel.read(name) == data, name

    sdists = tmp_path / "sdists"
    run([sys.executable, "-m", "build", "--sdist", "--no-isolation", "--outdir", str(sdists),
         str(source / "apps/bridge-daemon")], cwd=tmp_path, env=env)
    unpacked = tmp_path / "unpacked"
    with tarfile.open(next(sdists.glob("*.tar.gz"))) as archive:
        archive.extractall(unpacked, filter="data")
    sdist_root = next(unpacked.iterdir())
    for name, data in expected.items():
        assert (sdist_root / name).read_bytes() == data
    rebuilt = tmp_path / "rebuilt"
    run([sys.executable, "-m", "pip", "wheel", "--no-index", "--no-deps", "--no-build-isolation",
         "-w", str(rebuilt), str(sdist_root)], cwd=tmp_path, env=env)
    with zipfile.ZipFile(next(rebuilt.glob("*.whl"))) as wheel:
        for name, data in expected.items():
            assert wheel.read(name) == data

    runtime = tmp_path / "runtime"
    venv.EnvBuilder(with_pip=True).create(runtime)
    python = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    pip = [str(python), "-m", "pip", "install"]
    wheelhouse = os.environ.get("BENCHLINK_TEST_WHEELHOUSE")
    if wheelhouse:
        pip += ["--no-index", "--find-links", wheelhouse]
    run(pip + ["-r", str(ROOT / "requirements.lock")] + [str(w) for w in wheels.glob("*.whl")],
        cwd=tmp_path, env=env)
    # Prove both source-wheel and sdist-derived wheel with real installed HTTP flows.
    for index, wheel in enumerate([daemon_wheel, next(rebuilt.glob("*.whl"))]):
        run([str(python), "-m", "pip", "install", "--no-index", "--no-deps", "--force-reinstall", str(wheel)],
            cwd=tmp_path, env=env)
        isolated = tmp_path / ("proof-" + str(index))
        isolated.mkdir()
        output = run([str(python), "-I", "-c", INSTALLED_PROOF], cwd=isolated, env=env)
        assert json.loads(output)["dispatches"] == 1
    for executable in ["benchlink-daemon", "benchlink-sidecar"]:
        run([str(runtime / "bin" / executable), "--help"], cwd=tmp_path, env=env)
