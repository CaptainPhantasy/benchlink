# Flows MVP v0.1 — Benchlink (A5-aligned)

**Owner:** Amir · Design Lead · **Companion:** `wireframes-mvp-v0.1.md`

## Connect / Pair (D2–D4, A3)
```mermaid
flowchart TD
  A[Open Bridge Control] --> B{Grok.app detected?}
  B -->|No| B1[S1 Not found + Refresh]
  B1 --> B
  B -->|Yes| C{Tailscale linked?}
  C -->|No| C1[S4 Open Tailscale]
  C1 --> C
  C -->|Yes| D{Bridge service reachable?}
  D -->|No| D1[S4 Retry service]
  D1 --> D
  D -->|Yes| E{Paired?}
  E -->|No| F[S1 Pair via Tailscale/device + short-lived token]
  F --> G[S2 Target picker]
  E -->|Yes| G
```

## Staff task happy path (D1 + A5 + S2)
```mermaid
flowchart TD
  G[S2 Pick Floyd / Ethan canary / lead] --> H[S3 Compose in Bridge Control A5]
  H --> I[Send over Tailscale HTTPS]
  I --> J[Status queued then running]
  J --> K{Result or error?}
  K -->|Result| L[S3 Result + Copy / Insert to Grok.app]
  K -->|Error| M[S3/S4 plain error + Retry]
  L --> N{End or new task?}
  N -->|New| G
  N -->|End| O[Idle]
```

## Error recovery
```mermaid
flowchart TD
  E[Link or task fails] --> T{Cause}
  T -->|Tailscale| R1[S4 Open Tailscale + Retry]
  T -->|Service| R2[S4 Retry backoff]
  T -->|Token expired| R3[Renew token or S1 Re-pair]
  T -->|Bot error| R4[S3 show error; resend or Change target]
  R1 --> S3[Resume S3]
  R2 --> S3
  R3 --> S3
  R4 --> S3
```
