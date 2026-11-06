# MVP authentication contract errata (PR1 review)

`packages/bridge-protocol/openapi.v1.json` declares a global `ShortLivedBearer`
requirement. Before this correction, no operation overrode it, so the contract
told generated clients that pairing needs the bearer it issues and that the
health probe needs a bearer. Runtime never behaved that way.

**Status: corrected.** The wire-frozen 0.2.0-mvp contract received a narrow,
approved freeze exception (Floyd GM, 2026-11-05, PR1 thread PRRT_kwDOVkRils60PUhO).
Only security metadata changed. The OpenAPI document now matches the runtime
behavior covered by loopback HTTP regression tests:

- `POST /v1/auth/pair` (`/auth/pair` in OpenAPI): operation override `security: []`
  (anonymous). No HTTP bearer is required. Pairing still requires an enrolled
  device, a single-use signed challenge and (when enabled) the exact trusted
  Tailscale identity.
- `GET /v1/health` (`/health` in OpenAPI): operation override
  `security: [{}, {ShortLivedBearer: []}]` (anonymous or optional bearer). No bearer
  returns `auth=unpaired`. A valid bearer returns `auth=paired`. An invalid or
  expired bearer does not make the health probe fail.
- Session and task routes still inherit the global bearer and their exact scopes.
  A write body `access_token` must match the header.

No runtime behavior, message schema, path, version or wire shape changed. The
packaged daemon ships only `schemas/*.schema.json`, so its distribution
resources are unaffected. `tests/test_pr1_auth_docs.py` asserts both the runtime
boundaries and the exact OpenAPI security overrides.
