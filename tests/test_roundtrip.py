"""Loopback round trip: sidecar -> bridge-daemon -> staff-adapter -> (fake) staff bot -> result -> bridge status."""
import json
import time
import urllib.request

import pytest
from benchlink_testkit import Harness, valid

from benchlink_adapter.results import InMemoryDriveClient
from benchlink_adapter.targets import ETHAN_ID, FLOYD_ID
from benchlink_sidecar.ux import BridgeControlShell, Phase


def bearer(t):
    return {"Authorization": f"Bearer {t}"}


@pytest.mark.parametrize("target,agent", [("floyd", FLOYD_ID), ("ethan", ETHAN_ID)])
def test_sidecar_full_round_trip_local_folder(target, agent):
    with Harness() as h:
        shell = BridgeControlShell(h.sidecar_cfg, client=h.client, poll_sleep=0.05, max_wait_ticks=100)
        assert shell.dry_run_mode is False
        out = shell.start_and_wait(f"Round trip via {target}", target_key=target)
        snap = out["snapshot"]
        assert snap["phase"] == "done" and snap["wait_status"] == "result", snap
        r = snap["result"]
        assert not valid("task.result", r)
        assert r["status"] == "succeeded" and r["agent_id"] == agent
        assert f"Round trip via {target}" in r["summary"] and r["artifact_links"]
        assert r["created_at"].endswith("Z")
        assert h.client.pair_count == 1  # paired automatically with the device key
        # result file landed in the Drive-like layout: <task_id>.json
        assert (h.results_dir / f"{r['task_id']}.json").is_file()
        # bridge status (loopback diagnostics) shows the same task with request id
        with urllib.request.urlopen(h.base + "/_daemon/status") as resp:
            status = json.loads(resp.read())
        row = next(x for x in status["bridge_status"] if x["task_id"] == r["task_id"])
        assert row["status"] == "succeeded" and row["target"] == target and row["request_id"]
        logged = [x for x in status["recent_requests"] if x.get("task_id") == r["task_id"]]
        assert logged and all(x["request_id"] for x in logged) and logged[0]["session_id"] == r["session_id"]
        # session events carry the staff result for the status view
        tok = h.client._live_token
        st, events, _ = h.http("GET", f"/v1/sessions/{r['session_id']}/events", headers=bearer(tok))
        assert st == 200 and len(events) == 1 and not valid("session.event", events[0])
        shell.end_task()
        assert shell.phase == Phase.PICK


def test_default_target_is_floyd_on_wire():
    with Harness() as h:
        tok = h.token()
        st, s, _ = h.session(tok, "floyd")
        st, acc, _ = h.task(tok, s["session_id"], "task_default")
        assert st == 202 and acc["status"] == "accepted" and acc["agent_id"] == FLOYD_ID
        assert not valid("task.result", acc)
        payload = h.adapter.transport.dispatched[0]
        assert payload["target"] == {"key": "floyd", "agent_id": FLOYD_ID}
        assert "access_token" not in payload["task"]
        assert payload["result_delivery"] == {"mode": "local_folder", "folder": str(h.results_dir),
                                              "file_name": "task_default.json"}


def test_target_by_uuid_resolves_to_ethan():
    with Harness() as h:
        tok = h.token()
        st, s, _ = h.session(tok, ETHAN_ID)
        assert st == 201 and s["target_agent"] == ETHAN_ID
        st, acc, _ = h.task(tok, s["session_id"], "task_uuid", target="ethan")
        assert st == 202 and acc["agent_id"] == ETHAN_ID


def test_drive_folder_result_source_with_fake_drive():
    drive = InMemoryDriveClient()
    env = {"BENCHLINK_RESULT_SOURCE": "drive_folder", "BENCHLINK_RESULTS_DRIVE_FOLDER_ID": "folder_test_123",
           "BENCHLINK_RESULTS_POLL_INTERVAL_S": "0"}
    with Harness(adapter_env=env, drive_client=drive) as h:
        tok = h.token()
        _, s, _ = h.session(tok, "ethan")
        st, acc, _ = h.task(tok, s["session_id"], "task_drive_1", target="ethan")
        assert st == 202
        st, r = h.wait_final(tok, "task_drive_1")
        assert r["status"] == "succeeded", r
        assert ("find", "folder_test_123", "task_drive_1.json") in drive.calls
        assert h.adapter.transport.dispatched[0]["result_delivery"] == {
            "mode": "drive_folder", "drive_folder_id": "folder_test_123", "file_name": "task_drive_1.json"}


def test_drive_folder_is_default_and_fails_closed_without_folder():
    with Harness(adapter_env={"BENCHLINK_RESULT_SOURCE": ""}) as h:
        assert h.adapter.source.kind == "drive_folder"
        tok = h.token()
        _, s, _ = h.session(tok)
        st, err, _ = h.task(tok, s["session_id"], "task_nofolder")
        assert st == 503 and err["code"] == "offline" and "DRIVE_FOLDER_ID" in err["message"]
        assert h.adapter.transport.dispatched == []
        st, r, _ = h.get_task(tok, "task_nofolder")
        assert r["status"] == "failed" and r["error_class"] == "offline"


