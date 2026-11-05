#!/usr/bin/env python3
"""Loopback e2e smoke: D3 pairing + Staff task round trip + B1–B4 failure classes.

sidecar (BridgeControlShell + BridgeClient, device-key pairing)
  -> bridge-daemon (127.0.0.1:<ephemeral>)
  -> staff-adapter (fake transport | webhook -> local fake routine receiver)
  -> result via ResultSource (local_folder mimicking Drive | fake Drive | http_callback)
  -> GET /v1/tasks/{id} + /_daemon/status bridge status.
No Tailscale, no real Drive, no live hosts. Exit 0 = all pass.
"""
from __future__ import annotations

import json
import socket
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "tests"))

from benchlink_testkit import Harness, valid  # noqa: E402

from benchlink_adapter.results import InMemoryDriveClient  # noqa: E402
from benchlink_sidecar.config import load_config as sidecar_config  # noqa: E402
from benchlink_sidecar.ux import BridgeControlShell  # noqa: E402

RESULTS: list[tuple[bool, str]] = []


def check(ok, name):
    RESULTS.append((bool(ok), name))
    print(("PASS " if ok else "FAIL ") + name)


def run_shell(h, goal, target="floyd", ticks=100):
    shell = BridgeControlShell(h.sidecar_cfg, client=h.client, poll_sleep=0.05, max_wait_ticks=ticks)
    return shell.start_and_wait(goal, target_key=target)["snapshot"]


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def main() -> int:
    print("== D3 pairing ==")
    with Harness() as h:
        st, tok, ch = h.pair_raw()
        check(st == 200 and not valid("auth.pair.challenge", ch) and not valid("auth.session.token", tok),
              "pair request->challenge->complete->auth.session.token (schema-valid)")
        check("refresh_token" not in tok and tok["expires_at"].endswith("Z"), "no refresh token; expires_at RFC3339 Z")
        t = tok["access_token"]
        _, pong, hdr = h.http("GET", "/v1/health", headers={"Authorization": f"Bearer {t}"})
        check(pong["auth"] == "paired" and hdr.get("X-Benchlink-Tailscale-Identity") == "none",
              "authed health auth=paired + X-Benchlink-Tailscale-Identity header")
        check(h.http("GET", "/v1/health")[1]["auth"] == "unpaired", "unauthed health auth=unpaired")
        check(h.pair_raw(sign=False)[0] == 403, "bad signature -> 403 auth.denied")
        check(h.pair_raw(scopes=["session", "task"])[0] == 422, "non-contract scopes -> 422 validation")
    with Harness(enroll=False) as h:
        check(h.pair_raw()[1].get("code") == "auth.denied", "unknown device -> auth.denied")
    with Harness(daemon_env={"BENCHLINK_ACCESS_TTL_SECONDS": "1"}) as h:
        t = h.token(); time.sleep(1.2)
        st, err, _ = h.session(t)
        check(st == 401 and err["code"] == "auth.expired", "TTL=1s: expired bearer -> 401 auth.expired")
        c = h.client
        c.send(c.build_session_create("floyd")); time.sleep(1.2)
        st, _ = c.send(c.build_session_create("floyd"))
        check(st == 201 and c.pair_count == 2, "sidecar re-pairs once on auth.expired and succeeds")

    print("== A: round trip (fake transport) ==")
    for target in ("floyd", "ethan"):
        with Harness() as h:
            snap = run_shell(h, f"Smoke round trip -> {target}", target)
            r = snap.get("result") or {}
            check(snap["phase"] == "done" and r.get("status") == "succeeded" and not valid("task.result", r),
                  f"[local_folder] sidecar->daemon->adapter->{target}->result: {r.get('summary')!r}")
            with urllib.request.urlopen(h.base + "/_daemon/status") as resp:
                status = json.loads(resp.read())
            row = [x for x in status["bridge_status"] if x["task_id"] == r.get("task_id")]
            check(row and row[0]["status"] == "succeeded" and row[0]["request_id"],
                  f"bridge status shows {target} task succeeded (request_id={row[0]['request_id'] if row else None})")
    drive = InMemoryDriveClient()
    with Harness(adapter_env={"BENCHLINK_RESULT_SOURCE": "drive_folder",
                              "BENCHLINK_RESULTS_DRIVE_FOLDER_ID": "folder_smoke",
                              "BENCHLINK_RESULTS_POLL_INTERVAL_S": "0"}, drive_client=drive) as h:
        snap = run_shell(h, "Smoke via fake Drive folder")
        check(snap["phase"] == "done", "[drive_folder default, fake Drive] round trip done")
    with Harness(adapter_env={"BENCHLINK_RESULT_SOURCE": "http_callback"}) as h:
        snap = run_shell(h, "Smoke via callback")
        check(snap["phase"] == "done", "[http_callback] round trip done")

    print("== A: webhook transport -> local fake routine receiver ==")
    from test_webhook import Receiver
    for src in ("http_callback", "local_folder"):
        with Receiver() as rx, Harness(adapter_env={
                "BENCHLINK_ADAPTER_TRANSPORT": "webhook", "BENCHLINK_RESULT_SOURCE": src,
                "BENCHLINK_TARGET_ETHAN_WEBHOOK_URL": rx.url,
                "BENCHLINK_TARGET_ETHAN_SENDER_KEY": "test-sender-key"}) as h:
            snap = run_shell(h, "Webhook canary", "ethan")
            hdr_ok = rx.requests and rx.requests[0]["headers"].get("Authorization") == "Bearer test-sender-key"
            check(snap["phase"] == "done" and hdr_ok,
                  f"[webhook+{src}] receiver got sender key, bot result back: {snap.get('result', {}).get('summary')!r}")

    print("== B1–B4 failure classes ==")
    with Harness() as h:
        cfg = sidecar_config({"BENCHLINK_BRIDGE_BASE_URL": h.base,
                              "BENCHLINK_DEVICE_KEY_PATH": str(h.dir / "missing.pem")})
        e = BridgeControlShell(cfg).start_and_wait("x")["snapshot"]["error"]
        check(e["code"] == "auth.unpaired", "B1 no token/no key -> auth.unpaired")
        st, err, _ = h.session("bogus-token-000000000000")
        check(st == 401 and err["code"] == "auth.unpaired" and not valid("error", err), "B1 bogus bearer -> 401")
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE": "reject"}) as h:
        check(run_shell(h, "x")["error"]["code"] == "auth.denied", "B1 staff webhook rejects sender key -> auth.denied")
    check(BridgeControlShell(sidecar_config({})).dry_run_mode, "B2 bridge URL unset -> fail closed (dry-run banner)")
    cfg = sidecar_config({"BENCHLINK_BRIDGE_BASE_URL": f"http://127.0.0.1:{free_port()}",
                          "BENCHLINK_ACCESS_TOKEN": "tok_unused_0123456789"})
    check(BridgeControlShell(cfg).start_and_wait("x")["snapshot"]["error"]["code"] == "offline",
          "B2 bridge down -> offline")
    with Harness(adapter_env={"BENCHLINK_ADAPTER_TRANSPORT": "webhook"}) as h:
        e = run_shell(h, "x")["error"]
        check(e["code"] == "offline" and e["http"] == 503, "B2 target webhook unset -> 503 offline")
    with Harness(adapter_env={"BENCHLINK_ADAPTER_TRANSPORT": "webhook",
                              "BENCHLINK_TARGET_FLOYD_WEBHOOK_URL": f"http://127.0.0.1:{free_port()}/h",
                              "BENCHLINK_TARGET_FLOYD_SENDER_KEY": "k"}) as h:
        check(run_shell(h, "x")["error"]["code"] == "offline", "B2 routine webhook unreachable -> offline")
    with Harness() as h:
        t = h.token()
        st, err, _ = h.session(t, "zed-not-a-bot")
        check(st == 422 and err["code"] == "routing", "B3 unknown target -> routing")
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE": "silent", "BENCHLINK_TASK_TIMEOUT_S": "0.3"}) as h:
        snap = run_shell(h, "never answered", ticks=40)
        check(snap["error"] and snap["error"]["code"] == "timeout", "B4 no result within task timeout -> timeout")
    with Receiver(delay=1.0) as rx, Harness(adapter_env={
            "BENCHLINK_ADAPTER_TRANSPORT": "webhook", "BENCHLINK_TARGET_FLOYD_WEBHOOK_URL": rx.url,
            "BENCHLINK_TARGET_FLOYD_SENDER_KEY": "test-sender-key", "BENCHLINK_WEBHOOK_TIMEOUT_S": "0.2"}) as h:
        check(run_shell(h, "slow")["error"]["code"] == "timeout", "B4 webhook dispatch timeout -> timeout")

    failed = [n for ok, n in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    for n in failed:
        print(" - FAILED", n)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
