"""Safety is ownership of the original task, never a presentation-code allowlist."""
import copy
import io
import json
import threading
import urllib.error
import urllib.request
from dataclasses import replace

import pytest
from benchlink_testkit import Harness, valid
from benchlink_sidecar import client as client_module
from benchlink_sidecar import server
from benchlink_sidecar.ux import BridgeControlShell, Phase, TaskInFlight

STATUSES = [200, 202, 204, 301, 400, 401, 403, 404, 408, 422, 429, 500, 501, 502, 503, 504]
REQUIRED = ["v", "type", "session_id", "task_id", "status", "summary", "artifact_links", "created_at", "agent_id"]
BAD_VALUES = {"v":1, "type":"other", "session_id":[], "task_id":"", "status":[],
              "summary":None, "artifact_links":None, "created_at":None, "agent_id":False, "error_class":[]}
CASES = (["partial-error", "list", "null", "invalid-json", "extra-field", "wrong-task", "wrong-session", "wrong-both"]
         + ["missing-" + field for field in REQUIRED]
         + ["invalid-" + field for field in BAD_VALUES]
         + ["bad-links-type", "bad-link-object", "bad-link-title", "bad-link-url", "extra-link-field",
            "bad-date", "date-no-zone", "midday-leap", "offset-shifted-leap", "unknown-status", "unknown-error", "valid-accepted", "valid-running"])


def mutate(payload, case):
    body = dict(payload, status="succeeded", summary="Untrusted terminal fixture")
    if case == "partial-error": return {"type":"error"}
    if case == "list": return [body]
    if case == "null": return None
    if case == "invalid-json": return b"not JSON"
    if case.startswith("missing-"): body.pop(case.removeprefix("missing-"), None)
    elif case.startswith("invalid-"): body[case.removeprefix("invalid-")] = BAD_VALUES[case.removeprefix("invalid-")]
    elif case == "extra-field": body["arbitrary"] = True
    elif case in {"wrong-task", "wrong-both"}: body["task_id"] = "task_unrelated"
    if case in {"wrong-session", "wrong-both"}: body["session_id"] = "sess_unrelated"
    if case == "bad-links-type": body["artifact_links"] = {}
    if case == "bad-link-object": body["artifact_links"] = ["not a link"]
    if case == "bad-link-title": body["artifact_links"] = [{"title":"", "url":"https://example.invalid/fixture"}]
    if case == "bad-link-url": body["artifact_links"] = [{"title":"fixture", "url":None}]
    if case == "extra-link-field": body["artifact_links"] = [{"title":"fixture", "url":"fixture", "extra":True}]
    if case == "bad-date": body["created_at"] = "not-a-date"
    if case == "date-no-zone": body["created_at"] = "2026-11-05T16:15:00"
    if case == "midday-leap": body["created_at"] = "2026-11-05T16:15:60Z"
    if case == "offset-shifted-leap": body["created_at"] = "2016-12-31T23:59:60-05:00"
    if case == "unknown-status": body["status"] = "complete"
    if case == "unknown-error": body["error_class"] = "made.up"
    if case.startswith("valid-"): body["status"] = case.removeprefix("valid-")
    return body


def install(h, monkeypatch, uncertain):
    original = h.srv.RequestHandlerClass._send
    mode = {"reply":None, "posts":[], "gets":[]}
    def send(self, status, payload, type_name=None, list_of=None):
        if self.path == "/v1/tasks" and status == 202:
            mode["posts"].append(payload["task_id"])
            if uncertain:
                raw, status = b'{"type":"error"}', 502
            else:
                return original(self, status, payload, type_name, list_of)
        elif self.path.startswith("/v1/tasks/") and mode["reply"] is not None:
            mode["gets"].append(self.path)
            status, case = mode["reply"]
            body = mutate(payload, case)
            raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        else:
            return original(self, status, payload, type_name, list_of)
        self.send_response(status)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    monkeypatch.setattr(h.srv.RequestHandlerClass, "_send", send)
    return mode


def assert_owned(shell, h, identity, uncertain):
    assert (shell.task_id, shell.session_id) == identity
    # The first assertion catches an actual duplicate POST on the rejected implementation.
    with pytest.raises(TaskInFlight): shell.start_and_wait("forbidden replacement", max_ticks=1)
    with pytest.raises(TaskInFlight): shell.pick("ethan")
    with pytest.raises(TaskInFlight): shell.compose("forbidden edit")
    with pytest.raises(TaskInFlight): shell.send()
    with pytest.raises(TaskInFlight): shell.end_task()
    assert shell.has_unfinished_task() and shell.snapshot()["task_in_flight"]
    assert (shell.task_id, shell.session_id) == identity
    if uncertain: assert shell.submission_uncertain
    assert len(h.state.tasks) == len(h.state.sessions) == len(h.adapter.transport.dispatched) == 1


