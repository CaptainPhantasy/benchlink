"""Check the evaluated Python HTML literal, not its escaped source."""
import re
import shutil
import subprocess

import pytest
from benchlink_sidecar.server import HTML_SHELL


def test_evaluated_inline_script_parses(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    script = re.search(r"<script>(.*?)</script>", HTML_SHELL, re.S).group(1)
    for key, value in {"__ROSTER__": "[]", "__OFFLINE__": "null", "__COPY__": "{}", "__PREVIEW__": "false"}.items():
        script = script.replace(key, value)
    path = tmp_path / "sidecar.js"
    path.write_text(script)
    result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
