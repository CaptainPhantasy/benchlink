# Bridge Control UX shell + bridge-daemon skeleton — evidence

**Date:** 2026-11-05 (America/Indianapolis)  
**Authority:** Floyd GM (scaffold accepted → UX shell + daemon skeleton); Amir v0.2 Pick→Compose/Send→Wait; Larry DNS FYI  
**Contract:** 0.2.0-mvp (frozen; not reopened)

## Summary
Shipped on Computer (`/workspace/grok-app-floyd-bridge/`):

1. **Bridge Control UX shell** in `apps/mac-sidecar/` — Amir v0.2 happy path **Pick → Compose/Send → Wait**, soft O1–O6 locked.
2. **OpenAPI client wiring** expanded to all 0.2.0-mvp paths (auth pair, session attach/messages/events/close, plus existing health/sessions/tasks).
3. **`apps/bridge-daemon` skeleton** — schema-valid `GET /v1/health` → `health.pong` (`runtime: inference`); other paths `501` stubs.
4. **Fail-closed base URL** with Larry’s intended-but-unset host (no live DNS invented; Computer smoke never calls it).

## Larry DNS FYI (incorporated)
| Item | Value |
|------|-------|
| Intended (once Douglas clears Tailscale serve; **NOT live**) | `https://inference.tail58d565.ts.net:19740` (client appends `/v1`) |
| Demoted / wrong-tailnet | `bridge.floyds-labs.ts.net` — still refused as placeholder; removed from primary examples |
| Computer smoke | Does **not** call intended host (HTTPS serve not ready; inference loopback `:19740` is local to inference only) |
| Fail-closed | URL unset **or** either placeholder host → refuse live calls unless `BENCHLINK_ALLOW_PLACEHOLDER_HOST=1` |

OpenAPI `servers[]` still lists the historical logical name from Sofia’s freeze; deploy/config docs now point at Larry’s intended host. Contract wire shapes unchanged.

## Amir alignment
| Soft lock | Honored how |
|-----------|-------------|
| O1 no mid-task handoff | `pick()` raises in WAIT; `end_task()` → PICK |
| O2 no auto-launch | Manual `ux` / `serve` only |
| O3 HP ping default off | `high_priority=False`; `reply_destination=bridge_status`; `priority=normal` |
| O4 session-only retention | In-memory `SessionTranscript`; cleared on `end_task()` |
| O5 window chrome | CLI TUI + loopback HTML at `/` |
| O6 sidecar is Bridge Control | UX lives in `benchlink_sidecar/ux.py` + `serve` HTML |
| S2 roster | Floyd (default, known id), Ethan (canary, known id), Amir/Sofia/Larry/Claire/Maya/Grace name keys only |

## File tree (new/changed)
```
apps/mac-sidecar/
  benchlink_sidecar/
    __init__.py          # 0.2.0-ux-shell
    __main__.py          # + ux command; dry-run lists all ops
    client.py            # + authPair, sessionAttach/Message/Events/Close
    config.py            # PLACEHOLDER_BASE_URL → inference.tail58d565.ts.net:19740
    server.py            # + HTML shell /, POST /_sidecar/ux/run
    ux.py                # NEW — Pick→Compose→Wait state machine
  smoke_sidecar.py       # extended (38 checks)
  README.md  .env.example  config.example.toml
apps/bridge-daemon/
  benchlink_daemon/
    __init__.py  __main__.py  config.py  server.py
  smoke_daemon.py        # 12 checks
  pyproject.toml  README.md  .env.example
docs/BRIDGE_CONTROL_UX_SHELL.md   # this file
SSoT.md                           # daemon + hostname FYI
```

## Smoke (EXIT 0)
```bash
cd /workspace/grok-app-floyd-bridge
.venv/bin/python apps/mac-sidecar/smoke_sidecar.py
# → 38/38 passed  EXIT 0

.venv/bin/python apps/bridge-daemon/smoke_daemon.py
# → 12/12 passed  EXIT 0
```

Covered: fail-closed unset URL; refuse intended + demoted placeholders; reject port 63301; dry-run schema validation for auth/session/task bodies; UX Pick→Compose→Wait + O1/O3; HTML shell; daemon health.pong schema-valid on ephemeral loopback; daemon 501 stubs.

## OpenAPI operationIds wired (mac-sidecar client)
`health`, `authPair`, `sessionCreate`, `sessionAttach`, `sessionMessage`, `sessionEvents`, `sessionClose`, `taskCreate`, `taskGet`

## Gaps / next
- Daemon session/task handlers still **501 stubs** (skeleton only).
- Live Tailscale HTTPS serve on `inference.tail58d565.ts.net:19740` waits on Douglas clear (Larry) — **deploy blocker**, not contract reopener.
- Pairing UX (S1) is dry-run body only; no real device proof yet.
- staff-adapter not started this pass.
- OpenAPI `servers[]` historical hostname vs Larry intended host — document-only drift until Sofia/Larry refresh servers list.

## Non-goals honored
No invented live DNS · no real Tailscale calls from Computer smoke · no Build Session API · no production site edits · stdlib-first (jsonschema only in `.venv` for smoke).
