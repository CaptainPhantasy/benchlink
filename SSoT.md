# SSoT — Grok-App ↔ Floyd Bridge

**Status:** wire-frozen MVP architecture (2026-11-05 America/Indianapolis)  
**Owner (architecture):** Sofia · Full-Stack Engineer  
**Authority:** Floyd GM freeze D1–D4 (Ethan PRD §7)

## Frozen decisions
| ID | Decision |
|----|----------|
| D1 | Staff task → Floyd/named bot → result in bridge status (canary=Ethan) |
| D2 | Tailscale HTTP(S); no Build Session API; sidecar only if extension sealed — **LOCKED: extension sealed → sidecar-only** ([spike](docs/MAC_EXTENSION_SPIKE.md)) |
| D3 | Tailscale/device-bound + short-lived tokens; usable secrets (Claire) |
| D4 | Bridge service on inference; Mac client stub; Computer-first repos |

## System context
```text
[Grok.app] --Mac stub--> [Tailscale HTTPS] --> [bridge-daemon @ inference]
                                                    |
                                              [staff-adapter] --> Floyd / Ethan(canary) / staff bots
```

## Components
| Component | Path |
|-----------|------|
| Protocol | `packages/bridge-protocol/` (schemas + openapi.v1.json) |
| Daemon | `apps/bridge-daemon/` (MVP round trip; runtime on inference; [config below](#bridge-daemon-config-contract)) |
| Adapter | `apps/staff-adapter/` (Transport: webhook\|fake; ResultSource: drive_folder\|local_folder\|http_callback) |
| Mac sidecar | `apps/mac-sidecar/` (Bridge Control UX shell + client; loopback `127.0.0.1:8787/v1` → Tailscale HTTPS; [scaffold](docs/MAC_SIDECAR_SCAFFOLD.md), [UX shell](docs/BRIDGE_CONTROL_UX_SHELL.md)) |
| Tests | `tests/` (pytest, loopback) |
| Smoke | `tools/smoke/` (`smoke_roundtrip.py` e2e + `apps/mac-sidecar/smoke_sidecar.py`, `apps/bridge-daemon/smoke_daemon.py`) |
| Contracts | `docs/api-contracts-mvp.md` (**0.2.0-mvp**) |

## Mac sidecar config contract
Precedence: env > TOML (`BENCHLINK_SIDECAR_CONFIG`) > default. Examples: `apps/mac-sidecar/.env.example`, `apps/mac-sidecar/config.example.toml`.

| Env var | TOML key | Default | Notes |
|---------|----------|---------|-------|
| `BENCHLINK_BRIDGE_BASE_URL` | `[bridge] base_url` | **unset** | **Primary.** bridge-daemon root; client appends `/v1`. https unless loopback. Port 63301 (Build Session API) rejected. |
| `BENCHLINK_ACCESS_TOKEN` | `[bridge] access_token` | unset | Short-lived bearer (D3); always redacted. |
| `BENCHLINK_ALLOW_PLACEHOLDER_HOST` | `[bridge] allow_placeholder_host` | `false` | Gate for calling an unprovisioned Tailscale host. |
| `BENCHLINK_SIDECAR_LISTEN_HOST` | `[sidecar] listen_host` | `127.0.0.1` | Loopback only. |
| `BENCHLINK_SIDECAR_LISTEN_PORT` | `[sidecar] listen_port` | `8787` | Contract loopback `http://127.0.0.1:8787/v1`. |
| `BENCHLINK_SIDECAR_CONFIG` | — | unset | Path to TOML file. |

**Placeholder policy (Larry FYI 2026-11-05):** Intended once Douglas clears Tailscale serve (still **NOT live**): `https://inference.tail58d565.ts.net:19740` (client appends `/v1`). Sidecar starts with URL unset; upstream calls fail closed (CLI exit 3; stub `503 error{code:offline}`); intended host **and** demoted wrong-tailnet `bridge.floyds-labs.ts.net` refused unless `BENCHLINK_ALLOW_PLACEHOLDER_HOST=1`. Do **not** call the intended host from Computer smoke (HTTPS serve not ready; loopback `:19740` health stub is inference-local only). No invented live DNS.

## Bridge-daemon config contract
Edit home: Computer. Runtime later: `/srv/organized/floyds-labs-staff/grok-app-floyd-bridge/`.

| Env var | Default | Notes |
|---------|---------|-------|
| `BENCHLINK_BRIDGE_LISTEN_HOST` | `127.0.0.1` | Computer smoke binds loopback |
| `BENCHLINK_BRIDGE_LISTEN_PORT` | `8790` | Avoids sidecar `8787`; rejects `63301` |
| `BENCHLINK_BRIDGE_PUBLIC_BASE_URL` | **unset** | Intended: `https://inference.tail58d565.ts.net:19740` (unset until serve live) |
| `BENCHLINK_ALLOW_PLACEHOLDER_HOST` | `false` | Same placeholder policy as sidecar |
| `BENCHLINK_LOG_LEVEL` | `INFO` | Logs are redacted (no Authorization/access_token/proof/challenge; token = sha256[:6]) |
| `BENCHLINK_DEVICE_ALLOWLIST_PATH` | `~/.config/benchlink/device_allowlist.json` | D3 enrollment `{device_id:{public_key_b64url, tailscale_identity?, scopes?}}`, outside repo |
| `BENCHLINK_REQUIRE_TAILSCALE_IDENTITY` | `false` | **Must be `1` in prod** (`Tailscale-User-Login` from tailscale serve); paired nodes must be untagged, user-owned (no tag identity in MVP) |
| `BENCHLINK_ACCESS_TTL_SECONDS` | `900` | Short-lived bearer; no refresh (client re-pairs) |
| `BENCHLINK_ADAPTER_TRANSPORT` | `webhook` | `webhook` \| `fake` (smoke only) |
| `BENCHLINK_FAKE_TRANSPORT_MODE` | `echo` | fake only: echo\|fail\|silent\|offline\|reject |
| `BENCHLINK_TARGET_<KEY>_WEBHOOK_URL` | unset | Routine webhook per target (FLOYD default, ETHAN canary); unset → `offline` |
| `BENCHLINK_TARGET_<KEY>_SENDER_KEY` | unset | Routine sender key (secret; inference env only) |
| `BENCHLINK_WEBHOOK_SENDER_KEY_HEADER` / `_SCHEME` | `Authorization` / `Bearer` | **TBD** routine sender-key convention |
| `BENCHLINK_WEBHOOK_TIMEOUT_S` | `10` | Dispatch timeout → `timeout` |
| `BENCHLINK_TASK_TIMEOUT_S` | `600` | No final result → `task.result{failed, timeout}` |
| `BENCHLINK_RESULT_SOURCE` | `drive_folder` | `drive_folder` \| `local_folder` \| `http_callback` (GM: Drive fallback default) |
| `BENCHLINK_RESULTS_DRIVE_FOLDER_ID` | unset | Bot writes `<task_id>.json`; unset → `offline` |
| `BENCHLINK_DRIVE_SA_KEY_PATH` | unset | Dedicated Drive service-account JSON key (inference, outside repo, mode 0600, rotated every 90 days); `drive_folder` refuses to start if missing/looser; scope `drive.readonly` |
| `BENCHLINK_RESULTS_LOCAL_DIR` | unset | `local_folder` (tests/smoke) |
| `BENCHLINK_RESULTS_POLL_INTERVAL_S` | `5` (`0` local/callback) | Result poll throttle |
| `BENCHLINK_CALLBACK_BASE_URL` / `_TOKEN_TTL_S` | derived / `900` | `http_callback` only |

Sidecar adds `BENCHLINK_DEVICE_KEY_PATH` (default `~/.config/benchlink/device_ed25519.pem`) and `BENCHLINK_DEVICE_ID` (default `hostname -s`).

MVP round trip implemented (health, D3 pair, sessions, tasks, events; messages 501). Run + go-live: [docs/MVP_ROUNDTRIP.md](docs/MVP_ROUNDTRIP.md).

## Paths
- Computer repo: `/workspace/grok-app-floyd-bridge/`
- Inference runtime parent: `/srv/organized/floyds-labs-staff/grok-app-floyd-bridge/`
- SDLC: mcp-gateway → sdlc-core-cloud-factory-mcp

## Change log
| Date | Change | Author |
|------|--------|--------|
| 2026-11-05 | Initial outline | Sofia |
| 2026-11-05 | PRD/ops absorb | Sofia |
| 2026-11-05 | Wire-freeze 0.2.0-mvp schemas + OpenAPI after D1–D4 freeze | Sofia |
| 2026-11-05 | D2 locked sidecar-only (extension sealed); scaffold `apps/mac-sidecar/` + config contract `BENCHLINK_BRIDGE_BASE_URL` (Floyd GM order) | Sofia |
| 2026-11-05 | Bridge Control UX shell (Pick→Compose→Wait) + `apps/bridge-daemon` skeleton; Larry hostname FYI → intended `inference.tail58d565.ts.net:19740` (unset-until-serve); demote `floyds-labs.ts.net` | Grok Bot |
| 2026-11-05 | MVP round trip: daemon tasks/sessions/D3 pairing (Ed25519, Claire spec) + staff-adapter (webhook/fake; ResultSource drive_folder default per GM, local_folder, http_callback) + sidecar device-key pairing; B1–B4 classes; pytest + `smoke_roundtrip.py`; [MVP_ROUNDTRIP.md](docs/MVP_ROUNDTRIP.md) | Sofia |
| 2026-11-05 | Drive results credential → dedicated service account `BENCHLINK_DRIVE_SA_KEY_PATH` (0600, drive.readonly, 90-day rotation; Claire); prod Tailscale identity = untagged user-owned nodes | Sofia |
