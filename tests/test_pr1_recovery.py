"""Minimal PR1 lifecycle corrections, real loopback HTTP and controlled races."""
import io
import json
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace

import pytest
from benchlink_testkit import Harness

from benchlink_sidecar import client as client_module
from benchlink_sidecar import server as server_module
from benchlink_sidecar.client import BridgeClient
from benchlink_sidecar.ux import BridgeControlShell


@contextmanager
def local_sidecar(h):
    server = server_module.serve(replace(h.sidecar_cfg, listen_port=0))
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval":.01}, daemon=True).start()
    base = "http://127.0.0.1:" + str(server.server_address[1])

    def call(path, body=None, method="POST"):
        request = urllib.request.Request(base + path, method=method,
            data=json.dumps(body or {}).encode() if method == "POST" else None,
            headers={"Content-Type":"application/json"})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())
    try:
        yield call, server, base
    finally:
        server.shutdown()
        server.server_close()


def result_for(h, status="succeeded", error=None):
    payload = h.adapter.transport.dispatched[-1]
    return dict(payload["result_template"], status=status, summary="Local recovery fixture",
                artifact_links=[], error_class=error, created_at="2026-11-05T16:00:00-05:00")


def test_status_reports_live_shell_and_second_send_cannot_replace_wait():
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE":"silent"}) as h, local_sidecar(h) as (call, _, _):
        status, out = call("/_sidecar/ux/run", {"goal":"first task", "max_ticks":1})
        first = out["snapshot"]
        assert status == 200 and first["phase"] == "wait"
        status, current = call("/_sidecar/ux", method="GET")
        assert current["task_id"] == first["task_id"] and current["phase"] == "wait"
        assert call("/_sidecar/ux/run", {"goal":"forbidden replacement", "max_ticks":1})[0] == 409
        assert len(h.adapter.transport.dispatched) == len(h.state.tasks) == len(h.state.sessions) == 1
        assert call("/_sidecar/ux", method="GET")[1]["task_id"] == first["task_id"]


def test_end_cannot_be_undone_by_poll_waiting_on_upstream(monkeypatch):
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE":"silent"}) as h, local_sidecar(h) as (call, sidecar, _):
        first = call("/_sidecar/ux/run", {"goal":"serialized task", "max_ticks":1})[1]["snapshot"]
        h.adapter.source.simulate_bot_write(first["task_id"], result_for(h))
        entered, release, end_received = threading.Event(), threading.Event(), threading.Event()
        original_send = BridgeClient.send
        def blocked_send(self, request):
            response = original_send(self, request)
            if request.operation_id == "taskGet":
                entered.set()
                assert release.wait(5)
            return response
        monkeypatch.setattr(BridgeClient, "send", blocked_send)
        original_post = sidecar.RequestHandlerClass.do_POST
        def received(self):
            if self.path.endswith("/end"):
                end_received.set()
            return original_post(self)
        monkeypatch.setattr(sidecar.RequestHandlerClass, "do_POST", received)
        with ThreadPoolExecutor(2) as pool:
            poll = pool.submit(call, "/_sidecar/ux/poll")
            assert entered.wait(5)
            end = pool.submit(call, "/_sidecar/ux/end")
            try:
                assert end_received.wait(5)
                assert end.result(timeout=5)[0] == 409, "End raced an older poll"
            finally:
                release.set()
            before_end = poll.result(timeout=5)[1]
            assert end.result(timeout=5)[0] == 409
            ended = call("/_sidecar/ux/end")[1]
        assert before_end["task_id"] == first["task_id"] and before_end["session_id"] == first["session_id"]
        assert ended["phase"] == "pick" and ended["task_id"] is ended["session_id"] is ended["result"] is None
        assert call("/_sidecar/ux", method="GET")[1]["phase"] == "pick"


@pytest.mark.parametrize("raw", [b"<html>Bad gateway</html>", b"", b"[]", b'{"type":"error"}'])
def test_non_envelope_poll_502_closes_wait_and_retry_only_gets_same_task(raw, monkeypatch):
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE":"silent"}) as h:
        shell = BridgeControlShell(h.sidecar_cfg, client=h.client, poll_sleep=0)
        shell.start_and_wait("poll fixture", max_ticks=1)
        identity = shell.task_id, shell.session_id
        original_open = client_module.open_no_redirects
        def bad_gateway(request, **kw):
            if request.get_method() == "GET" and "/tasks/" in request.full_url:
                raise urllib.error.HTTPError(request.full_url, 502, "Local gateway", {}, io.BytesIO(raw))
            return original_open(request, **kw)
        monkeypatch.setattr(client_module, "open_no_redirects", bad_gateway)
        shell.wait_once()
        assert shell.phase.value == "error" and shell.error["code"] == "offline"
        assert shell.can_resume_interrupted_wait()
        with pytest.raises(RuntimeError):
            shell.start_and_wait("forbidden replacement")
        monkeypatch.setattr(client_module, "open_no_redirects", original_open)
        h.adapter.source.simulate_bot_write(identity[0], result_for(h))
        shell.retry_terminal_error()
        assert shell.phase.value == "done" and (shell.task_id, shell.session_id) == identity
        assert len(h.adapter.transport.dispatched) == 1