def test_http_callback_result_source_round_trip_and_token_gate():
    env = {"BENCHLINK_RESULT_SOURCE": "http_callback"}
    with Harness(adapter_env=env) as h:
        tok = h.token()
        _, s, _ = h.session(tok)
        st, _, _ = h.task(tok, s["session_id"], "task_cb_1")
        assert st == 202
        st, r = h.wait_final(tok, "task_cb_1")
        assert r["status"] == "succeeded", r
        d = h.adapter.transport.dispatched[0]["result_delivery"]
        assert d["mode"] == "http_callback" and d["url"].endswith("/_bridge/results/task_cb_1")


def test_http_callback_rejects_bad_missing_expired_tokens():
    env = {"BENCHLINK_RESULT_SOURCE": "http_callback", "BENCHLINK_FAKE_TRANSPORT_MODE": "silent"}
    with Harness(adapter_env=env) as h:
        tok = h.token()
        _, s, _ = h.session(tok)
        h.task(tok, s["session_id"], "task_cb_2")
        d = h.adapter.transport.dispatched[0]["result_delivery"]
        result = {"v": "1", "type": "task.result", "session_id": s["session_id"], "task_id": "task_cb_2",
                  "status": "succeeded", "summary": "ok", "artifact_links": [], "error_class": None,
                  "agent_id": "floyd", "created_at": "2026-11-05T12:00:00-05:00"}
        path = "/_bridge/results/task_cb_2"
        st, err, _ = h.http("POST", path, result)
        assert st == 401 and err["code"] == "auth.unpaired"
        st, err, _ = h.http("POST", path, result, {d["token_header"]: "wrong-token"})
        assert st == 403 and err["code"] == "auth.denied"
        h.adapter.source.expire_token_for_test("task_cb_2")
        st, err, _ = h.http("POST", path, result, {d["token_header"]: d["token"]})
        assert st == 401 and err["code"] == "auth.expired"
        st, err, _ = h.http("POST", path, {"v": "1"}, {d["token_header"]: d["token"]})
        assert st == 422 and err["code"] == "validation"


def test_invalid_result_file_fails_explicitly_and_offsets_normalised():
    env = {"BENCHLINK_FAKE_TRANSPORT_MODE": "silent"}
    with Harness(adapter_env=env) as h:
        tok = h.token()
        _, s, _ = h.session(tok)
        h.task(tok, s["session_id"], "task_bad")
        (h.results_dir / "task_bad.json").write_text(json.dumps({"v": "1", "type": "task.result"}))
        st, r = h.wait_final(tok, "task_bad")
        assert r["status"] == "failed" and r["error_class"] == "validation"
        h.task(tok, s["session_id"], "task_offset")
        (h.results_dir / "task_offset.json").write_text(json.dumps({
            "v": "1", "type": "task.result", "session_id": s["session_id"], "task_id": "task_offset",
            "status": "succeeded", "summary": "done", "artifact_links": [], "error_class": None,
            "agent_id": FLOYD_ID, "created_at": "2026-11-05T12:00:00-05:00"}))
        st, r = h.wait_final(tok, "task_offset")
        assert r["status"] == "succeeded" and r["created_at"] == "2026-11-05T17:00:00Z"


def test_fake_fail_mode_reports_failed_result():
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE": "fail"}) as h:
        shell = BridgeControlShell(h.sidecar_cfg, client=h.client, max_wait_ticks=100)
        snap = shell.start_and_wait("will fail")["snapshot"]
        assert snap["phase"] == "error" and snap["error"]["code"] == "internal"


def test_session_close_and_attach():
    with Harness() as h:
        tok = h.token()
        _, s, _ = h.session(tok)
        sid = s["session_id"]
        st, att, _ = h.http("POST", f"/v1/sessions/{sid}/attach",
                            {"v": "1", "type": "session.attach", "access_token": tok, "session_id": sid}, bearer(tok))
        assert st == 200 and not valid("session.created", att)
        st, _, _ = h.http("POST", f"/v1/sessions/{sid}/close", {"v": "1", "type": "session.close",
                                                                "access_token": tok, "session_id": sid,
                                                                "reason": "user_done"}, bearer(tok))
        assert st == 200
        st, err, _ = h.task(tok, sid, "task_after_close")
        assert st == 404 and err["code"] == "session.not_found"
        st, err, _ = h.get_task(tok, "task_missing")
        assert st == 404 and err["code"] == "task.not_found"


def test_sidecar_loopback_server_live_run_poll_end():
    from dataclasses import replace

    from benchlink_sidecar.server import serve as sidecar_serve
    with Harness() as h:
        sc = sidecar_serve(replace(h.sidecar_cfg, listen_port=0))
        threading = __import__("threading")
        threading.Thread(target=sc.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        base = f"http://127.0.0.1:{sc.server_address[1]}"

        def post(path, body=None):
            req = urllib.request.Request(base + path, data=json.dumps(body or {}).encode(), method="POST",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read())
        try:
            out = post("/_sidecar/ux/run", {"target": "ethan", "goal": "HTML send"})
            snap = out["snapshot"]
            for _ in range(50):
                if snap["phase"] != "wait":
                    break
                time.sleep(0.05)
                snap = post("/_sidecar/ux/poll")
            assert snap["phase"] == "done" and "HTML send" in snap["result"]["summary"]
            assert "access_token" not in json.dumps(out["send"].get("task_response", {})).replace('"<redacted>"', "")
            assert post("/_sidecar/ux/end")["phase"] == "pick"
        finally:
            sc.shutdown()
            sc.server_close()
