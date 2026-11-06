#!/usr/bin/env bash
# shellcheck disable=SC2015  # pass/fail/log always return 0
# Benchlink pairing proof (BRIDGE_TOKEN_SPEC v0.1). Run on the Mac mini (tailnet).
# Token lives only in a shell variable: never echoed, written to disk, or put in URLs.
# Usage: scripts/pairing-proof.sh [--dry-run]
# Env: BENCHLINK_BRIDGE_BASE_URL (default below), BENCHLINK_DEVICE_ID (default: hostname),
#      BENCHLINK_DEVICE_KEY_PATH (Ed25519 PEM private key file; required, only key source),
#      BENCHLINK_PROOF_REPAIR=1 (optional re-pair check),
#      BENCHLINK_PROOF_EXPIRY_WAIT=<seconds> (optional, short-TTL test instance: wait, expect 401 auth.expired, re-pair),
#      OPENSSL (default openssl; needs Ed25519 -rawin, OpenSSL 3)
# Pins (Sofia 2026-11-05): sig = Ed25519 over UTF-8 "challenge.nonce", base64url no padding; challenge single-use 60s TTL;
#      no refresh token; access TTL 15 min; 401 unpaired/expired, 403 denied; bearer in header only.
set -u
BASE="${BENCHLINK_BRIDGE_BASE_URL:-https://inference.tail58d565.ts.net:19740}"
BASE="${BASE%/}"
DEVICE_ID="${BENCHLINK_DEVICE_ID:-$(hostname -s 2>/dev/null || echo mac-mini)}"
KEY_PATH="${BENCHLINK_DEVICE_KEY_PATH:-}"
SCOPES='["session.create","session.message","session.close","task.create"]'
OPENSSL="${OPENSSL:-openssl}"
CLIENT_VERSION="0.2.0-mvp"
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="./pairing-proof-${STAMP}.log"
FAILS=0
TOKEN=""
umask 077

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { printf '%s %s\n' "$(ts)" "$*" | tee -a "$LOG"; }
pass() { log "PASS $*"; }
fail() { log "FAIL $*"; FAILS=$((FAILS+1)); }
die() { log "BLOCKED $*"; log "RESULT: BLOCKED (exit 2)"; exit 2; }
have() { command -v "$1" >/dev/null 2>&1; }

# json_get KEY  (stdin JSON) -> string value; jq if present, else sed fallback (flat string keys)
json_get() {
  if have jq; then jq -r --arg k "$1" '.[$k] // empty | tostring'
  else sed -n 's/.*"'"$1"'"[[:space:]]*:[[:space:]]*"\{0,1\}\([^",}]*\)"\{0,1\}.*/\1/p' | head -n 1; fi
}
hash6() { if have shasum; then printf '%s' "$1" | shasum -a 256 | cut -c1-6; else printf '%s' "$1" | sha256sum | cut -c1-6; fi; }
nonce() { "$OPENSSL" rand -hex 16 2>/dev/null || od -An -N16 -tx1 /dev/urandom | tr -d ' \n'; }

# req METHOD PATH [BODY] [HDR_TOKEN_MODE: none|token|bogus] [BODY_TOKEN_MODE: token|other]
# -> sets HTTP, BODY_OUT. Secrets never in argv: body via stdin, Authorization header via fd 3 (process substitution).
# Literal __TOKEN__ in BODY is replaced in-shell (printf builtin) with the token (or a mismatching value for "other").
BOGUS="bogus-proof-token-000000"
req() {
  _m="$1"; _p="$2"; _b="${3:-}"; _a="${4:-none}"; _bt="${5:-token}"
  _out="$(mktemp -t bl.XXXXXX)"; _hdr="$(mktemp -t blh.XXXXXX)"
  _tok="$TOKEN"; [ "$_bt" = "other" ] && _tok="mismatch-${BOGUS}"
  _hv=""; case "$_a" in token) _hv="$TOKEN";; bogus) _hv="$BOGUS";; esac
  set -- -sS -m 15 -o "$_out" -D "$_hdr" -w '%{http_code}' -X "$_m" -H 'Content-Type: application/json'
  [ -n "$_b" ] && set -- "$@" --data-binary @-
  [ -n "$_hv" ] && set -- "$@" -H @/dev/fd/3
  HTTP="$(printf '%s' "${_b//__TOKEN__/$_tok}" | curl "$@" "${BASE}${_p}" 3< <(printf 'Authorization: Bearer %s\n' "$_hv") 2>/dev/null)" || HTTP="000"
  _tok=""; _hv=""
  BODY_OUT="$(cat "$_out")"; rm -f "$_out"
  HDRS_OUT="$(grep -i '^X-Benchlink-' "$_hdr" | tr -d '\r')"; rm -f "$_hdr"   # only non-secret X-Benchlink-* headers kept
}
hdr_get() { printf '%s\n' "$HDRS_OUT" | sed -n "s/^$1:[[:space:]]*//Ip" | head -n 1; }
# to_utc RFC3339 -> RFC3339 UTC (Z); passes through if already Z or unparsable
to_utc() {
  _v="$1"
  case "$_v" in *Z) printf '%s' "$_v"; return;; esac
  date -u -d "$_v" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null && return
  _c="$(printf '%s' "$_v" | sed -E 's/\.[0-9]+//; s/([+-][0-9]{2}):([0-9]{2})$/\1\2/')"
  _e="$(date -j -f %Y-%m-%dT%H:%M:%S%z "$_c" +%s 2>/dev/null)" && date -u -r "$_e" +%Y-%m-%dT%H:%M:%SZ && return
  printf '%s' "$_v"
}

