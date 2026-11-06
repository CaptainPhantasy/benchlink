# smoke

Offline contract fixtures and loopback probes. No Tailscale, no Drive, no live hosts.

| Script | What |
|---|---|
| `validate_fixtures.py` | fixtures vs frozen schemas |
| `smoke_roundtrip.py` | **MVP e2e:** D3 pairing + Staff task round trip (fake, fake-Drive, callback, webhook→local receiver) + B1–B4 |
| `benchlink_testkit.py` | shared loopback harness for smoke + `tests/` |
| `../../apps/bridge-daemon/smoke_daemon.py`, `../../apps/mac-sidecar/smoke_sidecar.py` | per-app smoke |

Run: `.venv/bin/python tools/smoke/smoke_roundtrip.py` (exit 0 = all pass). See `docs/MVP_ROUNDTRIP.md`.