@pytest.mark.parametrize("code", ["offline", "internal", "timeout"])
def test_terminal_retry_keeps_draft_but_requires_fresh_explicit_send(code):
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE":"silent"}) as h:
        shell = BridgeControlShell(h.sidecar_cfg, client=h.client, poll_sleep=0)
        shell.start_and_wait("preserved goal", target_key="ethan", high_priority=True, max_ticks=1)
        first = shell.task_id
        h.adapter.source.simulate_bot_write(first, result_for(h, "failed", code))
        shell.wait_once()
        assert shell.phase.value == "error"
        assert hasattr(shell, "retry_terminal_error"), "terminal Retry is missing"
        assert not shell.can_resume_interrupted_wait()
        shell.retry_terminal_error()
        assert shell.phase.value == "pick" and shell.task_id is shell.session_id is None
        assert (shell.goal, shell.target_key, shell.high_priority) == ("preserved goal", "ethan", True)
        shell.retry_terminal_error()
        assert len(h.adapter.transport.dispatched) == 1
        h.adapter.transport.mode = "echo"
        shell.start_and_wait(shell.goal, target_key=shell.target_key, high_priority=shell.high_priority, max_ticks=100)
        assert shell.task_id != first and len(h.adapter.transport.dispatched) == 2


@pytest.mark.parametrize("wrapped", [False, True])
def test_ordinary_5_second_reply_deadline_is_timeout_not_offline(wrapped, monkeypatch):
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE":"silent"}) as h:
        shell = BridgeControlShell(h.sidecar_cfg, client=h.client, poll_sleep=0)
        shell.start_and_wait("5s read fixture", max_ticks=1)
        budgets = []
        def deadline(request, **kwargs):
            budgets.append(kwargs["timeout"])
            error = TimeoutError("local fixture deadline")
            raise urllib.error.URLError(error) if wrapped else error
        monkeypatch.setattr(client_module, "open_no_redirects", deadline)
        shell.wait_once()
        assert budgets == [5.0]
        assert shell.error["code"] == "timeout" and shell.can_resume_interrupted_wait()


def test_dispatch_budget_outlasts_default_ten_second_webhook_and_is_configurable(monkeypatch):
    from benchlink_adapter.config import load_adapter_config
    with Harness() as h:
        budgets = []
        class Response:
            status = 202
            def read(self):
                return json.dumps({"v":"1", "type":"task.result", "task_id":"task_deadline", "session_id":"sess_deadline", "status":"accepted", "summary":"", "artifact_links":[], "agent_id":"floyd", "created_at":"2026-11-05T16:00:00-05:00"}).encode()
            def __enter__(self): return self
            def __exit__(self, *args): pass
        def delayed_response(request, **kwargs):
            budgets.append(kwargs["timeout"])
            assert kwargs["timeout"] > load_adapter_config({}) .webhook_timeout_s == 10.0
            return Response()
        monkeypatch.setattr(client_module, "open_no_redirects", delayed_response)
        request = h.client.build_task_create("sess_deadline", "task_deadline", "deadline fixture", dry_run=False)
        assert h.client._raw_send(request)[0] == 202
        assert budgets == [15.0]
        custom = BridgeClient(h.sidecar_cfg, timeout=20, dispatch_timeout=11)
        custom._raw_send(request)
        assert budgets == [15.0, 20]


def test_live_501_result_does_not_become_simulated_success(monkeypatch):
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE":"silent"}) as h:
        shell = BridgeControlShell(h.sidecar_cfg, client=h.client, poll_sleep=0)
        shell.start_and_wait("not simulated", max_ticks=1)
        monkeypatch.setattr(h.client, "send", lambda request: (501, {"type":"task.result", "status":"succeeded"}))
        shell.wait_once()
        assert shell.phase.value == "error" and shell.result is None


def test_concurrent_run_is_rejected_before_original_dispatch_reply(monkeypatch):
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE":"silent"}) as h,local_sidecar(h) as (call,_,_):
        entered,release=threading.Event(),threading.Event()
        original=BridgeClient.send
        def delayed(self,request):
            result=original(self,request)
            if request.operation_id=="taskCreate":
                entered.set();assert release.wait(5)
            return result
        monkeypatch.setattr(BridgeClient,"send",delayed)
        with ThreadPoolExecutor(2) as pool:
            first=pool.submit(call,"/_sidecar/ux/run",{"goal":"one original","max_ticks":1})
            try:
                assert entered.wait(5)
                assert call("/_sidecar/ux/run",{"goal":"forbidden concurrent run"})[0]==409
                assert call("/_sidecar/ux/end")[0]==409
            finally:release.set()
            snapshot=first.result(timeout=5)[1]["snapshot"]
        assert snapshot["task_in_flight"]
        assert len(h.state.tasks)==len(h.state.sessions)==len(h.adapter.transport.dispatched)==1


def test_operation_lock_is_released_before_http_transport_but_response_is_frozen(monkeypatch):
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE":"silent"}) as h,local_sidecar(h) as (call,sc,_):
        entered,release=threading.Event(),threading.Event()
        original=sc.RequestHandlerClass._json
        def delayed(self,status,payload):
            if self.path=="/_sidecar/ux/run" and status==200:
                assert isinstance(payload,bytes),"reply must freeze while operation lock is held"
                entered.set();assert release.wait(5)
            return original(self,status,payload)
        monkeypatch.setattr(sc.RequestHandlerClass,"_json",delayed)
        with ThreadPoolExecutor(1) as pool:
            first=pool.submit(call,"/_sidecar/ux/run",{"goal":"boundary original","max_ticks":1})
            try:
                assert entered.wait(5)
                current=call("/_sidecar/ux",method="GET")[1]
                h.adapter.source.simulate_bot_write(current["task_id"],result_for(h))
                assert call("/_sidecar/ux/poll")[1]["phase"]=="done"
                assert call("/_sidecar/ux/end")[1]["phase"]=="pick"
            finally:release.set()
            frozen=first.result(timeout=5)[1]["snapshot"]
        assert frozen["phase"]=="wait" and frozen["task_id"]==current["task_id"]
        assert call("/_sidecar/ux",method="GET")[1]["phase"]=="pick"
        assert len(h.state.tasks)==len(h.state.sessions)==len(h.adapter.transport.dispatched)==1