b64url() { base64 | tr -d '\n=' | tr '+/' '-_'; }
sign_challenge() { # $1 = message (UTF-8 "challenge.nonce"); key only from BENCHLINK_DEVICE_KEY_PATH
  _mf="$(mktemp -t blmsg.XXXXXX)"; printf '%s' "$1" > "$_mf"   # 0600 (umask 077), deleted immediately; -rawin needs a sized file
  "$OPENSSL" pkeyutl -sign -rawin -inkey "$KEY_PATH" -in "$_mf" 2>/dev/null | b64url
  rm -f "$_mf"
}
# pair: full request->challenge->complete; sets TOKEN, EXP, TT. Returns via die on block.
pair() {
  N="$(nonce)"
  req POST /v1/auth/pair "{\"v\":\"1\",\"type\":\"auth.pair.request\",\"device_id\":\"$DEVICE_ID\",\"client\":\"grok.app\",\"client_version\":\"$CLIENT_VERSION\",\"nonce\":\"$N\"}"
  [ "$HTTP" = "501" ] && die "/v1/auth/pair returns 501 — Sofia not landed"
  [ "$HTTP" = "403" ] && die "pair request 403 auth.denied — device not on BENCHLINK_DEVICE_ALLOWLIST_PATH (enrollment, Douglas OK) or Tailscale identity missing"
  [ "$HTTP" = "200" ] || die "pair request -> $HTTP"
  CH="$(printf '%s' "$BODY_OUT" | json_get challenge)"; CN="$(printf '%s' "$BODY_OUT" | json_get nonce)"
  CX="$(printf '%s' "$BODY_OUT" | json_get expires_at)"; BODY_OUT=""
  [ -n "$CH" ] && pass "auth.pair.challenge received (challenge redacted) expires_at=$(to_utc "$CX")" || die "no challenge in response"
  PROOF="$(sign_challenge "${CH}.${CN:-$N}")"; CH=""
  [ -n "$PROOF" ] || die "signing failed (key at BENCHLINK_DEVICE_KEY_PATH; need OpenSSL 3 Ed25519 -rawin)"
  req POST /v1/auth/pair "{\"v\":\"1\",\"type\":\"auth.pair.complete\",\"device_id\":\"$DEVICE_ID\",\"proof\":\"$PROOF\",\"scopes\":$SCOPES}"
  PROOF=""
  TOKEN="$(printf '%s' "$BODY_OUT" | json_get access_token)"
  EXP="$(to_utc "$(printf '%s' "$BODY_OUT" | json_get expires_at)")"; TT="$(printf '%s' "$BODY_OUT" | json_get token_type)"
  BODY_OUT=""
  if [ "$HTTP" = "200" ] && [ -n "$TOKEN" ] && [ "$TT" = "short_lived" ]; then
    pass "auth.session.token issued token_sha256_6=$(hash6 "$TOKEN") token_type=$TT expires_at=$EXP (proof/token redacted)"
  else TOKEN=""; die "pair complete -> $HTTP (no short_lived token)"; fi
}

log "Benchlink pairing proof start base=$BASE device_id=$DEVICE_ID dry_run=$DRY"
if [ "$DRY" = 1 ]; then
  cat <<STEPS | tee -a "$LOG"
[dry-run] 1 preflight: tailscale status; GET $BASE/v1/health no bearer expect auth=unpaired (000 => daemon not live)
[dry-run] 2 POST $BASE/v1/auth/pair {v:1,type:auth.pair.request,device_id,client:grok.app,client_version,nonce} (501 => Sofia not landed)
[dry-run]   via tailscale serve: yes iff base is https://*.ts.net (Tailscale-User-Login injected)
[dry-run] 3 sign UTF-8 "challenge.nonce" with Ed25519 key file BENCHLINK_DEVICE_KEY_PATH, base64url no pad; complete within 60s
[dry-run] 4 POST $BASE/v1/auth/pair {v:1,type:auth.pair.complete,device_id,proof,scopes:$SCOPES} -> auth.session.token
[dry-run] 5 authed GET $BASE/v1/health (Bearer header) expect 200 auth=paired; read X-Benchlink-Tailscale-Identity (fail if none on *.ts.net)
[dry-run] 6 POST $BASE/v1/sessions {session.create, access_token=<bearer>, intent:staff_task, target_agent:ethan} expect 201 (token via stdin/fd only)
[dry-run] 7 negatives: no bearer 401; bogus bearer 401; body/header mismatch 403
[dry-run] 8 optional (BENCHLINK_PROOF_REPAIR=1): re-pair -> new token
[dry-run] 9 optional (BENCHLINK_PROOF_EXPIRY_WAIT=s): wait -> 401 auth.expired -> re-pair -> 201
[dry-run] log: $LOG (token sha256 prefix 6; Authorization/access_token/proof/challenge redacted)
STEPS
  log "RESULT: DRY-RUN OK"; exit 0