def advance(shell):
    if shell.phase == Phase.WAIT: shell.wait_once()
    else: shell.retry_terminal_error()


@pytest.mark.parametrize("uncertain", [pytest.param(True,id="uncertain"), pytest.param(False,id="accepted")])
@pytest.mark.parametrize("status", [pytest.param(s,id="http"+str(s)) for s in STATUSES])
@pytest.mark.parametrize("case", CASES)
def test_every_recovery_shape_preserves_original_ownership(case, status, uncertain, monkeypatch):
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE":"silent"}) as h:
        mode = install(h, monkeypatch, uncertain)
        shell = BridgeControlShell(h.sidecar_cfg, client=h.client, poll_sleep=0)
        shell.start_and_wait("original owned task", max_ticks=1)
        identity = shell.task_id, shell.session_id
        for reply in [(status,case), (501,"partial-error"), (200,"valid-running"), (403,"partial-error")]:
            mode["reply"] = reply
            advance(shell)
            assert_owned(shell, h, identity, uncertain)
        assert mode["posts"] == [identity[0]]
        assert mode["gets"] and all(path.endswith("/"+identity[0]) for path in mode["gets"])


@pytest.mark.parametrize("phase", list(Phase))
@pytest.mark.parametrize("code", ["offline", "timeout", "internal", "validation", "auth.denied", "task.not_found", None])
@pytest.mark.parametrize("uncertain", [True, False])
def test_inflight_guard_is_independent_of_phase_and_error_code(phase, code, uncertain):
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE":"silent"}) as h:
        shell = BridgeControlShell(h.sidecar_cfg, client=h.client, poll_sleep=0)
        shell.start_and_wait("original task", max_ticks=1)
        identity = shell.task_id, shell.session_id
        shell.phase = phase
        shell.error = {"code":code}
        shell.submission_uncertain, shell.task_accepted = uncertain, not uncertain
        # A raw status field is not terminal proof.
        shell.result = {"status":"failed"}
        assert_owned(shell, h, identity, uncertain)


@pytest.mark.parametrize("terminal", ["succeeded", "failed"])
@pytest.mark.parametrize("omit_optional_error", [False, True])
def test_only_matching_valid_terminal_confirmation_releases_uncertainty(terminal, omit_optional_error, monkeypatch):
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE":"silent"}) as h:
        mode = install(h, monkeypatch, True)
        shell = BridgeControlShell(h.sidecar_cfg, client=h.client, poll_sleep=0)
        shell.start_and_wait("original task", max_ticks=1)
        identity = shell.task_id, shell.session_id
        mode["reply"] = (200,"valid-running")
        advance(shell)
        assert_owned(shell, h, identity, True)
        payload = dict(h.adapter.transport.dispatched[0]["result_template"], status=terminal, summary="Confirmed fixture",
                       artifact_links=[], error_class=None if terminal=="succeeded" else "internal",
                       created_at="2026-11-05T16:15:00-05:00")
        if omit_optional_error: payload.pop("error_class", None)
        assert not valid("task.result", payload)
        h.adapter.source.simulate_bot_write(identity[0], payload)
        mode["reply"] = None
        advance(shell)
        assert not shell.submission_uncertain and not shell.has_unfinished_task()
        assert (shell.task_id,shell.session_id) == identity
        assert len(h.adapter.transport.dispatched) == 1
        shell.end_task()
        shell.start_and_wait("fresh explicit Send after terminal proof", max_ticks=1)
        assert len(h.state.tasks) == len(h.state.sessions) == len(h.adapter.transport.dispatched) == 2


@pytest.mark.parametrize("code", ["auth.expired", "auth.unpaired", "auth.denied", "validation", "offline",
                                    "timeout", "routing", "internal", "session.not_found", "task.not_found"])
