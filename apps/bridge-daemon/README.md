# bridge-daemon — Benchlink inference runtime (D4)

**Contract:** 0.2.0-mvp  
**Status:** MVP round trip (2026-11-05 America/Indianapolis)  
**Edit home:** Computer `/workspace/grok-app-floyd-bridge/`  
**Runtime home (later):** `/srv/organized/floyds-labs-staff/grok-app-floyd-bridge/`

Python ≥ 3.11 + `jsonschema`, `cryptography`; imports `apps/staff-adapter`. Serves the Tailscale HTTPS upstream the Mac sidecar
calls via `BENCHLINK_BRIDGE_BASE_URL`. **No invented live DNS** — public base URL stays unset until Douglas clears
Tailscale serve on `inference.tail58d565.ts.net:19740` (Larry FYI). Do not call
that host from Computer smoke (HTTPS serve not ready).

## Config

| Env var | Default | Notes |
|---|---|---|
| `BENCHLINK_BRIDGE_LISTEN_HOST` | `127.0.0.1` | Computer smoke binds loopback; an IPv6 literal such as `::1` gets an IPv6 socket and bracketed URLs |
| `BENCHLINK_BRIDGE_LISTEN_PORT` | `8790` | Avoids sidecar `8787`; rejects `63301` |
| `BENCHLINK_BRIDGE_PUBLIC_BASE_URL` | **unset** | Intended: `https://inference.tail58d565.ts.net:19740` (Larry; unset until Douglas clears serve). Refused unless `BENCHLINK_ALLOW_PLACEHOLDER_HOST=1`. Demoted: `bridge.floyds-labs.ts.net`. |
| `BENCHLINK_ALLOW_PLACEHOLDER_HOST` | `false` | Same policy as mac-sidecar |

## Auth / adapter config
D3 (`BENCHLINK_DEVICE_ALLOWLIST_PATH`, `BENCHLINK_REQUIRE_TAILSCALE_IDENTITY` — **must be 1 in prod**,
`BENCHLINK_ACCESS_TTL_SECONDS`) and staff-adapter env (transport, per-target webhook URL + sender key,
result source, timeouts): see `.env.example` and `docs/MVP_ROUNDTRIP.md`.

## Endpoints (MVP round trip)
Full table and error classes: [`docs/MVP_ROUNDTRIP.md`](../../docs/MVP_ROUNDTRIP.md) §2–3.
Health, D3 pairing, sessions (create/attach/events/close), tasks (create/get) implemented;
`sessions/{id}/messages` returns 501 (not MVP). Non-contract: `/_bridge/results/{task_id}`
(http_callback only), `/_daemon/status` (loopback-only bridge status).

## Run / smoke

```bash
cd apps/bridge-daemon
python3 -m benchlink_daemon config
python3 -m benchlink_daemon serve   # http://127.0.0.1:8790/v1/health
.venv/bin/python -m pytest -q tests && .venv/bin/python tools/smoke/smoke_roundtrip.py

# from repo root
.venv/bin/python apps/bridge-daemon/smoke_daemon.py
```
