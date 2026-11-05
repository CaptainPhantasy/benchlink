"""Exercise the installed wheel, not checkout imports added by conftest."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv
import zipfile


def test_isolated_sidecar_wheel_imports_ui_and_runs_console_entry(tmp_path):
    root = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    shutil.copytree(root / "apps/mac-sidecar", source, ignore=shutil.ignore_patterns("__pycache__", "*.egg-info", "build", "dist"))
    wheels = tmp_path / "wheels"
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH" and not k.startswith("BENCHLINK_")}
    def run(command):
        result = subprocess.run([str(x) for x in command], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout
    run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "--wheel-dir", wheels, source])
    wheel = next(wheels.glob("benchlink_mac_sidecar-*.whl"))
    isolated = tmp_path / "installed"
    venv.EnvBuilder(with_pip=True).create(isolated)
    python = isolated / "bin/python"
    install = [python, "-m", "pip", "install"]
    if os.environ.get("BENCHLINK_TEST_WHEELHOUSE"):
        install += ["--no-index", "--find-links", os.environ["BENCHLINK_TEST_WHEELHOUSE"]]
    run(install + [wheel])
    script = """
import json, threading, urllib.request
from dataclasses import replace
from pathlib import Path
import benchlink_sidecar.server as server
import benchlink_sidecar.ui.copy as copy
from benchlink_sidecar.config import load_config
assert 'site-packages' in str(Path(server.__file__))
assert 'site-packages' in str(Path(copy.__file__))
assert copy.error_card('internal')['title']
sc = server.serve(replace(load_config({}), listen_port=0))
thread = threading.Thread(target=sc.serve_forever, kwargs={'poll_interval':.01}, daemon=True)
thread.start()
try:
    with urllib.request.urlopen(f'http://127.0.0.1:{sc.server_address[1]}/') as response:
        assert response.status == 200 and b'Bridge Control' in response.read()
finally:
    sc.shutdown(); sc.server_close(); thread.join()
print('installed UI/server imports and loopback startup PASS')
"""
    assert "PASS" in run([python, "-I", "-c", script])
    assert "CLI" in run([isolated / "bin/benchlink-sidecar", "--help"])
    with zipfile.ZipFile(wheel) as archive:
        assert {"benchlink_sidecar/ui/__init__.py", "benchlink_sidecar/ui/copy.py"} <= set(archive.namelist())
