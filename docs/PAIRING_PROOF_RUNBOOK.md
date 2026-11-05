# Benchlink Pairing Proof — Runbook (Claire · Ops)

Repo: `CaptainPhantasy/benchlink` (private) — `scripts/pairing-proof.sh`. Implements BRIDGE_TOKEN_SPEC v0.3. Run on the **Mac mini** (on tailnet, untagged user-owned node) so no human handles a raw token.

## Prerequisites (in order)
1. **Sofia:** pairing + bearer landed (green on loopback 2026-11-05); merged by Douglas.
2. **Larry:** daemon behind `tailscale serve` at `https://inference.tail58d565.ts.net:19740` (tailnet only, no Funnel), `BENCHLINK_REQUIRE_TAILSCALE_IDENTITY=1`. Confirm the Mac mini is an untagged node.
3. **Douglas OK — enrollment (out of band):** operator adds `{device_id: {public_key_b64url}}` to `BENCHLINK_DEVICE_ALLOWLIST_PATH` on inference (outside repo). The pair request carries no public key.
4. Device Ed25519 private key file at `BENCHLINK_DEVICE_KEY_PATH` (sidecar key file, mode 0600). This is the only key source. OpenSSL 3 (`brew install openssl@3`).

## One command (Mac mini)
```bash
cd benchlink && OPENSSL=/opt/homebrew/opt/openssl@3/bin/openssl BENCHLINK_DEVICE_KEY_PATH=<sidecar key file> scripts/pairing-proof.sh
```
Preview: `scripts/pairing-proof.sh --dry-run`. Optional: `BENCHLINK_PROOF_REPAIR=1` (re-pair gives new token); `BENCHLINK_PROOF_EXPIRY_WAIT=<s>` on a short-TTL test instance (expects 401 `auth.expired`, then re-pair → 201). Overrides: `BENCHLINK_BRIDGE_BASE_URL`, `BENCHLINK_DEVICE_ID`.

## Checks
1. tailscale up; `GET /v1/health` without bearer → 200 `auth=unpaired`; log notes whether the path is via tailscale serve (`*.ts.net`).
2. `POST /v1/auth/pair` `auth.pair.request` → `auth.pair.challenge` → Ed25519 signature over UTF-8 `challenge + "." + nonce`, base64url with no padding → `auth.pair.complete` (scopes `session.create, session.message, session.close, task.create`) within the 60 s single-use challenge TTL → `auth.session.token` (`short_lived`, 15 min).
3. `GET /v1/health` with bearer → `auth=paired`; logs `X-Benchlink-Tailscale-Identity`; **fails if `none` on a `*.ts.net` base**.
4. `POST /v1/sessions` `{"v":"1","type":"session.create","access_token":<bearer>,"intent":"staff_task","target_agent":"ethan"}` → **201**.
5. Negatives: no bearer 401 · bogus bearer 401 · body/header token mismatch 403.

## Exit codes
`0` all pass · `1` a check failed · `2` blocked (names owner: "daemon not live" Larry · "Sofia not landed" pair 501 · 403 at pair = not on allowlist / identity missing → enrollment).

## Evidence & secrecy
`./pairing-proof-<UTC>.log`: status codes, device_id, token SHA-256 prefix (6 chars), expires_at in RFC3339 UTC, Tailscale identity, UTC timestamps. Authorization, access_token, proof, and challenge are never logged. Token lives only in a shell variable; sent via stdin (body) and fd 3 (header), never argv, URL, echo, or disk. Upload the log to the bridge Drive folder and link it in STAFF_COLLAB.

## Verified on Claire box (2026-11-05)
shellcheck clean; `--dry-run` exit 0; vs Sofia loopback daemon (temp allowlist + generated key, identity check off): main proof + re-pair **PASS exit 0**; expiry check (TTL 3 s, wait 5 s) **PASS exit 0**.