def test_every_frozen_get_error_class_preserves_unknown_original(code, monkeypatch):
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE":"silent"}) as h:
        mode = install(h, monkeypatch, True)
        shell = BridgeControlShell(h.sidecar_cfg, client=h.client, poll_sleep=0)
        shell.start_and_wait("original task", max_ticks=1)
        identity = shell.task_id,shell.session_id
        real_open = client_module.urllib.request.urlopen
        def error(request, *args, **kwargs):
            if request.get_method()=="GET" and "/tasks/" in request.full_url:
                body={"v":"1","type":"error","code":code,"message":"Local rejection fixture",
                      "retryable":False,"correlation_id":"req_invariant"}
                raise urllib.error.HTTPError(request.full_url, 401 if code.startswith("auth.") else 400,
                                             "Fixture", {}, io.BytesIO(json.dumps(body).encode()))
            return real_open(request,*args,**kwargs)
        monkeypatch.setattr(client_module.urllib.request,"urlopen",error)
        advance(shell)
        assert_owned(shell,h,identity,True)


@pytest.mark.parametrize("wrapped", [True, False])
def test_repeated_recovery_deadlines_never_release_pending_original(wrapped, monkeypatch):
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE":"silent"}) as h:
        install(h,monkeypatch,True)
        shell=BridgeControlShell(h.sidecar_cfg,client=h.client,poll_sleep=0)
        shell.start_and_wait("original task",max_ticks=1)
        identity=shell.task_id,shell.session_id
        def deadline(*args,**kwargs):
            error=TimeoutError("Local recovery deadline")
            raise urllib.error.URLError(error) if wrapped else error
        monkeypatch.setattr(client_module.urllib.request,"urlopen",deadline)
        for _ in range(3):
            advance(shell)
            assert shell.error["code"]=="timeout"
            assert_owned(shell,h,identity,True)


@pytest.mark.parametrize("case", ["partial-error","wrong-task","wrong-session","wrong-both",
                                  "missing-summary","missing-artifact_links","invalid-status","bad-link-object"])
def test_direct_loopback_retry_run_and_end_share_the_same_invariant(case, monkeypatch):
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE":"silent"}) as h:
        mode=install(h,monkeypatch,True)
        sc=server.serve(replace(h.sidecar_cfg,listen_port=0))
        threading.Thread(target=sc.serve_forever,kwargs={"poll_interval":.01},daemon=True).start()
        base="http://127.0.0.1:"+str(sc.server_address[1])
        def post(path,body=None):
            request=urllib.request.Request(base+path,data=json.dumps(body or {}).encode(),method="POST",
                                           headers={"Content-Type":"application/json"})
            try:
                with urllib.request.urlopen(request,timeout=5) as response:
                    return response.status,json.loads(response.read())
            except urllib.error.HTTPError as error:
                return error.code,json.loads(error.read())
        try:
            first=post("/_sidecar/ux/run",{"goal":"original"})[1]["snapshot"]
            identity=first["task_id"],first["session_id"]
            for status in [200,400,401,403,501,502,504]:
                mode["reply"]=(status,case)
                snap=post("/_sidecar/ux/retry")[1]
                assert (snap["task_id"],snap["session_id"])==identity
                assert snap["task_in_flight"] and snap["submission_uncertain"]
                assert post("/_sidecar/ux/run",{"goal":"forbidden replacement"})[0]==409
                assert post("/_sidecar/ux/end")[0]==409
                assert len(h.state.tasks)==len(h.state.sessions)==len(h.adapter.transport.dispatched)==1
        finally:
            sc.shutdown();sc.server_close()


def test_synchronous_session_stage_already_owns_single_flight_guard(monkeypatch):
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE":"silent"}) as h:
        shell=BridgeControlShell(h.sidecar_cfg,client=h.client,poll_sleep=0)
        entered,release=threading.Event(),threading.Event()
        original=h.client.send
        errors=[]
        def blocked(request):
            response=original(request)
            if request.operation_id=="sessionCreate" and not entered.is_set():
                entered.set()
                assert release.wait(5)
            return response
        monkeypatch.setattr(h.client,"send",blocked)
        def worker():
            try: shell.start_and_wait("first synchronous send",max_ticks=1)
            except BaseException as error: errors.append(error)
        thread=threading.Thread(target=worker)
        thread.start()
        try:
            assert entered.wait(5)
            for operation in [lambda:shell.pick("ethan"),lambda:shell.compose("replace"),
                              shell.send,lambda:shell.start_and_wait("replace"),shell.end_task]:
                with pytest.raises(TaskInFlight): operation()
        finally:
            release.set();thread.join(5)
        assert not thread.is_alive() and not errors
        assert len(h.state.tasks)==len(h.state.sessions)==len(h.adapter.transport.dispatched)==1


