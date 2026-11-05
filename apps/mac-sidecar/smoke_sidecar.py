#!/usr/bin/env python3
"""Offline smoke for apps/mac-sidecar. No real Tailscale, no live DNS.

Asserts CLI exit-code contract, fail-closed base URL (incl. intended-but-unset
https://inference.tail58d565.ts.net:19740 and demoted floyds-labs.ts.net),
schema-validates dry-run bodies for all OpenAPI ops, exercises loopback stub,
and runs Bridge Control UX Pick→Compose/Send→Wait state machine.
Exit 0 = all checks pass; 1 = any check failed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SCHEMAS = ROOT / "packages/bridge-protocol/schemas"
sys.path.insert(0, str(HERE))

from benchlink_sidecar.client import BridgeClient  # noqa: E402
from benchlink_sidecar.config import PLACEHOLDER_BASE_URL, load_config  # noqa: E402
from benchlink_sidecar.server import serve  # noqa: E402
from benchlink_sidecar.ux import (  # noqa: E402
    DEFAULT_TARGET,
    BridgeControlShell,
    Phase,
)

RESULTS: list[tuple[bool, str]] = []
CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("BENCHLINK_")}

EXPECTED_OPS = {
    "health", "authPair", "sessionCreate", "sessionAttach", "sessionMessage",
    "sessionEvents", "sessionClose", "taskCreate", "taskGet",
}


def check(ok: bool, name: str):
    RESULTS.append((ok, name))
    print(("PASS " if ok else "FAIL ") + name)


def cli(args, extra_env=None):
    env = dict(CLEAN_ENV, PYTHONPATH=str(HERE), **(extra_env or {}))
    p = subprocess.run([sys.executable, "-m", "benchlink_sidecar", *args],
                       cwd=HERE, env=env, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def expect_exit(name, args, want, extra_env=None, stderr_has=None):
    rc, out, err = cli(args, extra_env)
    ok = rc == want and (stderr_has is None or stderr_has in err)
    check(ok, f"{name}: exit={rc} (want {want})")
    return out


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


class FakeDaemon(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/v1/health":
            body = json.dumps({"v": "1", "type": "health.pong", "daemon_version": "fake-0",
                               "uptime_s": 1, "auth": "unpaired", "runtime": "inference",
                               "transport": "tailscale-https"}).encode()
            self.send_response(200)
        else:
            body = b"{}"
            self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def start(srv):
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def main() -> int:
    print("== CLI exit-code contract ==")
    expect_exit("config parse, URL unset", ["config"], 0)
    out = expect_exit("dry-run, URL unset", ["dry-run"], 0)
    expect_exit("health refuses when URL unset", ["health"], 3,
                stderr_has="BENCHLINK_BRIDGE_BASE_URL is unset")
    expect_exit("health refuses intended placeholder host", ["health"], 3,
                {"BENCHLINK_BRIDGE_BASE_URL": PLACEHOLDER_BASE_URL},
                stderr_has="placeholder")
    expect_exit("health refuses demoted floyds-labs host", ["health"], 3,
                {"BENCHLINK_BRIDGE_BASE_URL": "https://bridge.floyds-labs.ts.net"},
                stderr_has="placeholder")
    expect_exit("config parse, intended placeholder set", ["config"], 0,
                {"BENCHLINK_BRIDGE_BASE_URL": PLACEHOLDER_BASE_URL + "/v1"})
    expect_exit("reject non-URL", ["config"], 2, {"BENCHLINK_BRIDGE_BASE_URL": "not a url"})
    expect_exit("reject Build Session API port 63301", ["config"], 2,
                {"BENCHLINK_BRIDGE_BASE_URL": "http://127.0.0.1:63301"})
    expect_exit("reject plain http to non-loopback", ["config"], 2,
                {"BENCHLINK_BRIDGE_BASE_URL": "http://inference.example.ts.net"})
    expect_exit("reject non-loopback listen host", ["config"], 2,
                {"BENCHLINK_SIDECAR_LISTEN_HOST": "0.0.0.0"})
    expect_exit("TOML config file parses", ["config"], 0,
                {"BENCHLINK_SIDECAR_CONFIG": str(HERE / "config.example.toml")})

    print("== dry-run bodies vs schemas (all OpenAPI ops) ==")
    shapes = {r["operationId"]: r for r in json.loads(out or "[]")}
    check(set(shapes) == EXPECTED_OPS,
          f"dry-run operationIds: {sorted(shapes)} (want {sorted(EXPECTED_OPS)})")
    check(shapes.get("health", {}).get("url") == "{BENCHLINK_BRIDGE_BASE_URL}/v1/health",
          "dry-run health URL templated, no host invented")
    check(PLACEHOLDER_BASE_URL == "https://inference.tail58d565.ts.net:19740",
          f"intended placeholder constant = {PLACEHOLDER_BASE_URL}")
    validate = schema_validator()
    if validate is None:
        print("SKIP schema validation (jsonschema not installed)")
    else:
        cfg = load_config({"BENCHLINK_ACCESS_TOKEN": "tok_smoke_0123456789ab"})
        c = BridgeClient(cfg)
        for name, req, sch in [
            ("auth.pair.request", c.build_auth_pair("mac-smoke-device", dry_run=True),
             "auth_pair_request.schema.json"),
            ("session.create", c.build_session_create("floyd", {"origin": "smoke"}, dry_run=True),
             "session_create.schema.json"),
            ("session.attach", c.build_session_attach("sess_demo_1", dry_run=True),
             "session_attach.schema.json"),
            ("session.message", c.build_session_message("sess_demo_1", "msg_demo_1", "hi", dry_run=True),
             "session_message.schema.json"),
            ("session.close", c.build_session_close("sess_demo_1", dry_run=True),
             "session_close.schema.json"),
            ("task.create", c.build_task_create("sess_demo_1", "task_demo_1",
                                                "Confirm bridge canary routing",
                                                target_agent="floyd", dry_run=True),
             "task_create.schema.json"),
        ]:
            errs = validate(sch, req.body)
            check(not errs, f"{name} body valid vs {sch} {errs or ''}")

    print("== Bridge Control UX Pick→Compose→Wait ==")
    cfg = load_config({})  # URL unset → dry-run / fail-closed
    shell = BridgeControlShell(cfg)
    check(shell.dry_run_mode is True and shell.offline_banner is not None,
          "UX fail-closed: dry-run + offline banner when URL unset")
    check(shell.phase == Phase.PICK and shell.target_key == DEFAULT_TARGET,
          f"UX starts at Pick (Floyd default={DEFAULT_TARGET})")
    locks = shell.soft_locks()
    check(all(locks.values()), f"soft locks O1–O6 all true: {locks}")
    # O1: cannot mid-task handoff
    shell.pick("ethan")
    shell.compose("Canary check", high_priority=False)
    shell.send()
    try:
        shell.pick("floyd")
        o1_ok = False
    except RuntimeError as e:
        o1_ok = "O1" in str(e)
    check(o1_ok, "O1 blocks mid-WAIT handoff")
    wait = shell.wait_until_done()
    check(wait["phase"] == "done" and wait["wait_status"] == "result",
          f"offline Wait reaches result: phase={wait['phase']} status={wait['wait_status']}")
    shell.end_task()
    check(shell.phase == Phase.PICK and shell.session_id is None,
          "End task → re-pick (O1/O4)")
    # Full happy path + CLI
    out_ux = expect_exit("ux CLI demo exit 0", ["ux", "Smoke goal from CLI"], 0)
    check('"ok": true' in out_ux.replace(" ", "").lower() or '"ok": true' in out_ux or '"ok":true' in out_ux.replace(" ", ""),
          "ux CLI prints ok JSON")
    # O3: default HP off in happy path
    shell2 = BridgeControlShell(load_config({}))
    hp = shell2.run_happy_path()
    check(hp["final_before_end"]["high_priority"] is False,
          "O3 HP ping default off in happy path")
    check(hp["send"]["task.create"]["body"]["reply_destination"] == "bridge_status",
          "O3 reply_destination=bridge_status by default")
    check(hp["send"]["task.create"]["body"]["priority"] == "normal",
          "O3 priority=normal by default")

    print("== loopback stub, upstream UNSET ==")
    cfg = load_config({})
    from dataclasses import replace
    stub = start(serve(replace(cfg, listen_port=0)))
    port = stub.server_address[1]
    st, body = get(f"http://127.0.0.1:{port}/v1/health")
    check(st == 503 and body.get("type") == "error" and body.get("code") == "offline",
          f"stub /v1/health unset -> {st} {body.get('code')}")
    if validate:
        check(not validate("error.schema.json", body), "stub 503 body valid vs error.schema.json")
    st, body = get(f"http://127.0.0.1:{port}/_sidecar/status")
    check(st == 200 and body.get("upstream_configured") is False, "stub /_sidecar/status reports unset")
    # HTML shell
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as r:
            html = r.read().decode()
            check(r.status == 200 and "Bridge Control" in html and "Pick" in html,
                  "loopback HTML Bridge Control shell served")
    except Exception as e:  # noqa: BLE001
        check(False, f"HTML shell fetch failed: {e}")
    stub.shutdown()

    print("== loopback stub -> fake upstream (127.0.0.1, ephemeral) ==")
    # NOTE: do NOT call inference.tail58d565.ts.net:19740 — HTTPS serve not ready
    fake = start(ThreadingHTTPServer(("127.0.0.1", 0), FakeDaemon))
    fport = fake.server_address[1]
    cfg2 = load_config({"BENCHLINK_BRIDGE_BASE_URL": f"http://127.0.0.1:{fport}"})
    stub2 = start(serve(replace(cfg2, listen_port=0)))
    st, body = get(f"http://127.0.0.1:{stub2.server_address[1]}/v1/health")
    check(st == 200 and body.get("type") == "health.pong", f"stub proxies GET /v1/health -> {st}")
    if validate:
        check(not validate("health_pong.schema.json", body), "proxied pong valid vs health_pong.schema.json")
    rc, _, _ = cli(["health"], {"BENCHLINK_BRIDGE_BASE_URL": f"http://127.0.0.1:{fport}"})
    check(rc == 0, f"CLI health against fake upstream exit={rc} (want 0)")
    stub2.shutdown()
    fake.shutdown()

    failed = [n for ok, n in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    if failed:
        print("FAILED:")
        for n in failed:
            print(" -", n)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
