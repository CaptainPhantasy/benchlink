"""CLI: python -m benchlink_daemon <command>

  config   print redacted config
  serve    listen on BENCHLINK_BRIDGE_LISTEN_HOST:PORT (default 127.0.0.1:8790)
  health   GET local /v1/health (requires serve already up, or use smoke)
"""
from __future__ import annotations

import json
import sys
import urllib.request

from .config import ConfigError, describe, load_config


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in {"-h", "--help"}:
        print(__doc__)
        return 1 if not argv else 0
    cmd = argv[0]
    try:
        cfg = load_config()
    except ConfigError as e:
        print(f"CONFIG ERROR: {e}", file=sys.stderr)
        return 2
    if cmd == "config":
        print(json.dumps(describe(cfg), indent=2))
        return 0
    if cmd == "serve":
        import logging
        import os
        logging.basicConfig(level=os.environ.get("BENCHLINK_LOG_LEVEL", "INFO"),
                            format="%(asctime)s %(levelname)s %(name)s %(message)s")
        from .server import listen_url, serve
        srv = serve(cfg)
        print(f"benchlink bridge-daemon listening on {listen_url(cfg.listen_host, cfg.listen_port)}/v1 "
              f"(public_base={cfg.public_base_url or 'UNSET'})")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0
    if cmd == "health":
        from .server import listen_url
        url = listen_url(cfg.listen_host, cfg.listen_port) + "/v1/health"
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                print(r.status, r.read().decode())
            return 0
        except OSError as e:
            print(f"UPSTREAM ERROR: {e}", file=sys.stderr)
            return 4
    print(f"unknown command {cmd!r}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