fi

# 1 preflight
have curl || die "curl missing"
if have tailscale; then
  tailscale status >/dev/null 2>&1 && pass "tailscale up" || die "tailscale not connected on this device"
else die "tailscale CLI not found (run on the Mac mini)"; fi
req GET /v1/health
[ "$HTTP" = "000" ] && die "daemon not live at $BASE (connect fail) — owner Larry"
A0="$(printf '%s' "$BODY_OUT" | json_get auth)"
[ "$HTTP" = "200" ] && [ "$A0" = "unpaired" ] && pass "pre-pair health (no bearer) 200 auth=unpaired" || fail "pre-pair health -> $HTTP auth=$A0 (want 200 unpaired)"
[ -n "$KEY_PATH" ] || die "BENCHLINK_DEVICE_KEY_PATH unset (sidecar Ed25519 key file)"
[ -r "$KEY_PATH" ] || die "device key file BENCHLINK_DEVICE_KEY_PATH not readable"
pass "device key file present (path set, contents not logged)"
TSNET=0; case "$BASE" in https://*.ts.net:*|https://*.ts.net|https://*.ts.net/*) TSNET=1;; esac
case "$BASE" in https://*.ts.net:*|https://*.ts.net) log "INFO via tailscale serve: yes (tailnet HTTPS host; daemon sees Tailscale-User-Login)";;
  *) log "INFO via tailscale serve: NO (direct/loopback; identity header not injected — not a prod proof)";; esac

# 2-4 pair (sign + complete promptly: challenge single-use, 60s TTL)
pair
T1H="$(hash6 "$TOKEN")"

# 5 authed health
req GET /v1/health "" token
A="$(printf '%s' "$BODY_OUT" | json_get auth)"
[ "$HTTP" = "200" ] && [ "$A" = "paired" ] && pass "authed health 200 auth=paired" || fail "authed health -> $HTTP auth=$A"
TSID="$(hdr_get X-Benchlink-Tailscale-Identity)"; [ -n "$TSID" ] || TSID="(header absent)"
log "INFO X-Benchlink-Tailscale-Identity=$TSID"
if [ "$TSNET" = 1 ]; then
  case "$TSID" in none|"(header absent)") fail "tailnet base but no bound Tailscale identity ($TSID)";; *) pass "tailscale identity bound at pairing";; esac
fi

# 6 authed call (body token must equal header bearer)
SBODY='{"v":"1","type":"session.create","access_token":"__TOKEN__","intent":"staff_task","target_agent":"ethan"}'
req POST /v1/sessions "$SBODY" token token
[ "$HTTP" = "201" ] && pass "authed POST /v1/sessions -> 201" || fail "authed POST /v1/sessions -> $HTTP (want 201)"

# 7 negatives
req POST /v1/sessions "$SBODY" none token
[ "$HTTP" = "401" ] && pass "no bearer -> 401" || fail "no bearer -> $HTTP (want 401)"
req POST /v1/sessions "$SBODY" bogus token
[ "$HTTP" = "401" ] && pass "bogus bearer -> 401" || fail "bogus bearer -> $HTTP (want 401)"
req POST /v1/sessions "$SBODY" token other
[ "$HTTP" = "403" ] && pass "body/header token mismatch -> 403" || fail "mismatch -> $HTTP (want 403)"

# 8 optional re-pair check: new opaque token differs
if [ "${BENCHLINK_PROOF_REPAIR:-0}" = 1 ]; then
  TOKEN=""; pair
  [ "$(hash6 "$TOKEN")" != "$T1H" ] && pass "re-pair issued new token" || fail "re-pair returned same token"
fi

# 9 optional expiry check (short-TTL test instance)
if [ -n "${BENCHLINK_PROOF_EXPIRY_WAIT:-}" ]; then
  log "INFO waiting ${BENCHLINK_PROOF_EXPIRY_WAIT}s for token expiry"
  sleep "$BENCHLINK_PROOF_EXPIRY_WAIT"
  req POST /v1/sessions "$SBODY" token token
  C="$(printf '%s' "$BODY_OUT" | json_get code)"
  [ "$HTTP" = "401" ] && [ "$C" = "auth.expired" ] && pass "expired token -> 401 auth.expired" || fail "after wait -> $HTTP code=$C (want 401 auth.expired)"
  TOKEN=""; pair
  req POST /v1/sessions "$SBODY" token token
  [ "$HTTP" = "201" ] && pass "re-pair after expiry -> sessions 201" || fail "re-pair after expiry -> $HTTP"
fi

TOKEN=""; unset TOKEN
if [ "$FAILS" -eq 0 ]; then log "RESULT: PASS"; exit 0; else log "RESULT: FAIL ($FAILS)"; exit 1; fi
