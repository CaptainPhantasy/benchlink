"""Run local verification with no inherited webhook/auth/proxy configuration.

Usage: python tools/testing/run_local.py <command> [args...]
Wheelhouse defaults to <repo>/.test-wheelhouse; override only with --wheelhouse PATH.
This is test tooling, not a daemon/sidecar service launcher.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
def clean_environment(wheelhouse):
    keep={'PATH','HOME','LANG','LC_ALL','TERM','TMPDIR','PLAYWRIGHT_BROWSERS_PATH'}
    env={k:v for k,v in os.environ.items() if k in keep}
    env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(Path(__file__).resolve().parent),
               PIP_NO_INDEX='1',PIP_CONFIG_FILE=os.devnull,PIP_DISABLE_PIP_VERSION_CHECK='1',
               PIP_FIND_LINKS=str(Path(wheelhouse).resolve()),
               BENCHLINK_TEST_WHEELHOUSE=str(Path(wheelhouse).resolve()),TZ='America/Indianapolis')
    return env
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheelhouse',default=str(Path(__file__).resolve().parents[2]/'.test-wheelhouse'))
    parser.add_argument('command',nargs=argparse.REMAINDER)
    args=parser.parse_args()
    if not args.command: parser.error('a verification command is required')
    return subprocess.run(args.command,env=clean_environment(args.wheelhouse)).returncode
if __name__=='__main__': sys.exit(main())
