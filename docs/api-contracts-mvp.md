# API contracts — MVP wire-freeze

**Project:** Grok-App ↔ Floyd Bridge  
**Version:** **0.2.0-mvp** (wire-frozen)  
**Date:** 2026-11-05 (America/Indianapolis)  
**Author:** Sofia · Full-Stack Engineer  
**Authority:** Floyd GM freeze of D1–D4 from Ethan PRD §7

## Frozen decisions
| ID | Freeze |
|----|--------|
| D1 | Staff task → Floyd / named bot → result in **bridge status** view; product canary = **Ethan** |
| D2 | Transport = **Tailscale HTTP(S)**; **no** Grok Build Session API hijack; sidecar only if extension surface sealed |
| D3 | Auth = Tailscale/device-bound identity + **short-lived** tokens; secrets **usable** at runtime (Claire) |
| D4 | Runtime = bridge service on **inference** under staff parent; **Mac client stub**; Computer-first repos |

## Transport (D2)
| Role | Endpoint |
|------|----------|
| Inference service (canonical) | Intended once Douglas clears Tailscale serve (Larry FYI; **NOT live**): `https://inference.tail58d565.ts.net:19740/v1`. Historical/wrong-tailnet logical name `bridge.floyds-labs.ts.net` demoted. |
| Mac stub / sealed-extension sidecar | `http://127.0.0.1:8787/v1` loopback → Tailscale upstream |
| Forbidden | Grok.app built-in Session API / Remote IM / providers aimed at Grok Build |

Paths: see `packages/bridge-protocol/openapi.v1.json`.

## Runtime (D4)
| Piece | Location |
|-------|----------|
| Repo / edit / commit | Bot Computer: `/workspace/grok-app-floyd-bridge/` |
| Always-on daemon | inference: `/srv/organized/floyds-labs-staff/grok-app-floyd-bridge/` |
| Mac UX stub | Mac mini beside Grok.app (client only) |

## Auth (D3)
1. Pair with device id + optional Tailscale identity → challenge → proof.  
2. Issue `auth.session.token` with `token_type: short_lived` (usable secret; Vault/host env; never hash-only).  
3. All session/task writes require Bearer token. Fail closed (`auth.denied` / `auth.expired` / `auth.unpaired`).

## D1 task flow
`session.create` (target `floyd` or named bot, canary `ethan`) → `task.create` → poll `GET /tasks/{id}` / events → `task.result` with `summary` + `artifact_links` for **bridge status**. Optional high-priority also mirrors to Floyd chat via `reply_destination`.

Known agent ids (routing hints, not secrets):
- Floyd: `3e5e1310-2535-4281-bcdc-f12f08d78f75`
- Ethan (canary): `bd8bb2b0-1df6-42f9-92ee-634c17736ccd`

## Message catalog (schemas on disk)
Located under `packages/bridge-protocol/schemas/`:
`health.ping|pong`, `auth.pair.*`, `auth.session.token`, `session.*`, `task.create`, `task.result`, `error`.

## Explicit non-contract
- Grok Build Session API / Remote IM settings bridges  
- Port MCP as SDLC factory  
- Production site edits; Stripe/HubSpot/Zapier billing; PSI/IDC client data  

## MVP-ready gate (architecture) — status
- [x] Frozen D1–D4 reflected in this doc  
- [x] Tailscale HTTPS path map in OpenAPI  
- [x] JSON Schema checked into `packages/bridge-protocol/schemas`  
- [x] Offline fixtures + validator under `tools/smoke`  
- [ ] Live Tailscale hostname provisioned (Larry) — **does not block contract MVP-ready**; **deploy blocker** (sidecar keeps `BENCHLINK_BRIDGE_BASE_URL` unset until then)  
- [x] Mac extension-surface spike — **done: sealed** → D2 sidecar-only ([MAC_EXTENSION_SPIKE.md](MAC_EXTENSION_SPIKE.md)); scaffold `apps/mac-sidecar/` ([MAC_SIDECAR_SCAFFOLD.md](MAC_SIDECAR_SCAFFOLD.md)), upstream via `BENCHLINK_BRIDGE_BASE_URL`  

**Contract MVP-ready:** YES (0.2.0-mvp) — schemas + OpenAPI + fixtures validated offline.
