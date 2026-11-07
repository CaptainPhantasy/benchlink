"""PR2: target selection, in-flight refusal, send safety (Amir polish ruling 1)."""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import replace

from benchlink_testkit import Harness

from benchlink_sidecar.server import serve
from benchlink_sidecar.ui.copy import TASK_IN_FLIGHT_BODY, TASK_IN_FLIGHT_TITLE
from benchlink_sidecar.ux import ROSTER


def _sidecar(h):
    sc = serve(replace(h.sidecar_cfg, listen_port=0))
    threading.Thread(target=sc.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    return sc, f"http://127.0.0.1:{sc.server_address[1]}"


def _post(base, path, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(base + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_roster_display_is_name_dot_role():
    for e in ROSTER:
        assert "·" in e.display, e
        # No bare first-name-only display left for leads
        assert e.display != e.key


def test_run_refuses_empty_goal_without_placeholder():
    with Harness() as h:
        sc, base = _sidecar(h)
        try:
            st, body = _post(base, "/_sidecar/ux/run", {"target": "ethan", "goal": "  "})
            assert st == 422
            assert body["type"] == "error" and body["code"] == "validation"
            assert "Bridge Control HTML send" not in json.dumps(body)
            st2, body2 = _post(base, "/_sidecar/ux/run", {"target": "ethan"})
            assert st2 == 422 and body2["code"] == "validation"
        finally:
            sc.shutdown(); sc.server_close()


def test_unknown_target_returns_routing():
    with Harness() as h:
        sc, base = _sidecar(h)
        try:
            st, body = _post(base, "/_sidecar/ux/run",
                             {"target": "nobody", "goal": "route me"})
            assert st == 422
            assert body["type"] == "error" and body["code"] == "routing"
            # User-facing presentation available when we attach it
            assert "O1" not in json.dumps(body)
        finally:
            sc.shutdown(); sc.server_close()


def test_inflight_send_returns_409_and_leaves_task_untouched():
    # Deterministic path: silent fake so task stays queued
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE": "silent",
                              "BENCHLINK_TASK_TIMEOUT_S": "30"}) as h:
        sc, base = _sidecar(h)
        try:
            st, out = _post(base, "/_sidecar/ux/run",
                            {"target": "floyd", "goal": "in flight probe", "max_ticks": 1})
            assert st == 200
            snap = out["snapshot"]
            assert snap["phase"] == "wait", snap
            task_id = snap["task_id"]
            # Forced second send must 409 and leave the running task alone
            st2, body2 = _post(base, "/_sidecar/ux/run",
                               {"target": "ethan", "goal": "should not start"})
            assert st2 == 409, body2
            assert body2["type"] == "error" and body2["code"] == "validation"
            assert TASK_IN_FLIGHT_BODY in body2["message"]
            assert "O1" not in body2["message"]
            assert body2["presentation"]["error"]["title"] == TASK_IN_FLIGHT_TITLE
            # Running task untouched
            st3, poll = _post(base, "/_sidecar/ux/poll")
            assert st3 == 200
            assert poll["phase"] == "wait"
            assert poll["task_id"] == task_id
            assert poll["target"] == "floyd"
            assert poll["goal"] == "in flight probe"
        finally:
            sc.shutdown(); sc.server_close()


def test_html_has_radiogroup_and_no_o1_user_copy():
    with Harness() as h:
        sc, base = _sidecar(h)
        try:
            with urllib.request.urlopen(base + "/", timeout=3) as r:
                html = r.read().decode()
            assert 'role="radiogroup"' in html
            assert "End the current task to change target" in html or "rosterLocked" in html
            assert "O1" not in html  # no internal codes in UI
            assert "Bridge Control HTML send" not in html
        finally:
            sc.shutdown(); sc.server_close()


def test_after_end_repick_and_send_works():
    with Harness() as h:
        sc, base = _sidecar(h)
        try:
            st, out = _post(base, "/_sidecar/ux/run",
                            {"target": "ethan", "goal": "first task", "max_ticks": 50})
            assert st == 200
            # Finish if still waiting
            snap = out["snapshot"]
            for _ in range(50):
                if snap["phase"] != "wait":
                    break
                time.sleep(0.05)
                _, snap = _post(base, "/_sidecar/ux/poll")
            _, ended = _post(base, "/_sidecar/ux/end")
            assert ended["phase"] == "pick"
            st2, out2 = _post(base, "/_sidecar/ux/run",
                              {"target": "floyd", "goal": "second task", "max_ticks": 50})
            assert st2 == 200
            snap2 = out2["snapshot"]
            for _ in range(50):
                if snap2["phase"] != "wait":
                    break
                time.sleep(0.05)
                _, snap2 = _post(base, "/_sidecar/ux/poll")
            assert snap2["phase"] == "done"
            assert snap2["target"] == "floyd"
        finally:
            sc.shutdown(); sc.server_close()
