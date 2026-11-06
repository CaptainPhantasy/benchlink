# Wireframes MVP v0.2 — Benchlink

**Owner:** Amir · Design Lead · **Date:** 2026-11-05 (America/Indianapolis)  
**Product:** **Benchlink** · **Supersedes:** `wireframes-mvp-v0.1.md`  
**Arch:** `../SSoT.md` · **Design SSoT:** `SSoT-design.md` · **Flows:** `flows-mvp-v0.2.md`  
**Contracts:** Sofia **0.2.0-mvp** — screen copy maps to `task.create` / `task.result`

## 1. Locked (Floyd GM — do not reopen)
| ID | Decision |
|----|----------|
| A3 | Companion **Bridge Control** Mac stub (not a Grok.app fork) — APPROVED |
| A5 | MVP typing in Bridge Control; Grok.app stays Build/ACP only — APPROVED |
| S2 / A7 | Static roster: Floyd (default) + Ethan, Amir, Sofia, Larry, Claire, Maya, Grace; no live presence — APPROVED |
| D1 | Staff task → Floyd/named → result/error in **bridge status**; canary = Ethan; optional HP Floyd chat ping |
| D2 | Transport: Tailscale HTTP(S); no Build Session API; **sidecar** (extension sealed) |
| D3 | Tailscale/device-bound + short-lived tokens; no secret values in UI |
| D4 | Inference bridge service + Mac Bridge Control; Computer-first repos |

## 2. Operator path (≤3 steps after paired)
```
[1 Pick target] → [2 Compose + Send = task.create] → [3 Wait for bridge status = task.result | error]
```
One-time: Connect / Pair (S1). Not counted in the three.

## 3. Screens (transport-agnostic chrome)

Primary chrome says **Connected / Disconnected** and **Bridge status**. Transport details (Tailscale, hostname) live under Connection / Settings — not in the happy-path strip.

### S1 — Connect / Pair (setup)
```
┌────────────────────────────────────────────────────────────┐
│  Benchlink · Bridge Control                    [beta] [···]│
│  Staff tasks for Floyd’s Labs — Grok.app stays Build/ACP.  │
├────────────────────────────────────────────────────────────┤
│  Grok.app     ● Detected · v0.2.50                         │
│  Bridge       ● Connected                                  │
│  Pairing      ○ Not paired          [ Pair this Mac ]      │
│  Auth: device-bound · short-lived tokens (values never shown)│
└────────────────────────────────────────────────────────────┘
```

### S2 — Pick target (step 1)
```
┌────────────────────────────────────────────────────────────┐
│  Benchlink · Who should take this Staff task?   Bridge ●   │
├────────────────────────────────────────────────────────────┤
│  Recommended                                               │
│  ┌ Floyd · General Manager · default router  [ Start ] ┐  │
│  └─────────────────────────────────────────────────────┘  │
│  Canary                                                    │
│  ┌ Ethan · Product · canary                  [ Start ] ┐  │
│  └─────────────────────────────────────────────────────┘  │
│  Leads (static)  [Amir] [Sofia] [Larry] [Claire] [Maya] [Grace] │
└────────────────────────────────────────────────────────────┘
```

### S3 — Compose + status (steps 2–3) — A5
```
┌────────────────────────────────────────────────────────────┐
│  Benchlink · Task → Floyd · GM              Bridge ●       │
├────────────────────────────────────────────────────────────┤
│  Target: Floyd · GM   [ Change ]    ☐ High priority (Floyd chat)│
│  ┌─ Compose Staff task (task.create) ───────────────────┐  │
│  │ Type here in Bridge Control…                         │  │
│  │ Optional: attach / paste context from Grok.app       │  │
│  └──────────────────────────────────────────────────────┘  │
│  [ Send task ]  → creates task.create                      │
│                                                            │
│  Bridge status                                             │
│  ○ Idle · ● Queued · ● Running · ● Result · ! Error        │
│  ┌─ task.result ────────────────────────────────────────┐  │
│  │ summary…                                             │  │
│  │ artifact_links…                                      │  │
│  └──────────────────────────────────────────────────────┘  │
│  [ Copy ]  [ Insert → Grok.app ]  [ End task ]             │
└────────────────────────────────────────────────────────────┘
```

### S4 — Connection / errors
```
┌────────────────────────────────────────────────────────────┐
│  Benchlink · Connection                                    │
│  ! Disconnected — plain reason (no tokens printed)         │
│  [ Retry ]  [ Re-pair ]  [ Open connection help ]          │
│  Details (collapsed): transport · hostname · last error    │
│  [ Copy diagnostics — secrets stripped ]                   │
└────────────────────────────────────────────────────────────┘
```

### S5 — Settings (bridge-only)
```
┌────────────────────────────────────────────────────────────┐
│  Benchlink settings                                        │
│  Default target: [ Floyd · GM ▾ ]                          │
│  Launch Bridge with Grok.app: [ O2 proposal ]              │
│  Env names only (values never shown)                       │
│  ✗ Build providers / Session API / Remote IM as Bot chat   │
│  ✗ Stripe / HubSpot / cross-brand billing                  │
└────────────────────────────────────────────────────────────┘
```

## 4. Contract label map (Sofia 0.2.0-mvp)
| UI | Contract |
|----|----------|
| Pair this Mac | `auth.pair.*` / `auth.session.token` |
| Connected strip | `health.ping|pong` |
| Start / session with target | `session.create` (target floyd \| ethan \| …) |
| Send task | `task.create` |
| Bridge status → Result | `task.result` (`summary`, `artifact_links`) |
| Error banner | `error` |

## 5. Soft opens — locked for MVP (Floyd GM)
| ID | Decision |
|----|----------|
| O1 | Mid-task handoff **out** — end session → pick another bot on S2 |
| O2 | Launch Bridge with Grok.app: **OFF** (manual open) |
| O3 | HP Floyd ping: **OFF** by default; operator may enable per task / for failures |
| O4 | Result retention: last session transcript in UI only; no durable store |
| O5 | Chrome: **window** Bridge Control (not menu-bar-only) |
| O6 | Sidecar UI: **yes** — Bridge Control *is* the sidecar UI |

## 6. Status
**MVP UX accepted** (Floyd GM). Pack = wireframes-mvp-v0.2. Further rev only if freeze reopens.
