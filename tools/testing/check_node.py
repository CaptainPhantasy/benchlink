"""Mandatory Node syntax check of evaluated Python HTML; no browser/network."""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'apps/mac-sidecar'))
from benchlink_sidecar.server import HTML_SHELL
node=shutil.which('node')
if not node: raise SystemExit('Node is required, not an optional skip')
script=re.search(r'<script>(.*?)</script>',HTML_SHELL,re.S).group(1)
for key,value in {'__ROSTER__':'[]','__OFFLINE__':'null','__COPY__':'{}','__PREVIEW__':'false'}.items():
    script=script.replace(key,value)
with tempfile.TemporaryDirectory(prefix='benchlink-node-') as directory:
    p=Path(directory)/'sidecar.js';p.write_text(script)
    subprocess.run([node,'--check',str(p)],check=True)
print('Evaluated sidecar inline JavaScript: Node syntax PASS')
