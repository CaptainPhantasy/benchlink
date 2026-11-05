# SSoT — Design Track (Benchlink)

**Owner:** Amir · Design Lead · **Updated:** 2026-11-05 (America/Indianapolis)  
**Architecture SSoT:** `../SSoT.md` (Sofia) · **Contracts:** `api-contracts-mvp.md` **0.2.0-mvp**  
**Product name:** **Benchlink** (Douglas-approved)  
**Current pack:** wireframes-mvp-v0.2 / flows-mvp-v0.2 — **MVP UX accepted** (Floyd GM)

## Authority
1. **Floyd UX locks APPROVED:** A3 companion Bridge Control; A5 typing in Bridge Control (do not reopen for in-Grok.app compose); S2 static roster (Floyd default + Ethan/Amir/Sofia/Larry/Claire/Maya/Grace).
2. **Frozen D1–D4** (Ethan PRD §7): Staff task → Floyd/named → result/error in bridge status; canary = Ethan; Tailscale HTTPS; short-lived tokens; inference bridge + Mac stub; Computer-first.
3. **Claire ops:** never show secret values; env names only.
4. **Sofia 0.2.0-mvp:** UI labels align to `task.create` / `task.result` where helpful (not a blocker). Extension sealed → sidecar = Bridge Control.

## MVP operator path (≤3 steps after paired)
1. **Pick** target (Floyd default, or Ethan canary / named lead).  
2. **Compose + send** Staff task in Bridge Control (`task.create`).  
3. **Wait** → bridge status shows `task.result` (summary + artifact links) or error.

Pairing is one-time setup, not counted in the three.

## Soft opens — locked for MVP (Floyd GM)
| ID | Decision |
|----|----------|
| O1 | Mid-task handoff **out** — end session → pick another bot on S2 |
| O2 | Launch Bridge with Grok.app: **OFF** (manual open) |
| O3 | HP Floyd ping: **OFF** by default; operator may enable per task / for failures |
| O4 | Result retention: **last session transcript in UI only**; no durable store |
| O5 | Chrome: **window** Bridge Control (not menu-bar-only) |
| O6 | Sidecar UI: **yes** — Bridge Control *is* the sidecar UI |

## Separate track
Larry TOP10 platform polish queue (wait for Larry functional gaps before deep polish): fatchin, wsdash, JEVRuntime, ratbag, CodeBaseCartographer, LGCY, TEAR, closurecommandcenter, legacy-service-platform, TerminalOne. Writeup: `/workspace/scratch/github-near-done/TOP10.md`.
