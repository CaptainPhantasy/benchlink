# Mac sidecar scaffold — evidence

**Date:** 2026-11-05 10:35 EST (America/Indianapolis)  
**Author:** Sofia · Full-Stack Engineer  
**Authority:** Floyd GM order — proceed against **0.2.0-mvp**; do not wait on Larry's Tailscale hostname; configurable base URL; no invented live DNS; no Build Session API.  
**D2:** locked — extension sealed → sidecar-only ([MAC_EXTENSION_SPIKE.md](MAC_EXTENSION_SPIKE.md)).

## Created
`apps/mac-sidecar/` — stdlib-only Python ≥ 3.11 (no runtime deps; `jsonschema` optional for smoke).

| File | Role |
|------|------|
| `README.md` | Purpose, config contract, run + smoke |
| `pyproject.toml` | Package `benchlink-mac-sidecar`, script `benchlink-sidecar` |
| `.env.example` / `config.example.toml` | Config examples (URL unset; placeholder commented) |
| `benchlink_sidecar/config.py` | Env > TOML > default loader; fail-closed `api_base`; denylist port 63301; https-unless-loopback; loopback-only listen |
| `benchlink_sidecar/client.py` | Request builders + live send for OpenAPI ops; dry-run uses `{BENCHLINK_BRIDGE_BASE_URL}/v1` template; token redaction |
| `benchlink_sidecar/server.py` | Loopback stub `127.0.0.1:8787`: `GET /v1/health` (proxy / 503 `error`), `GET /_sidecar/status` (non-contract) |
| `benchlink_sidecar/__main__.py` | CLI: `config` / `dry-run` / `health` / `serve` |
| `smoke_sidecar.py` | Offline smoke (20 checks) |

Also edited: `SSoT.md` (D2 lock, component row, config table, change log), `docs/api-contracts-mvp.md` (MVP gate: spike done/sealed; hostname = Larry deploy blocker), `.gitignore` (`!.env.example`, `__pycache__/`).

## Config keys
`BENCHLINK_BRIDGE_BASE_URL` (primary; TOML `[bridge] base_url`), `BENCHLINK_ACCESS_TOKEN` (`[bridge] access_token`), `BENCHLINK_ALLOW_PLACEHOLDER_HOST` (`[bridge] allow_placeholder_host`), `BENCHLINK_SIDECAR_LISTEN_HOST` (`[sidecar] listen_host`, default `127.0.0.1`), `BENCHLINK_SIDECAR_LISTEN_PORT` (`[sidecar] listen_port`, default `8787`), `BENCHLINK_SIDECAR_CONFIG` (TOML path).

Placeholder `https://bridge.floyds-labs.ts.net` = logical, **unset-until-provisioned** (Larry). Refused unless `BENCHLINK_ALLOW_PLACEHOLDER_HOST=1`.

## OpenAPI paths wired (`{base}/v1`)
`GET /health` (health) · `POST /sessions` (sessionCreate) · `POST /tasks` (taskCreate) · `GET /tasks/{task_id}` (taskGet). Dry-run bodies validated against `session_create` / `task_create` schemas.

## CLI exit codes (observed, BENCHLINK_* unset)
Contract: 0 ok · 1 usage · 2 bad config · 3 upstream URL unset/placeholder (fail closed) · 4 upstream error.
```
config (URL unset): 0
dry-run (URL unset): 0
REFUSED: BENCHLINK_BRIDGE_BASE_URL is unset. The bridge-daemon Tailscale hostname is not provisioned yet (Larry owns live DNS). Refusing to call upstream.
health (URL unset): 3
health (placeholder host): 3
config (Build Session API port): 2
```

## Smoke
```bash
cd /workspace/grok-app-floyd-bridge
.venv/bin/python apps/mac-sidecar/smoke_sidecar.py ; echo EXIT=$?
```
Exit **0**. Output:
```
== CLI exit-code contract ==
PASS config parse, URL unset: exit=0 (want 0)
PASS dry-run, URL unset: exit=0 (want 0)
PASS health refuses when URL unset: exit=3 (want 3)
PASS health refuses logical placeholder host: exit=3 (want 3)
PASS config parse, placeholder set: exit=0 (want 0)
PASS reject non-URL: exit=2 (want 2)
PASS reject Build Session API port 63301: exit=2 (want 2)
PASS reject plain http to non-loopback: exit=2 (want 2)
PASS reject non-loopback listen host: exit=2 (want 2)
PASS TOML config file parses: exit=0 (want 0)
== dry-run bodies vs schemas ==
PASS dry-run operationIds: ['health', 'sessionCreate', 'taskCreate', 'taskGet']
PASS dry-run health URL templated, no host invented
PASS session.create body valid vs session_create.schema.json 
PASS task.create body valid vs task_create.schema.json 
== loopback stub, upstream UNSET ==
PASS stub /v1/health unset -> 503 offline
PASS stub 503 body valid vs error.schema.json
PASS stub /_sidecar/status reports unset
== loopback stub -> fake upstream (127.0.0.1, ephemeral) ==
PASS stub proxies GET /v1/health -> 200
PASS proxied pong valid vs health_pong.schema.json
PASS CLI health against fake upstream exit=0 (want 0)

20/20 passed
```
Network: only `127.0.0.1` ephemeral ports (in-process fake daemon). No Tailscale, no DNS lookups, no Build Session API.

## SHA-256
```
195b63efbdadc037137996c4fdd59d8c4a9f8c6c4d779dfcfbb72a18def91535  apps/mac-sidecar/.env.example
5fd9ccb0fd4d77a0987c266771435bbe19269818f5f30c3e4b3969656df39e3f  apps/mac-sidecar/README.md
d1d8722920c68265b2343d4ea6be4a20aaab75bb2ea213796c351789c60852c5  apps/mac-sidecar/benchlink_sidecar/__init__.py
8a282be60f15646ff8210061c168ea4faefd25ce6701e2fcae7a0a8270314dbe  apps/mac-sidecar/benchlink_sidecar/__main__.py
6343ce7deb5bbd67ee32e95bd271537c6f078633ef40d935da1ebfec97eaa6c4  apps/mac-sidecar/benchlink_sidecar/client.py
4439bc56a28e1bef96de390e910cd1555313f0b7adddf6973680ae451949b39d  apps/mac-sidecar/benchlink_sidecar/config.py
a666ad55900c701f3a9d77f63534efdae0b38abf1e1ab0dd05c211c6499dad2d  apps/mac-sidecar/benchlink_sidecar/server.py
538ff64c50389dcf8b7329dabcf4e3152a09cc7d1415c8e89d697da36386a5d2  apps/mac-sidecar/config.example.toml
626136716ccc8936f901d62f1de70ad6e74a9d8a4499faee306bec33995c9c4b  apps/mac-sidecar/pyproject.toml
891669bfd6a6041040c1c7ee0c9af778f84b97f3dd28aa43c0996963e9c6e1cf  apps/mac-sidecar/smoke_sidecar.py
```
