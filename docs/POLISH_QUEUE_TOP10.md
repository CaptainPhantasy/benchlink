# CaptainPhantasy: 10 non-website apps closest to done (code review)

Reviewed Thu Nov 5, 2026 (America/Indianapolis, UTC-5). I ranked these by **code evidence**: entrypoints, real modules, test suites I ran on this box, and TODO/stub density. Docs and UI polish didn't count.
Artifacts: `inventory_all.json` / `inventory.json` (all 241 repos, categorized), `metrics.json` (per-repo src/test counts), `candidates.json` (81 software candidates with real source), `logs/` (test runs), `src/` (12 shallow clones of public repos).

Method: 241 repos inventoried → 18 websites/client sites, 30 empty stubs, 1 fork (crush), 1 archived (RestoreAndRenew) excluded → I pulled file trees for about 110 repos (public via blobless clone; private via the GitHub connector) → I shallow-cloned 12 public repos and **ran their test suites** → for private repos I read the manifest and entrypoint through the connector. I couldn't clone or run private repos (the box has no gh auth), so they rank on code structure only.

## Ranked top 10

| # | Repo | What it is | Score | Hard evidence |
|---|------|-----------|------:|---------------|
| 1 | [fatchin](https://github.com/CaptainPhantasy/fatchin) | Self-hosted GitHub review fleet with a human merge gate (Python CLI and web control) | 9.5 | **912 tests passed, 0 failed** (93s). 0 TODO/FIXME. `fatchin`/`fatchin-web` console scripts; `src/ghagent/{executor,worker,fleet_runner,web_control}.py` are complete. CI present. |
| 2 | [wsdash](https://github.com/CaptainPhantasy/wsdash) | On-demand dashboard/CLI for unfinished workspace work, with optional LLM recap | 9 | **37/37 tests pass** (1.5s). `wsdash.cli:main` (807 LOC), `llm/{recap,chat,client}.py`, installer. Small and finishable. 11 TODO markers. |
| 3 | [JEVRuntime](https://github.com/CaptainPhantasy/JEVRuntime) | Terminal app: describe a job → approve a Constitution → run an agent under an independent supervisor | 8.5 | **279 passed / 2 failed** (both are read-only CLI cases in `test_git_reconciliation.py`). 1 TODO. supervisor.py, cli.py, git_gateway.py, workspace.py and runtime.py are all substantial. |
| 4 | [ratbag](https://github.com/CaptainPhantasy/ratbag) | Local, patch-only coding workbench (plan → build → test → review) with a web UI | 8.5 | **110 passed, 2 errors** (`test_engine::test_evidence` and `test_workbench::test_options` look like helpers that pytest collects as tests). 0 TODO. `swarm/{engine,providers,server}.py` plus web/app.js. |
| 5 | [CodeBaseCartographer](https://github.com/CaptainPhantasy/CodeBaseCartographer) | Codebase visualization/analysis tool (Vite/React plus a Node server) | 8 | **601/601 tests pass**. 1 of 36 test files fails to transform (`tests/integration/renameVariable.test.ts`). 14 TODOs. CI present. |
| 6 | [LGCY](https://github.com/CaptainPhantasy/LGCY) *(private)* | Standalone Bun TUI client for OpenAI-, Anthropic- and OpenCode-compatible providers | 8 | Clean layered architecture (core ← broker ← gateway ← services ← app, ADR-0001). 25 src files, each with a matching test (21 test files, including an import-boundary test). `main.ts` is a complete composition root with 0 TODOs. Scripts: test, typecheck, `bun build --compile`. Not run: private repo, and the box has no bun. |
| 7 | [TEAR](https://github.com/CaptainPhantasy/TEAR) *(private)* | Local-first freeware Markdown knowledge app, plus a browser extension | 7.5 | `app/tear-core.js` (770 LOC, 4 TODO markers), app.js, local server. 5 `node:test` suites plus a `verify:no-external` check. Zero runtime deps. Small enough to finish in one pass. |
| 8 | [closurecommandcenter](https://github.com/CaptainPhantasy/closurecommandcenter) | "Floyd's Unified Command Kernel": a local ops dashboard and server with an LLM tool bridge | 7 | **243 passed / 22 failed / 35 skipped**. Most failures expect a built workspace-editor dist or macOS volume paths; one is an MCP tool-contract test. server.py and closure_control.py are about 4.6k LOC each. Core works; the build and test environment needs fixing. |
| 9 | [legacy-service-platform](https://github.com/CaptainPhantasy/legacy-service-platform) *(private)* | Configurable field-service platform: estimating, restoration, scheduling, email/calendar sync, QuickBooks, Stripe, voice, customer portal | 7 | Largest real product here: 240 src files / 3.7 MB. **81 test files** (server and client). `verify` = typecheck + test + build. Postgres and pg-boss backend. Not run: private. Note: it has Stripe/QuickBooks/SignalWire integrations, so confirm the brand and account with Douglas before touching any payment path. Scope is big; finishing means getting `npm run verify` green, not adding features. |
| 10 | [TerminalOne](https://github.com/CaptainPhantasy/TerminalOne) | Local browser terminal with real PTY sessions, resume, themes and voice input | 7 | 6.3k LOC; src/server.js is 752 LOC. 13 test files. Pinned to Node `>=22 <23`, so on this box's Node 24 `npm ci` refused to install. The partial run gave 5 pass / 1 fail (`request-boundary-integration`). Needs a Node 22 environment, then the native PTY build verified. |

### Per-repo: gaps Larry would close and confidence

1. **fatchin**: Basically done. Remaining: an install/deploy smoke test (`deploy/`), check the web GUI tests (`tests/gui-browser-*.mjs`) under Playwright, and add a `[dev]` extra (pip warned it's missing). **Confidence: very high.** Note: **gha-worker** (private) is its twin (shares about 140 of 283 paths, same `src/ghagent`). Pick one as canonical before handoff.
2. **wsdash**: Clear the 11 TODOs, run an end-to-end `wsdash` check against a real workspace, set the pytest-asyncio loop-scope config. **Very high.**
3. **JEVRuntime**: Fix the 2 read-only CLI reconciliation failures (revoked policy / legacy attempt id paths), then do a full supervisor run end-to-end. **High.**
4. **ratbag**: Rename or fixture the two helper functions pytest collects as tests, smoke-test `ratbag` server and web UI against a provider. **High.**
5. **CodeBaseCartographer**: Fix the transform error in the renameVariable integration test, make `tsc && vite build` pass, verify the server/ package. **High.**
6. **LGCY**: Install bun, run `bun test` and `tsc --noEmit`, compile the binary, live-test against one provider. The code is clean, so remaining risk is mostly unknown test status. **High.**
7. **TEAR**: Run `npm run verify`, check that the extension loads in Chrome, close the 4 TODOs. **High.**
8. **closurecommandcenter**: Make the tests build the editor dist or skip without it, remove `/Volumes` assumptions, fix the MCP tool-contract test. **Medium-high.**
9. **legacy-service-platform**: Run `npm run verify` and triage failures. Integrations need sandbox creds and stubs. **Medium**: lots of surface area, but the test harness is already there.
10. **TerminalOne**: Run under Node 22, rebuild node-pty, fix the request-boundary test, then smoke-test. **Medium-high.**

## Near-misses

- **bonsai-harness** (public): deterministic agent inference harness, `0.1.0-beta.3`, 15 tests, a `verify:beta` script, CI, 1 TODO. Not run (no bun on the box). Could easily swap in for #10.
- **decision-board** (private): "The Round Table" multi-agent decision engine, FastAPI and Next.js, 14 test files, JSON schemas. Not run, and the backend entrypoint is thin.
- **mouth** (public): local speech-to-text. 4 test collection errors from missing deps (pyperclip, apscheduler not declared in pyproject). Probably close, but needs audio hardware to verify.
- **openmythos** (public): 37 test files, but the tests hard-code `/Volumes/Storage/OpenMythos/...` config paths. It's also an oh-my-pi derivative.
- **STAT** (private, Rust/Axum medical documentation validation), **mission-control-macos** (Swift, can't be tested on Linux), **Floyd_Code** (Go; `go build ./...` passes, but it's a large crush-derived codebase with 423 TODOs). All are substantial but not near-done.
- Also substantial but large or harness-sprawl: f7, FLUX, OhMyFloyd (oh-my-pi fork), Floyd-Code-CLI, CASPER, DeerFloyd (deer-flow derivative), LegacyFieldCommand, ScanStockPro, LeadFlip, Floyd-TTY-Bridge-for-Chrome (root-level test scripts need a live bridge; collection errors).

## Excluded as websites / client sites / archives
PSI, PSILIVE, PSI09182026, precision-sewer-inspection, psi-forms-platform, IndianaDrainCompany.com, SimpleManPlumbing-private-20260717, rrp-original-site-copy-20261027-isolated-7d92, cloudgeneralist-private-20260716, stone-lake-interactive-map, Restore, Restore-Devil, RestoreAndRenew (archived), 317Leads-private-20260717 (client leads worker), LAILIVE, IndieDevProfile, douglas-talley-portfolio, GenSpark, HoosierDaddy, sb1-9je9354w, WebSwap (no source). Also excluded: 30 empty or near-empty stubs, and the crush fork.
