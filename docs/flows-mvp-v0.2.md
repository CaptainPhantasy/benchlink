# Flows MVP v0.2 — Benchlink

**Owner:** Amir · Design Lead · **Companion:** `wireframes-mvp-v0.2.md`  
**Contracts:** Sofia 0.2.0-mvp (`task.create` / `task.result`)

## Setup (once) — Connect / Pair
```mermaid
flowchart TD
  A[Open Bridge Control] --> B{Grok.app detected?}
  B -->|No| B1[S1 Not found + Refresh]
  B1 --> B
  B -->|Yes| C{Bridge Connected?}
  C -->|No| C1[S4 Retry / connection help]
  C1 --> C
  C -->|Yes| D{Paired?}
  D -->|No| E[S1 Pair = auth.pair + short-lived token]
  E --> F[S2 Pick target]
  D -->|Yes| F
```

## MVP happy path (≤3 steps)
```mermaid
flowchart TD
  F[1 Pick Floyd / Ethan canary / lead] --> G[2 Compose in Bridge Control]
  G --> H[Send = task.create]
  H --> I[3 Wait — Bridge status queued then running]
  I --> J{task.result or error?}
  J -->|task.result| K[Show summary + artifact_links]
  J -->|error| L[S3/S4 plain error + Retry]
  K --> M{End or new?}
  M -->|New| F
  M -->|End| N[Idle]
```

## Error recovery
```mermaid
flowchart TD
  E[Fail] --> T{Cause}
  T -->|Disconnected| R1[S4 Retry / Re-pair]
  T -->|Token expired| R2[Renew or Re-pair]
  T -->|Bot / task error| R3[Show error; resend or Change target]
  R1 --> S3[Resume S3]
  R2 --> S3
  R3 --> S3
```
