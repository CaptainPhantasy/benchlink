# mac-sidecar — Benchlink Mac client stub (Bridge Control)

**Contract:** 0.2.0-mvp (`docs/api-contracts-mvp.md`, `packages/bridge-protocol/openapi.v1.json`)  
**Decision:** D2 locked — Grok.app extension surface is sealed (`docs/MAC_EXTENSION_SPIKE.md`) → **sidecar-only**.  
**Status:** UX shell (2026-11-05 America/Indianapolis). Stdlib-only Python ≥ 3.11.

## Purpose
Companion **Bridge Control** client that runs on the Mac mini *beside* Grok.app (not inside it).
It talks **Tailscale HTTPS** to `bridge-daemon` on inference and exposes a loopback stub at
`http://127.0.0.1:8787/v1` plus an HTML/TUI Bridge Control shell (Amir v0.2 A3/A5).

**It NEVER touches** the Grok Build Session API (`127.0.0.1:63301`), Remote IM, or Grok Build providers.
Port 63301 is hard-rejected as an upstream or listen port.

## Config contract
Precedence: **env > TOML file > default**. Copy `.env.example` or `config.example.toml`.

| Env var | TOML key | Default | Notes |
|---|---|---|---|
| `BENCHLINK_BRIDGE_BASE_URL` | `[bridge] base_url` | **unset** | **Primary.** bridge-daemon root; client appends `/v1`. https required unless loopback. |
| `BENCHLINK_ACCESS_TOKEN` | `[bridge] access_token` | unset | Short-lived bearer from `/auth/pair` (D3). Redacted in all output. |
| `BENCHLINK_ALLOW_PLACEHOLDER_HOST` | `[bridge] allow_placeholder_host` | `false` | Must be set before an unprovisioned Tailscale host may be called. |
| `BENCHLINK_SIDECAR_LISTEN_HOST` | `[sidecar] listen_host` | `127.0.0.1` | Loopback only: `127.0.0.1`, `localhost` or `::1` (IPv6 socket). |
| `BENCHLINK_SIDECAR_LISTEN_PORT` | `[sidecar] listen_port` | `8787` | Matches contract loopback. |
| `BENCHLINK_SIDECAR_CONFIG` | — | unset | Path to the TOML file. |
| `BENCHLINK_DEVICE_KEY_PATH` | `[bridge] device_key_path` | `~/.config/benchlink/device_ed25519.pem` | D3 Ed25519 device key (0600). `generate-key` / `pubkey` print only the public allowlist snippet. |
| `BENCHLINK_DEVICE_ID` | `[bridge] device_id` | `hostname -s` | Must match the daemon allowlist key. |

**D3 pairing (MVP round trip, 2026-11-05):** live calls pair automatically with the device key
(`auth.pair.request` → sign `challenge.nonce` → `auth.pair.complete`), keep the bearer in memory
only, attach it as `Authorization: Bearer` **and** body `access_token`, and re-pair **once** on
`auth.expired`/`auth.unpaired`. CLI: `generate-key [--force] [--tailscale-identity LOGIN]`,
`pubkey`, `pair`. Errors surface in Bridge Control as frozen classes (see `docs/MVP_ROUNDTRIP.md`).

**Placeholder policy (Larry FYI):** Intended once Douglas clears Tailscale serve (**NOT live**):
`https://inference.tail58d565.ts.net:19740` (client appends `/v1`). Sidecar starts with URL unset;
upstream calls **fail closed**. Intended host and demoted wrong-tailnet `bridge.floyds-labs.ts.net`
are refused unless `BENCHLINK_ALLOW_PLACEHOLDER_HOST=1`. Do **not** call the intended host from
Computer smoke (HTTPS serve not ready).

## OpenAPI operations wired (`{base}/v1` + path)
| operationId | Method | Path | Body schema |
|---|---|---|---|
| `health` | GET | `/health` | → `health_pong` |
| `authPair` | POST | `/auth/pair` | `auth_pair_request.schema.json` |
| `sessionCreate` | POST | `/sessions` | `session_create.schema.json` |
| `sessionAttach` | POST | `/sessions/{session_id}/attach` | `session_attach.schema.json` |
| `sessionMessage` | POST | `/sessions/{session_id}/messages` | `session_message.schema.json` |
| `sessionEvents` | GET | `/sessions/{session_id}/events` | → `session_event[]` |
| `sessionClose` | POST | `/sessions/{session_id}/close` | `session_close.schema.json` |
| `taskCreate` | POST | `/tasks` | `task_create.schema.json` |
| `taskGet` | GET | `/tasks/{task_id}` | → `task_result` |

Bearer auth (`ShortLivedBearer`) on session/task calls.

## Bridge Control UX (Amir v0.2)
Happy path after paired: **Pick → Compose/Send → Wait** (soft O1–O6 locked).
- CLI demo: `python -m benchlink_sidecar ux`
- Loopback HTML: `python -m benchlink_sidecar serve` → `http://127.0.0.1:8787/`
- Dry-run when `BENCHLINK_BRIDGE_BASE_URL` unset (offline banner; no DNS invented)

## Run
```bash
cd apps/mac-sidecar
python3 -m benchlink_sidecar config    # redacted effective config
python3 -m benchlink_sidecar dry-run   # all OpenAPI request shapes, no network
python3 -m benchlink_sidecar health    # GET {base}/v1/health (exit 3 if unset/placeholder)
python3 -m benchlink_sidecar ux        # Pick→Compose→Wait demo (dry-run when unset)
python3 -m benchlink_sidecar serve     # loopback stub + HTML shell on 127.0.0.1:8787
```

## Smoke
```bash
# from repo root; jsonschema in .venv enables schema checks
.venv/bin/python apps/mac-sidecar/smoke_sidecar.py
```
Offline only: fake upstream on `127.0.0.1:<ephemeral>`; no Tailscale; never hits
`inference.tail58d565.ts.net:19740`.

## Layout
```
apps/mac-sidecar/
  README.md  pyproject.toml  config.example.toml  .env.example  smoke_sidecar.py
  benchlink_sidecar/{__init__,__main__,config,client,server,ux}.py
```

## Soft UX locks (Floyd / Amir v0.2)
O1 no mid-task handoff · O2 no auto-launch · O3 HP ping default off · O4 session-only retention ·
O5 window chrome · O6 Bridge Control **is** the sidecar UI. Evidence: `docs/BRIDGE_CONTROL_UX_SHELL.md`.
