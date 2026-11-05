"""CLI: python -m benchlink_sidecar <command>

  config            print redacted effective config (exit 0 if parse OK, 2 on ConfigError)
  dry-run           print request shapes for all OpenAPI ops (no network)
  health            GET {base}/v1/health live (exit 3 if base URL unset/placeholder)
  ux [goal…]        Bridge Control shell demo: Pick → Compose/Send → Wait (dry-run when URL unset)
  serve             run loopback stub on 127.0.0.1:8787 (+ Bridge Control HTML at /)
  generate-key [--force] [--tailscale-identity LOGIN]
                    create Ed25519 device key at BENCHLINK_DEVICE_KEY_PATH (0600) and print
                    the PUBLIC allowlist snippet only (private key never printed)
  pubkey            print the public allowlist snippet for an existing key
  pair              pair with the bridge (D3) and print token_type/expires_at (token never printed)
"""
from __future__ import annotations

import json
import sys

from .client import BridgeClient
from .config import ConfigError, describe, load_config

EXIT_OK, EXIT_USAGE, EXIT_CONFIG, EXIT_UNSET, EXIT_UPSTREAM = 0, 1, 2, 3, 4


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in {"-h", "--help"}:
        print(__doc__)
        return EXIT_USAGE if not argv else EXIT_OK
    cmd = argv[0]
    try:
        cfg = load_config()
    except ConfigError as e:
        print(f"CONFIG ERROR: {e}", file=sys.stderr)
        return EXIT_CONFIG

    if cmd == "config":
        print(json.dumps(describe(cfg), indent=2))
        return EXIT_OK
    client = BridgeClient(cfg)
    if cmd in {"generate-key", "pubkey"}:
        from .pairing import allowlist_snippet, generate_key, load_key
        ts_id = None
        if "--tailscale-identity" in argv:
            i = argv.index("--tailscale-identity")
            ts_id = argv[i + 1] if i + 1 < len(argv) else None
        try:
            key = (generate_key(cfg.device_key_path, force="--force" in argv) if cmd == "generate-key"
                   else load_key(cfg.device_key_path))
        except ConfigError as e:
            print(f"CONFIG ERROR: {e}", file=sys.stderr)
            return EXIT_CONFIG
        print(json.dumps(allowlist_snippet(cfg.device_id, key, ts_id), indent=2))
        return EXIT_OK
    if cmd == "pair":
        from .pairing import PairingError
        try:
            print(json.dumps({"device_id": cfg.device_id, **client.pair()}))
            return EXIT_OK
        except ConfigError as e:
            print(f"REFUSED: {e}", file=sys.stderr)
            return EXIT_UNSET
        except PairingError as e:
            print(f"PAIR FAILED: {e.code}: {e.message}", file=sys.stderr)
            return EXIT_UPSTREAM
        except OSError as e:
            print(f"UPSTREAM ERROR: {e}", file=sys.stderr)
            return EXIT_UPSTREAM
    if cmd == "dry-run":
        print(json.dumps([r.redacted() for r in client.all_dry_run_shapes()], indent=2))
        return EXIT_OK
    if cmd == "health":
        try:
            status, payload = client.health()
        except ConfigError as e:
            print(f"REFUSED: {e}", file=sys.stderr)
            return EXIT_UNSET
        except OSError as e:
            print(f"UPSTREAM ERROR: {e}", file=sys.stderr)
            return EXIT_UPSTREAM
        print(status, json.dumps(payload))
        return EXIT_OK if status == 200 else EXIT_UPSTREAM
    if cmd == "ux":
        from .ux import run_cli_demo
        goal = " ".join(argv[1:]).strip() or None
        return run_cli_demo(cfg, goal)
    if cmd == "serve":
        from .server import serve
        srv = serve(cfg)
        print(f"benchlink mac-sidecar listening on http://{cfg.listen_host}:{cfg.listen_port}/v1 "
              f"(upstream {'configured' if cfg.upstream_configured else 'UNSET — /v1/health returns 503'}); "
              f"Bridge Control UI at http://{cfg.listen_host}:{cfg.listen_port}/")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
        return EXIT_OK
    print(f"unknown command {cmd!r}\n{__doc__}", file=sys.stderr)
    return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
