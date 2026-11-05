# Internal Beta Acceptance Checklist — Grok-App ↔ Floyd Bridge

**Owner:** Grace · Client Success Lead  
**Scope:** Staff-only internal MVP → functional beta (not public, not client-facing)  
**Version:** v0.2 · 2026-11-05 (America/Indianapolis)  
**Project:** [Grok-App-Floyd-Bridge](https://drive.google.com/drive/folders/1MxA46KBtEXXLFuJZ39UT_YiUdKutj0DR)  
**Collab:** [STAFF_COLLAB.md](https://drive.google.com/file/d/1vvR8ZscZRsOtwVHB7CYaO1boujQVkDSJ/view)  
**Aligned to:** Ethan [PRD-v0](https://drive.google.com/file/d/1kj5pgJ3rG8XsWV3PSeI7NJAhEQhnGxcz/view) §4; Larry [GATE_CHECKLIST](https://drive.google.com/file/d/1-n7CedE6-wtEV0seOwao2u2bRcToJG5U/view) §F; Claire [OPS_CONSTRAINTS](https://drive.google.com/file/d/1mvB9F5p2Wf7PrUC9Kjbl6FYUoeHnBn1K/view) R4; **Frozen D1–D4** (Floyd GM 2026-11-05)

**MVP cut (Grace acceptance):** one verified happy-path **Staff task** round trip + clear failure classes. Everything else deferred or non-gate.

**Rule:** Check a box only with evidence (live probe id, log link, Drive/file path, or STAFF_COLLAB receipt). No hopeful checks.

## Frozen MVP story (D1–D4)

| ID | Freeze (do not re-litigate in acceptance) |
|----|-------------------------------------------|
| D1 | Operator in Grok.app starts **Staff task** → Floyd (default) or named staff bot → result/error in bridge status (optional Floyd chat ping for high priority). Product canary = Ethan. |
| D2 | Transport: Tailscale HTTP(S) Mac stub ↔ bridge service. Do not hijack Build Session API. |
| D3 | Auth: Tailscale identity and/or device-bound credential + short-lived request tokens. No secrets in chat/STAFF_COLLAB. |
| D4 | Runtime: always-on bridge on inference when LAN-shared; Mac client stub for UX; repo Computer-first. |

---

## Support path (staff-only)

| Issue class | First owner | Escalate to | Do not |
|-------------|-------------|-------------|--------|
| Pairing / auth / "can't connect" | Larry + Sofia | Floyd → Douglas | Paste tokens into chat or STAFF_COLLAB |
| Routing to wrong bot | Floyd | Douglas | Bypass Floyd for staff fan-out |
| Mac UX / unclear steps | Amir | Ethan | Invent client-facing copy |
| Repo / CI / gate fail | Larry | Sofia | Edit production sites |
| Secrets / access / cost | Claire | Floyd → Douglas | Reuse PSI Stripe or brand billing creds |
| "Ready for staff trial?" | **Grace** | Floyd → Douglas | Open to external clients / PSI / IDC |
| Naming | Maya | Floyd | Public launch language |

**Intake:** Grace inbox (`Floyd's Labs Staff/Grace/inbox/`) or ping Grace in Grok Bot with: what you tried, expected vs actual, session/request ids, screenshot path.

**Internal beta open when:** A + B green, C green, Larry gate F checked or deferred with note, Floyd alerts Douglas.

---

## A. Happy path — one Staff task round trip (must pass)

- [ ] A1. ≤3 explicit actions to start a Staff task after setup (Amir flow + live count)
- [ ] A2. Default target → Floyd; named target → that staff bot; canary Ethan works (receipt / bridge log)
- [ ] A3. Ack or result visible within 2 minutes under normal load (timestamped probe)
- [ ] A4. ≥1 full round trip: Mac stub → Tailscale HTTP(S) → bridge → staff bot → result summary + artifact links (session/request ids logged)
- [ ] A5. Result or error appears in bridge status without leaving the loop (optional Floyd ping only for high priority)

## B. Clear failure classes (must pass)

- [ ] B1. Auth failure → explicit error class (no silent drop)
- [ ] B2. Offline / unreachable Mac stub, Tailscale, or inference runtime → explicit error class
- [ ] B3. Wrong / unknown bot target → explicit error class
- [ ] B4. Timeout → explicit error class
- [ ] B5. Larry failure matrix has Grace sign-off (pass or accepted deferral with note)

## C. Safety & scope (must pass)

- [ ] C1. No production-site writes
- [ ] C2. No Stripe / HubSpot / billing writes from the bridge
- [ ] C3. No autonomous external email/Slack/SMS outside AGENTS approval rules
- [ ] C4. No secrets in STAFF_COLLAB, SSoT, routines, or MEMORY (names only)
- [ ] C5. PSI / IDC / external client data out of scope
- [ ] C6. Does not hijack Grok Build Session API as staff transport (D2)

## D. Docs & support readiness

- [x] D1. Checklist on Drive — evidence: `1vTKx-6lFTsqr9oMzTEKtY_pZzMAgxwMV`
- [x] D2. Support path matches ownership matrix
- [x] D3. Criteria aligned to frozen D1–D4 (this v0.2)
- [ ] D4. Known limitations linked (Larry RISK_REGISTER + Ethan non-goals) before wider staff trial
- [ ] D5. Rollback / stop-using note (Claire R4 / Larry plan) before wider staff use
- [ ] D6. Floyd notified when A–C green for Douglas alert

## E. Non-gates for this MVP

- Public / client beta, SLAs, Legacy AI customer support
- UI polish as a hard gate
- Cloud provision/deploy as a hard gate
- Full seven-lead fan-out UX
- Tightening iMessage `allowFrom=*` (not MVP transport)

---

## Sign-off

| Role | Name | Date | Evidence |
|------|------|------|----------|
| Grace (criteria ready) | Grace | 2026-11-05 | v0.2; Drive `1vTKx-6lFTsqr9oMzTEKtY_pZzMAgxwMV` |
| Grace (A–C green) | | | |
| Larry (gates) | | | |
| Floyd (Douglas alert) | | | |

**Status:** Acceptance **criteria** delivered and aligned to frozen D1–D4. Probe boxes A–C unchecked until live evidence.
