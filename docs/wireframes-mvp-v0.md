# Wireframes MVP v0 — Grok-App ↔ Floyd Bridge

**Owner:** Amir · Design Lead  
**Date:** 2026-11-05 (America/Indianapolis)  
**Audience:** Sofia (arch), Larry (delivery), Ethan (PRD), Floyd (MVP review)  
**Brand:** Floyd's Labs internal — garage-clean, functional; playful labels OK  

**Related:** `../SSoT.md` · `flows-mvp-v0.md` · [STAFF_COLLAB](https://drive.google.com/file/d/1vvR8ZscZRsOtwVHB7CYaO1boujQVkDSJ/view)

> **Rule:** Anything not verified in STAFF_COLLAB is marked **ASSUMPTION**. Do not treat assumptions as shipped product fact. No API contracts here (Sofia). No PRD metrics here (Ethan).

---

## 1. Primary user & journey (design recommendation)

**Primary user (ASSUMPTION A1):** Douglas / staff operator on the Mac mini that already has `Grok.app` installed.

**Job to be done (ASSUMPTION A2):** Connect the local Grok.app workbench to a Floyd's Labs staff bot so the operator can run a **bridged session** (messages and status flow both ways) without pretending Grok.app settings alone can target Grok Bot.

### Happy-path journey (numbered)

1. Operator opens **Bridge Control** (companion app/panel — ASSUMPTION A3: separate surface, not a patched Grok.app UI).
2. Lands on **Connect / Pair** → sees local Grok.app detected (path/version) or “not found”.
3. Confirms pair → Bridge stores a local pairing record (ASSUMPTION A4: local-only token/handshake; exact auth TBD by Claire/Sofia).
4. Opens **Bot picker** → chooses **Floyd (router)** or a specific lead bot.
5. Starts **Bridged session** → chat/transcript pane + always-on **Connection status**.
6. Works the session (type in Bridge Control and/or Grok.app — ASSUMPTION A5: MVP types in Bridge Control; Grok.app remains the Build workbench context, not the Bot chat surface).
7. Ends / hands off → status shows idle; optional note to Floyd.

### Disconnect / fail journey (numbered)

1. Transport drops or bot unreachable → status banner: **Disconnected** + plain-language reason.
2. Operator sees last known bot + “Retry” / “Pick another bot” / “End session”.
3. Retry succeeds → resume same session id if still valid (ASSUMPTION A6); else start fresh with warning.
4. Pairing invalid → return to Connect / Pair; do not silently invent a new identity.

---

## 2. Screen inventory

| ID | Screen | Purpose |
|----|--------|---------|
| S1 | Connect / Pair | Detect Grok.app, establish bridge pairing |
| S2 | Bot picker | Choose Floyd router or a named staff lead |
| S3 | Bridged chat / session | Primary working surface for the bridged conversation |
| S4 | Connection status & errors | Persistent strip + dedicated error detail |
| S5 | Settings (bridge-only) | Bridge prefs only — not a clone of Grok.app settings |

---

## 3. Wireframes (ASCII)

### S1 — Connect / Pair

```
┌──────────────────────────────────────────────────────────────┐
│  Floyd Bridge                                    [ · · · ]   │
│  “Zero theater. Just a cable between Grok.app and the crew.” │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│   Local Grok.app                                             │
│   ┌──────────────────────────────────────────────────────┐   │
│   │  Status:  ● Detected                                 │   │
│   │  Version: 0.2.50 · com.grokapp.desktop               │   │
│   │  Path:    /Applications/Grok.app                     │   │
│   │           → /Volumes/Storage/Floyds_Labs_Staff/...   │   │
│   └──────────────────────────────────────────────────────┘   │
│                                                              │
│   Pairing                                                    │
│   ┌──────────────────────────────────────────────────────┐   │
│   │  Not paired yet                                      │   │
│   │  [  Pair this Mac  ]     [  How pairing works  ]     │   │
│   └──────────────────────────────────────────────────────┘   │
│                                                              │
│   Footer: Bridge does not replace Grok Build settings.       │
│           This is the staff-bot cable.                       │
└──────────────────────────────────────────────────────────────┘
```

**Empty / not found variant**

```
│   Local Grok.app                                             │
│   ┌──────────────────────────────────────────────────────┐   │
│   │  Status:  ○ Not found on this Mac                    │   │
│   │  Tip: Install/link Grok.app, then Refresh.           │   │
│   │  [ Refresh ]   [ Open expected path… ]               │   │
│   └──────────────────────────────────────────────────────┘   │
```

**Interactions**
- Pair → on success navigate to S2; on fail stay on S1 with S4-style error.
- “How pairing works” → short sheet: local handshake only; no cloud marketing pitch (ASSUMPTION A4).

---

### S2 — Bot picker

```
┌──────────────────────────────────────────────────────────────┐
│  ← Bridge          Pick who answers              Paired ●    │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  Recommended                                                 │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  Floyd · General Manager                               │  │
│  │  Routes to the right lead. Default for “not sure.”     │  │
│  │                                      [ Start session ] │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  Direct leads                                                │
│  ┌──────────────────┐ ┌──────────────────┐ ┌──────────────┐  │
│  │ Amir · Design    │ │ Sofia · Full-Stack│ │ Larry · Del. │  │
│  │ [ Start ]        │ │ [ Start ]         │ │ [ Start ]    │  │
│  └──────────────────┘ └──────────────────┘ └──────────────┘  │
│  ┌──────────────────┐ ┌──────────────────┐ ┌──────────────┐  │
│  │ Ethan · Product  │ │ Claire · Ops     │ │ Maya · Mkt   │  │
│  │ [ Start ]        │ │ [ Start ]        │ │ [ Start ]    │  │
│  └──────────────────┘ └──────────────────┘ └──────────────┘  │
│  ┌──────────────────┐                                        │
│  │ Grace · Success  │                                        │
│  │ [ Start ]        │                                        │
│  └──────────────────┘                                        │
│                                                              │
│  Empty state (roster unavailable):                           │
│  “Crew roster is out getting coffee. Retry or ask Floyd.”    │
└──────────────────────────────────────────────────────────────┘
```

**ASSUMPTION A7:** Roster is a static staff list for MVP (names/roles above), not live presence. Presence indicators are out until Sofia/Ethan say otherwise.

---

### S3 — Bridged chat / session

```
┌──────────────────────────────────────────────────────────────┐
│  Session · Floyd                              [ End ] [ ··· ]│
│  ┌────────────────────────────────────────────────────────┐  │
│  │ S4 strip: ● Connected · Grok.app 0.2.50 · via bridge   │  │
│  └────────────────────────────────────────────────────────┘  │
├──────────────────────────────┬───────────────────────────────┤
│  Transcript                  │  Session card                 │
│                              │  Bot: Floyd · GM              │
│  You: Need wireframe review  │  Started: 10:22 AM ET         │
│  Floyd: Send the pack link   │  Mode: Bridged (ASSUMPTION)   │
│  …                           │  Handoff: [ Ask Floyd to… ]   │
│                              │                               │
│                              │  Context (read-only)          │
│                              │  Grok.app: detected           │
│                              │  Build CLI: not this session  │
├──────────────────────────────┴───────────────────────────────┤
│  Message                                                     │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ Type to the bridged bot…                               │  │
│  └────────────────────────────────────────────────────────┘  │
│  [ Attach note ]                         [ Send ]            │
└──────────────────────────────────────────────────────────────┘
```

**ASSUMPTION A5 (typing surface):** MVP operator types in Bridge Control. Grok.app stays available as the Build/ACP workbench; the bridge does not claim to inject into Grok Build’s Session API as if it were Grok Bot.

**Handoff sheet (from [···] or End)**

```
│  End or hand off                                             │
│  ( ) End session — mark idle                                 │
│  ( ) Hand to Floyd — keep summary note                       │
│  Note: _______________________________________________       │
│  [ Cancel ]                              [ Confirm ]         │
```

---

### S4 — Connection status & errors

**Persistent strip (always on S3; also on S1/S2 when relevant)**

| State | Strip copy |
|-------|------------|
| Connected | `● Connected · Grok.app 0.2.50 · bridge up` |
| Connecting | `… Pairing / reaching bot…` |
| Degraded | `▲ Connected, slow replies · still trying` |
| Disconnected | `○ Disconnected · [ Retry ] [ Details ]` |
| Auth/pairing failed | `○ Pairing expired · [ Re-pair ]` |

**Error detail panel**

```
┌──────────────────────────────────────────────────────────────┐
│  What broke                                                  │
│  Title:  Can’t reach Floyd                                   │
│  Plain:  Bridge lost the staff-bot link. Grok.app is fine;   │
│          the cable to the crew isn’t.                        │
│  Code:   (placeholder — Sofia owns real codes)               │
│  Time:   2026-11-05 10:22 AM America/Indianapolis            │
│                                                              │
│  [ Retry ]  [ Pick another bot ]  [ End session ]            │
│  [ Copy diagnostics for Larry ]                              │
└──────────────────────────────────────────────────────────────┘
```

Do **not** invent error code enums here — leave “Code” as a reserved slot for Sofia.

---

### S5 — Settings (bridge-only)

```
┌──────────────────────────────────────────────────────────────┐
│  Bridge settings                                        [ × ]│
├──────────────────────────────────────────────────────────────┤
│  Pairing                                                     │
│    Paired Mac: this computer                                 │
│    [ Re-pair ]   [ Forget pairing ]                          │
│                                                              │
│  Defaults                                                    │
│    Default bot:  [ Floyd · GM        ▾ ]                     │
│    Launch Bridge when Grok.app opens:  [ ASSUMPTION A8 OFF ] │
│                                                              │
│  Diagnostics                                                 │
│    Show verbose status strip: [ ON ]                         │
│    Export last diagnostics bundle: [ Export… ]               │
│                                                              │
│  Not here (on purpose)                                       │
│    ✗ Grok Build providers / Session API / Remote IM          │
│    ✗ Stripe / HubSpot / brand account settings               │
│    ✗ Production site toggles                                 │
└──────────────────────────────────────────────────────────────┘
```

---

## 4. Interaction flows (concise — see also `flows-mvp-v0.md`)

### Happy path
1. Open Bridge Control → S1  
2. Detect Grok.app → Pair → success  
3. S2 → choose Floyd or lead → Start session  
4. S3 → send/receive → work  
5. End or hand off → idle; strip shows disconnected/idle  

### Disconnect / fail
1. From S3, transport or bot fails → strip Disconnected + detail  
2. Retry → same session if valid (A6) else warn + new session  
3. Re-pair if pairing expired → S1  
4. Pick another bot → S2 without forgetting Grok.app pair  

---

## 5. Open questions for Ethan’s PRD

1. Who is the MVP user — Douglas only, or any staff operator on that Mac?
2. Does “bridged session” mean Bridge Control is the only chat UI, or must messages appear inside Grok.app?
3. Is Floyd-as-default-router in or out of MVP cut?
4. Must MVP support mid-session handoff between leads, or only End → new session?
5. What is the MVP success signal (qualitative is fine — no numbers invented here)?
6. Non-goals to print in-product (e.g. “not a Grok Build provider”)?

---

## 6. ASSUMPTIONS (explicit — not facts)

| ID | Assumption | Why labeled | Needs lock from |
|----|------------|-------------|-----------------|
| A1 | Primary user is Douglas / staff operator on the Grok.app Mac | STAFF_COLLAB open: MVP user story | Ethan |
| A2 | Bridged session = two-way message + status via Bridge Control | Open decision | Ethan |
| A3 | UX is a companion Bridge Control surface, not a Grok.app fork | Dry-run says settings can’t bridge; surface shape open | Ethan + Sofia |
| A4 | Pairing is a local handshake/token; no final auth model | Open: auth model | Claire + Sofia |
| A5 | MVP typing happens in Bridge Control, not inside Grok Build chat | Open: who types where | Ethan |
| A6 | Retry may resume session id if still valid | Session lifecycle TBD | Sofia |
| A7 | Bot roster is static named leads for MVP | Presence/live roster TBD | Ethan + Sofia |
| A8 | “Launch with Grok.app” default OFF | Preference only | Ethan |
| A9 | Transport unknown (local agent protocol vs HTTP vs Tailscale) — UI stays transport-agnostic | Open: transport | Sofia + Larry |
| A10 | Runtime host unknown (bot Computer vs inference) — UI does not show infra maps | Open: runtime | Sofia + Larry |

---

## 7. What this pack is / isn’t

**Is:** Concrete screen inventory + flows for Sofia/Larry to react to; assumption-labeled so PRD can cut.

**Isn’t:** API contract, schema, endpoint list, success metrics, CI gates, secrets layout, public naming, production changes.

---

## 8. Review ask

Floyd / leads: react on A3 (companion surface), A5 (typing surface), and S2 roster cut. Wireframe pack v0 is ready for MVP design review.