@pytest.mark.parametrize("status", [pytest.param(s,id="http"+str(s)) for s in STATUSES])
@pytest.mark.parametrize("case", CASES)
def test_create_response_matrix_establishes_ownership_before_any_retry(case,status,monkeypatch):
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE":"silent"}) as h:
        mode=install(h,monkeypatch,False)
        original=h.srv.RequestHandlerClass._send
        def create(self,st,payload,type_name=None,list_of=None):
            if self.path=="/v1/tasks" and st==202:
                body=mutate(payload,case)
                raw=body if isinstance(body,bytes) else json.dumps(body).encode()
                self.send_response(status);self.send_header("Content-Length",str(len(raw)))
                self.end_headers();self.wfile.write(raw)
                return
            return original(self,st,payload,type_name,list_of)
        monkeypatch.setattr(h.srv.RequestHandlerClass,"_send",create)
        shell=BridgeControlShell(h.sidecar_cfg,client=h.client,poll_sleep=0)
        shell.start_and_wait("original create",max_ticks=1)
        identity=shell.task_id,shell.session_id
        uncertain=not(status in {200,202} and case in {"valid-accepted","valid-running"})
        assert_owned(shell,h,identity,uncertain)
        mode["reply"]=(401,"partial-error")
        advance(shell)
        assert_owned(shell,h,identity,uncertain)


@pytest.mark.parametrize("code", ["auth.expired","auth.unpaired","auth.denied","validation","offline",
                                    "timeout","routing","internal","session.not_found","task.not_found"])
@pytest.mark.parametrize("status", [400,401,403,408,422,500,501,502,504])
def test_even_complete_create_error_is_not_terminal_confirmation(code,status,monkeypatch):
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE":"silent"}) as h:
        original=h.srv.RequestHandlerClass._send
        posts=[]
        def send(self,st,payload,type_name=None,list_of=None):
            if self.path=="/v1/tasks" and st==202:
                posts.append(payload["task_id"])
                body={"v":"1","type":"error","code":code,"message":"Complete error after accepted fixture",
                      "retryable":False,"correlation_id":"req_complete_fixture"}
                assert not valid("error",body)
                raw=json.dumps(body).encode()
                self.send_response(status);self.send_header("Content-Length",str(len(raw)))
                self.end_headers();self.wfile.write(raw)
                return
            return original(self,st,payload,type_name,list_of)
        monkeypatch.setattr(h.srv.RequestHandlerClass,"_send",send)
        shell=BridgeControlShell(h.sidecar_cfg,client=h.client,poll_sleep=0)
        shell.start_and_wait("original accepted task",max_ticks=1)
        identity=shell.task_id,shell.session_id
        assert_owned(shell,h,identity,True)
        shell.retry_terminal_error()
        assert_owned(shell,h,identity,True)
        assert posts==[identity[0]], "ambiguous POST must recover with GET, not replay taskCreate"


@pytest.mark.parametrize("stage",["create","recovery","poll"])
@pytest.mark.parametrize("kind",["incomplete-read","eof","runtime","invalid-json"])
def test_any_reply_exception_preserves_attempted_task_ownership(stage,kind,monkeypatch):
    from http.client import IncompleteRead
    factories={"incomplete-read":lambda:IncompleteRead(b"fixture partial body",100),
               "eof":lambda:EOFError("Local EOF fixture"),"runtime":lambda:RuntimeError("Local reply fixture"),
               "invalid-json":lambda:ValueError("Local JSON fixture")}
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE":"silent"}) as h:
        install(h,monkeypatch,stage=="recovery")
        shell=BridgeControlShell(h.sidecar_cfg,client=h.client,poll_sleep=0)
        original=h.client.send
        if stage!="create": shell.start_and_wait("original",max_ticks=1)
        def fail_after_effect(request):
            response=original(request)
            if request.operation_id==("taskCreate" if stage=="create" else "taskGet"):
                raise factories[kind]()
            return response
        monkeypatch.setattr(h.client,"send",fail_after_effect)
        try:
            if stage=="create": shell.start_and_wait("original",max_ticks=1)
            else: advance(shell)
        except Exception:
            pass  # still require the invariant even if a reply implementation raises
        identity=shell.task_id,shell.session_id
        blocked=False
        try: shell.start_and_wait("forbidden replacement",max_ticks=1)
        except TaskInFlight: blocked=True
        except Exception: pass
        assert blocked and len(h.state.tasks)==len(h.state.sessions)==len(h.adapter.transport.dispatched)==1
        assert shell.phase==Phase.ERROR and shell.snapshot()["task_in_flight"]
        assert (shell.task_id,shell.session_id)==identity
