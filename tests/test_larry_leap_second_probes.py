"""Independent review probes; synthetic loopback only, no production edits."""
import json
import sys
from pathlib import Path
import pytest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/smoke"))
sys.path.insert(0, str(ROOT / "tests"))
from benchlink_testkit import Harness
from benchlink_sidecar.ux import BridgeControlShell, TaskInFlight


def second_send_blocked(shell):
    try:
        shell.start_and_wait("review replacement must be refused", max_ticks=1)
    except TaskInFlight:
        return True
    return False


@pytest.mark.parametrize("uncertain", [True, False])
@pytest.mark.parametrize("terminal", ["succeeded", "failed"])
@pytest.mark.parametrize("wrong", ["task_id", "session_id", "both"])
def test_independent_well_formed_wrong_identity_keeps_all_guards(uncertain, terminal, wrong, monkeypatch):
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE":"silent"}) as h:
        original = h.srv.RequestHandlerClass._send
        def send(self, status, payload, type_name=None, list_of=None):
            if self.path == "/v1/tasks" and status == 202 and uncertain:
                raw, status = b'{"type":"error"}', 502
            elif self.path.startswith("/v1/tasks/") and status == 200:
                body = dict(payload, status=terminal, summary="unrelated result")
                if wrong in {"task_id", "both"}: body["task_id"] = "task_wrong"
                if wrong in {"session_id", "both"}: body["session_id"] = "sess_wrong"
                raw = json.dumps(body).encode()
            else:
                return original(self,status,payload,type_name,list_of)
            self.send_response(status);self.send_header("Content-Length",str(len(raw)))
            self.end_headers();self.wfile.write(raw)
        monkeypatch.setattr(h.srv.RequestHandlerClass,"_send",send)
        shell = BridgeControlShell(h.sidecar_cfg,client=h.client,poll_sleep=0)
        shell.start_and_wait("review original",max_ticks=1)
        identity = shell.session_id,shell.task_id
        if uncertain: shell.retry_terminal_error()
        assert shell.has_unfinished_task()
        assert second_send_blocked(shell)
        with pytest.raises(TaskInFlight): shell.end_task()
        assert (shell.session_id,shell.task_id) == identity
        assert len(h.state.tasks)==len(h.state.sessions)==len(h.adapter.transport.dispatched)==1


@pytest.mark.parametrize("stage", ["create", "recovery", "poll"])
@pytest.mark.parametrize("terminal", ["succeeded", "failed"])
def test_invalid_midday_leap_second_terminal_must_not_unlock(stage, terminal, monkeypatch):
    # RFC3339 sections 5.7 / Appendix D: :60 can only be the UTC day-ending
    # inserted leap second, never 16:15 UTC on an ordinary mid-month date.
    invalid_timestamp = "2026-11-05T16:15:60Z"
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE":"silent"}) as h:
        original = h.srv.RequestHandlerClass._send
        def send(self, status, payload, type_name=None, list_of=None):
            if self.path == "/v1/tasks" and status == 202 and stage == "recovery":
                raw,status = b'{"type":"error"}',502
            elif (self.path == "/v1/tasks" and status == 202 and stage == "create") or (self.path.startswith("/v1/tasks/") and status == 200):
                body = dict(payload,status=terminal,summary="untrusted terminal",created_at=invalid_timestamp)
                raw = json.dumps(body).encode()
            else:
                return original(self,status,payload,type_name,list_of)
            self.send_response(status);self.send_header("Content-Length",str(len(raw)))
            self.end_headers();self.wfile.write(raw)
        monkeypatch.setattr(h.srv.RequestHandlerClass,"_send",send)
        shell = BridgeControlShell(h.sidecar_cfg,client=h.client,poll_sleep=0)
        shell.start_and_wait("review original must remain owned",max_ticks=1)
        original_id = shell.task_id
        if stage == "recovery": shell.retry_terminal_error()
        after = {"phase":shell.phase.value,"task_in_flight":shell.has_unfinished_task(),
                 "submission_uncertain":shell.submission_uncertain}
        original_status = h.state.tasks[original_id]["status"]
        blocked = second_send_blocked(shell)
        counts = len(h.state.tasks),len(h.state.sessions),len(h.adapter.transport.dispatched)
        print(json.dumps({"stage":stage,"terminal":terminal,"created_at":invalid_timestamp,
                          "after_reply":after,"original_task_status":original_status,
                          "second_send_blocked":blocked,"task_session_dispatch_counts":counts}))
        assert blocked and counts == (1,1,1), "invalid RFC3339 terminal released ownership and permitted a replacement dispatch"
