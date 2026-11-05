# MVP authentication contract errata (PR1 review)

The frozen `packages/bridge-protocol/openapi.v1.json` currently declares a global
`ShortLivedBearer` requirement and omits operation-level exceptions. That is a
real generated-client blocker, not a resolved thread. Its JSON bytes remain
unchanged pending explicit contract-owner approval of this security-metadata correction.
Do not change runtime behavior to require a bearer to obtain the first bearer.

Actual behavior, covered by loopback HTTP regression:
- `POST /v1/auth/pair` (`/auth/pair` in OpenAPI): no HTTP bearer required. Pairing still
  requires an enrolled device, single-use signed challenge and (when enabled) the
  exact trusted Tailscale identity. Proposed operation override: `security: []`.
- `GET /v1/health` (`/health` in OpenAPI): no bearer required, returns `auth=unpaired`.
  A valid bearer optionally produces `auth=paired`; an invalid/expired bearer does
  not make the health probe fail. Proposed override: `security: [{}, {ShortLivedBearer: []}]`.
- Session/task routes still require the global bearer and applicable exact scopes;
  write body `access_token` must match the header. No runtime auth relaxation.

The separate integration artifact `openapi-auth-correction.REQUIRES-APPROVAL.patch`
adds only those two operation overrides. It is deliberately NOT applied to the
frozen contract. Larry should obtain the narrow freeze exception, review generated
client effects, then integrate it. Until then PR1 thread PRRT_kwDOVkRils60PUhO remains
OPEN/BLOCKED; local passing tests do not resolve a contract mismatch or prove remote CI.
