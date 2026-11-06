"""Benchlink bridge-daemon — inference runtime home (D4).

Contract: 0.2.0-mvp. Serves Tailscale HTTPS upstream for the Mac sidecar.
Computer is the edit home; inference path later:
  /srv/organized/floyds-labs-staff/grok-app-floyd-bridge/
"""
import sys as _sys
from pathlib import Path as _Path

__version__ = "0.2.0+mvp.roundtrip"
CONTRACT_VERSION = "0.2.0-mvp"

# staff-adapter lives beside us in the repo (apps/staff-adapter); make it importable
try:  # pragma: no cover - trivial
    import benchlink_adapter as _ba  # noqa: F401
except ImportError:  # pragma: no cover
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / "staff-adapter"))
