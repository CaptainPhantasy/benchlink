# Wireframes MVP v0.1 — Benchlink (A5-aligned)

**Owner:** Amir · Design Lead · **Date:** 2026-11-05 (America/Indianapolis)
**Product:** **Benchlink** · **Supersedes:** `wireframes-mvp-v0.md` (history)
**Arch SSoT:** `../SSoT.md` (Sofia) · **Design SSoT:** `SSoT-design.md` · **Flows:** `flows-mvp-v0.1.md`

## 1. Locks (do not reopen without Floyd)
| Source | Lock |
|--------|------|
| Floyd UX A3 | Companion **Bridge Control** surface (not a Grok.app fork) |
| Floyd UX A5 | **MVP typing in Bridge Control**; Grok.app stays Build/ACP workbench |
| Floyd UX S2 | Static roster: Floyd (default) + Ethan, Amir, Sofia, Larry, Claire, Maya, Grace; no live presence |
| D1 | Staff task → Floyd/named bot → result/error in bridge status; canary = Ethan; optional HP Floyd ping |
| D2 | Tailscale HTTP(S); no Build Session API; sidecar only if extension sealed |
| D3 | Tailscale/device-bound + short-lived tokens; no secret values in UI |
| D4 | Bridge service on inference; Mac client stub = Bridge Control; Computer-first repos |

## 2. Primary journey
1. Operator opens **Bridge Control** (Mac stub) alongside Grok.app.
2. S1: Grok.app detected + Tailscale linked + bridge service reachable → Pair (device/Tailscale identity).
3. S2: Pick target — **Floyd (default)**, **Ethan (canary)**, or named lead.
4. S3: **Type Staff task in Bridge Control** (A5); send; status queued → running → **result/error**.
5. Optional: Insert/copy result into Grok.app context; HP toggle may ping Floyd chat.
6. End task → idle. Grok.app remains the Build workbench throughout.

## 3. Screens (ASCII)

### S1 — Connect / Pair
```
┌────────────────────────────────────────────────────────────┐
│  Benchlink · Bridge Control              [beta]   [ ··· ]  │
│  Staff cable for Grok.app — not a Build provider.          │
├────────────────────────────────────────────────────────────┤
│  Grok.app     ● Detected · v0.2.50 · com.grokapp.desktop   │
│  Tailscale    ● Linked                                     │
│  Bridge svc   ● Reachable (inference via Tailscale HTTPS)  │
│                                                            │
│  Pairing: Not paired   [ Pair this Mac ]  [ How pairing ]  │
│  Auth: Tailscale / device-bound · short-lived tokens only  │
│  (Never shows secret values — Claire ops)                  │
└────────────────────────────────────────────────────────────┘
```

### S2 — Target picker (S2 lock)
```
┌────────────────────────────────────────────────────────────┐
│  ← Benchlink          Who should take this Staff task?     │
├────────────────────────────────────────────────────────────┤
│  Recommended                                               │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ Floyd · General Manager · default router             │  │
│  │                                    [ Start task ]    │  │
│  └──────────────────────────────────────────────────────┘  │
│  Canary                                                    │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ Ethan · Product · canary                             │  │
│  │                                    [ Start task ]    │  │
│  └──────────────────────────────────────────────────────┘  │
│  Leads (static)                                            │
│  [Amir] [Sofia] [Larry] [Claire] [Maya] [Grace]            │
└────────────────────────────────────────────────────────────┘
```

### S3 — Staff task (compose + status) — A5
```
┌────────────────────────────────────────────────────────────┐
│  Benchlink · Task → Floyd · GM          Tailscale ●  Svc ● │
├────────────────────────────────────────────────────────────┤
│  Target: Floyd · GM          [ Change ]   ☐ High priority  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ Compose Staff task here (Bridge Control — A5)        │  │
│  │ Attach context from Grok.app if offered…             │  │
│  └──────────────────────────────────────────────────────┘  │
│  [ Send task ]                                             │
│  Status: ○ Idle / ● Queued / ● Running / ● Result / ! Err  │
│  Result pane: (empty until result)   [ Copy ] [ Insert→Grok]│
│  No Build Session API · No secret values                   │
└────────────────────────────────────────────────────────────┘
```

### S4 — Connection / errors
```
┌────────────────────────────────────────────────────────────┐
│  Benchlink · Connection                                    │
│  ! Disconnected — Tailscale down / service unreachable /   │
│    token expired (plain reason; no token printed)          │
│  [ Retry ] [ Open Tailscale ] [ Re-pair ] [ End task ]     │
│  [ Copy diagnostics — secrets stripped ]                   │
└────────────────────────────────────────────────────────────┘
```

### S5 — Settings (bridge-only)
```
┌────────────────────────────────────────────────────────────┐
│  Benchlink settings                                        │
│  Default target: [ Floyd · GM ▾ ]                          │
│  Launch Bridge with Grok.app: [ O2 proposal ON ]           │
│  Env names (values never shown): BRIDGE_BASE_URL, …        │
│  ✗ Build providers / Session API / Remote IM               │
│  ✗ Stripe / HubSpot / brand billing                        │
└────────────────────────────────────────────────────────────┘
```

## 4. OPEN (soft)
O1 mid-task handoff (proposal: cancel+restart) · O2 launch-with-Grok · O3 HP default off · O4 keep last 20 results · O5 menu-bar vs window · O6 sidecar UI if extension sealed.

## 5. Review ask
Pack follows Floyd UX locks A3/A5/S2 + D2–D4. D1 workflow honored with Bridge Control as Staff-task compose (A5). Ping if A5 should reopen toward in-Grok.app compose.
