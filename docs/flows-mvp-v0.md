# Flows MVP v0 — Grok-App ↔ Floyd Bridge

**Owner:** Amir · Design Lead  
**Date:** 2026-11-05 (America/Indianapolis)  
**Companion:** `wireframes-mvp-v0.md` · `../SSoT.md`

> Flows are UX-level. Transport, auth, and runtime are **ASSUMPTIONS** (see wireframe pack A4/A9/A10). No API contracts.

---

## Connect / Pair

```mermaid
flowchart TD
  A[Open Bridge Control] --> B[S1 Connect / Pair]
  B --> C{Grok.app detected?}
  C -->|No| D[Show Not found + Refresh]
  D --> C
  C -->|Yes| E[Show version + path]
  E --> F{Operator pairs?}
  F -->|Cancel| B
  F -->|Pair| G[Local pairing handshake ASSUMPTION A4]
  G --> H{Pair OK?}
  H -->|No| I[S4 Pairing failed / Re-pair]
  I --> B
  H -->|Yes| J[S2 Bot picker]
```

---

## Bridged session

```mermaid
flowchart TD
  J[S2 Bot picker] --> K{Choose bot}
  K -->|Floyd router| L[Start session → S3]
  K -->|Named lead| L
  L --> M[S4 strip: Connected]
  M --> N[Operator sends message in Bridge Control ASSUMPTION A5]
  N --> O[Bot reply in transcript]
  O --> N
  N --> P{End or hand off?}
  P -->|Continue| N
  P -->|End session| Q[Mark idle · strip idle]
  P -->|Hand to Floyd| R[Optional note → Floyd · idle]
  Q --> S[Return S2 or stay idle S3]
  R --> S
```

---

## Error recovery

```mermaid
flowchart TD
  S3[S3 Bridged session] --> E1{Link healthy?}
  E1 -->|Yes| OK[Keep working]
  E1 -->|No| E2[S4 strip: Disconnected]
  E2 --> E3[Error detail: plain reason]
  E3 --> E4{Operator choice}
  E4 -->|Retry| E5{Session still valid? ASSUMPTION A6}
  E5 -->|Yes| S3
  E5 -->|No| E6[Warn · start fresh session]
  E6 --> S3
  E4 -->|Pick another bot| S2[S2 Bot picker]
  E4 -->|End session| Idle[Idle / unpaired session]
  E4 -->|Re-pair| S1[S1 Connect / Pair]
```

---

## Notes for implementers (non-contract)

- Keep status strip (S4) visible across S1–S3 whenever connection state matters.
- Error copy talks about the **bridge cable**, not Grok Build provider settings.
- Do not encode Tailscale vs HTTP vs local protocol in the UI until Sofia locks transport (A9).
