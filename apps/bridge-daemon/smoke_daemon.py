#!/usr/bin/env python3
"""Offline smoke for apps/bridge-daemon skeleton. No Tailscale, no live DNS.

Starts daemon on ephemeral 127.0.0.1 port, validates health.pong schema,
checks unauthenticated task/session paths fail closed (401/403), confirms intended public base stays unset/fail-closed.
Does NOT call https://inference.tail58d565.ts.net:19740 (serve not ready).
"""
from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SCHEMAS = ROOT / "packages/bridge-protocol/schemas"
sys.path.insert(0, str(HERE))

from benchlink_daemon.config import PLACEHOLDER_BASE_URL, load_config  # noqa: E402
from benchlink_daemon.server import serve  # noqa: E402

RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, name: str):
    RESULTS.append((ok, name))
    print(("PASS " if ok else "FAIL ") + name)


def schema_validator():
    try:
        from jsonschema import Draft202012Validator
        from referencing import Registry, Resource
        from referencing.jsonschema import DRAFT202012
    except Exception:
        return None
    res = []
    for p in SCHEMAS.glob("*.schema.json"):
        raw = json.loads(p.read_text())
        res.append((raw["$id"], Resource.from_contents(raw, default_specification=DRAFT202012)))
    reg = Registry().with_resources(res)

    def validate(schema_file, data):
        s = json.loads((SCHEMAS / schema_file).read_text())
        return [e.message for e in Draft202012Validator(s, registry=reg).iter_errors(data)]
    return validate


def query_token_fixture_url(base: str) -> str:
    """A visibly synthetic runtime sentinel, never a bearer or stored credential."""
    fake_query_token = "fixture-not-a-real-token-" + str(os.getpid())
    return base + "/v1/tasks/task_x?" + urlencode({"access_token": fake_query_token})


def http_json(method, url, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def main() -> int:
    print("== config ==")
    cfg = load_config({})
    check(cfg.public_base_url is None and not cfg.public_configured,
          "public base unset / not configured (fail closed)")
    check(PLACEHOLDER_BASE_URL == "https://inference.tail58d565.ts.net:19740",
          f"intended placeholder = {PLACEHOLDER_BASE_URL}")
    cfg_ph = load_config({"BENCHLINK_BRIDGE_PUBLIC_BASE_URL": PLACEHOLDER_BASE_URL})
    check(cfg_ph.public_base_url == PLACEHOLDER_BASE_URL and not cfg_ph.public_configured,
          "intended placeholder set but public_configured=False without allow flag")
    try:
        load_config({"BENCHLINK_BRIDGE_LISTEN_PORT": "63301"})
        check(False, "reject listen port 63301")
    except Exception as e:
        check("63301" in str(e) or "not allowed" in str(e), f"reject listen port 63301: {e}")

    print("== ephemeral loopback serve ==")
    from dataclasses import replace
    cfg = replace(load_config({}), listen_port=0)
    from benchlink_adapter.config import AdapterConfigError
    try:
        serve(cfg, adapter_env={})
        check(False, "default drive_folder refuses to start without BENCHLINK_DRIVE_SA_KEY_PATH")
    except AdapterConfigError as e:
        check("BENCHLINK_DRIVE_SA_KEY_PATH" in str(e),
              "default drive_folder refuses to start without BENCHLINK_DRIVE_SA_KEY_PATH")
    srv = serve(cfg, adapter_env={"BENCHLINK_ADAPTER_TRANSPORT": "fake",
                                  "BENCHLINK_RESULT_SOURCE": "http_callback"})
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address
    base = f"http://{host}:{port}"

    st, body = http_json("GET", f"{base}/v1/health")
    check(st == 200 and body.get("type") == "health.pong", f"GET /v1/health -> {st} {body.get('type')}")
    check(body.get("runtime") == "inference" and body.get("transport") == "tailscale-https",
          f"pong runtime/transport: {body.get('runtime')}/{body.get('transport')}")
    validate = schema_validator()
    if validate is None:
        print("SKIP schema validation (jsonschema not installed)")
    else:
        errs = validate("health_pong.schema.json", body)
        check(not errs, f"health.pong schema-valid {errs or ''}")
        st401, err = http_json("POST", f"{base}/v1/sessions",
                               {"v": "1", "type": "session.create"})
        check(st401 == 401 and err.get("code") == "auth.unpaired",
              f"POST /v1/sessions without bearer -> {st401} {err.get('code')}")
        check(not validate("error.schema.json", err), "401 body valid vs error.schema.json")

    st, err = http_json("POST", f"{base}/v1/auth/pair", {"v": "1"})
    check(st == 422 and err.get("code") == "validation", f"POST /v1/auth/pair bad body -> {st}")
    st, err = http_json("POST", f"{base}/v1/tasks", {"v": "1"})
    check(st == 401 and err.get("code") == "auth.unpaired", f"POST /v1/tasks without bearer -> {st}")
    st, err = http_json("GET", f"{base}/v1/tasks/task_x")
    check(st == 401 and err.get("code") == "auth.unpaired", f"GET /v1/tasks/{{id}} without bearer -> {st}")
    st, err = http_json("GET", query_token_fixture_url(base))
    check(st == 403 and err.get("code") == "auth.denied", f"token in query rejected -> {st}")

    srv.shutdown()
    failed = [n for ok, n in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    if failed:
        print("FAILED:")
        for n in failed:
            print(" -", n)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
