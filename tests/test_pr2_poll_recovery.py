"""Readable error cards must not release ownership of accepted Staff work."""
import pytest

from benchlink_sidecar.ux import BridgeControlShell, Phase, TaskInFlight
from benchlink_testkit import Harness


@pytest.mark.parametrize("failure", ["internal", "connection"])
def test_poll_error_card_retry_preserves_task_until_confirmed_terminal(monkeypatch, failure):
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE": "silent"}) as h:
        shell = BridgeControlShell(h.sidecar_cfg, client=h.client, poll_sleep=0)
        shell.start_and_wait("Keep tracking this task", max_ticks=1)
        identity = shell.session_id, shell.task_id
        original_send = h.client.send
        operations = []

        def interrupted_poll(request):
            operations.append(request.operation_id)
            assert request.operation_id == "taskGet"
            if failure == "connection":
                raise OSError("Local connection interruption")
            return 500, {"v": "1", "type": "error", "code": "internal",
                         "message": "Local polling error", "retryable": True,
                         "correlation_id": "req_poll_fixture"}

        monkeypatch.setattr(h.client, "send", interrupted_poll)
        shell.wait_once()
        snapshot = shell.snapshot()
        assert snapshot["phase"] == "error"
        assert snapshot["presentation"]["status"]["label"] == "Error"
        assert snapshot["presentation"]["error"]["title"]
        assert snapshot["task_accepted"] and snapshot["task_in_flight"] and snapshot["can_resume"]
        shell.retry_terminal_error()
        assert (shell.session_id, shell.task_id) == identity
        assert operations == ["taskGet", "taskGet"]
        for action in (shell.end_task, lambda: shell.start_and_wait("Duplicate task")):
            with pytest.raises(TaskInFlight):
                action()

        monkeypatch.setattr(h.client, "send", original_send)
        shell.retry_terminal_error()
        assert shell.phase == Phase.WAIT
        assert (shell.session_id, shell.task_id) == identity
        assert shell.snapshot()["task_in_flight"]
        assert "error" not in shell.snapshot()["presentation"]

        result = dict(h.adapter.transport.dispatched[0]["result_template"],
                      status="failed", summary="Confirmed failure", artifact_links=[],
                      error_class="internal", created_at="2026-11-06T12:00:00Z")
        h.adapter.source.simulate_bot_write(shell.task_id, result)
        shell.wait_once()
        assert not shell.snapshot()["task_in_flight"]
        shell.retry_terminal_error()
        assert shell.phase == Phase.PICK
        assert shell.session_id is shell.task_id is None
        assert shell.goal == "Keep tracking this task"
        assert len(h.state.tasks) == len(h.state.sessions) == len(h.adapter.transport.dispatched) == 1
