"""Minimal loopback Bridge Control stub on 127.0.0.1:8787.

Routes:
  GET /                  -> Bridge Control HTML shell (Pick→Compose→Wait chrome)
  GET /v1/health         -> proxies upstream {base}/v1/health when configured;
                            otherwise 503 with an `error` envelope (code=offline).
                            The sidecar never fabricates a health.pong (that
                            schema asserts runtime=inference).
  GET /_sidecar/status   -> NON-CONTRACT local diagnostics (redacted config)
  GET /_sidecar/ux       -> NON-CONTRACT JSON snapshot helper for the HTML shell
  POST /_sidecar/ux/run|poll|end -> NON-CONTRACT live Bridge Control session (pairs + bearer via client)
Everything else -> 404 error envelope.
"""
from __future__ import annotations

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import CONTRACT_VERSION, __version__
from .client import BridgeClient
from .config import ConfigError, SidecarConfig, describe
from .ux import DEFAULT_TARGET, ROSTER, BridgeControlShell, TaskInFlight
from .request_guard import request_denial


def error_envelope(code: str, message: str, retryable: bool) -> dict:
    return {"v": "1", "type": "error", "code": code, "message": message,
            "retryable": retryable, "correlation_id": "sc_" + uuid.uuid4().hex[:16]}


HTML_SHELL = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<title>Benchlink · Bridge Control</title>
<style>
 body{font:14px/1.4 system-ui,sans-serif;margin:24px;max-width:720px;background:#0f1115;color:#e8eaed}
 h1{font-size:18px;margin:0 0 8px} .muted{color:#9aa0a6}
 .card{border:1px solid #3c4043;border-radius:8px;padding:12px;margin:12px 0;background:#1a1d23}
 button{margin:4px 4px 4px 0;padding:6px 10px;cursor:pointer}
 textarea{width:100%;min-height:80px;background:#0f1115;color:#e8eaed;border:1px solid #3c4043;border-radius:6px;padding:8px}
 .banner{background:#3a2a00;border:1px solid #f9ab00;padding:8px;border-radius:6px;margin:8px 0}
 .ok{color:#81c995} .warn{color:#fdd663}
</style></head><body>
<h1>Benchlink · Bridge Control</h1>
<p class="muted">Companion sidecar UI (O6). Amir v0.2 · Pick → Compose/Send → Wait. Soft O1–O6 locked.</p>
<div id="banner" class="banner" style="display:none"></div>
<div class="card" id="pick">
  <strong>1 · Pick target</strong> (S2 static roster; Floyd default)
  <div id="roster"></div>
</div>
<div class="card" id="compose">
  <strong>2 · Compose + Send</strong> <span class="muted">(= session.create → task.create)</span><br/>
  Target: <span id="targetLabel">floyd</span>
  <label><input type="checkbox" id="hp"/> High priority (Floyd chat) — O3 default OFF</label>
  <textarea id="goal" placeholder="Type Staff task here in Bridge Control…"></textarea>
  <button id="send" disabled>Send task</button>
</div>
<div class="card" id="wait">
  <strong>3 · Wait · Bridge status</strong>
  <div id="status" class="muted">Idle</div>
  <pre id="result" style="white-space:pre-wrap"></pre>
  <button id="retry" hidden>Retry</button>
  <button id="end">End task → re-pick</button>
</div>
<script>
const roster = __ROSTER__;
const offline = __OFFLINE__;
const banner = document.getElementById('banner');
if (offline) { banner.style.display='block'; banner.textContent='Offline / fail-closed: '+offline+' — Send uses dry-run payloads.'; }
let target='__DEFAULT__', lastSnapshot={}, ownedSnapshot=null;
let busy=false, loaded=false, requestUncertain=false, localFailure=false, timer=null;
function pending() { return requestUncertain || lastSnapshot.task_in_flight===true || ownedSnapshot!==null; }
function controls() {
  document.getElementById('send').disabled=!loaded || busy || pending();
  document.getElementById('end').disabled=!loaded || busy || pending();
  document.getElementById('retry').hidden=!(localFailure || lastSnapshot.phase==='error');
  document.getElementById('retry').disabled=busy;
  document.querySelectorAll('#roster button').forEach(b=>b.disabled=busy || pending());
}
function show(snap) {
  if (!snap || typeof snap.task_in_flight!=='boolean') throw new Error('Task ownership unavailable');
  lastSnapshot=snap;
  if (snap.task_in_flight) ownedSnapshot={...snap};
  else { ownedSnapshot=null; requestUncertain=false; }
  localFailure=false;
  document.getElementById('status').textContent='status='+snap.wait_status+' phase='+snap.phase+(snap.error ? ' error='+snap.error.code : '');
  document.getElementById('result').textContent=JSON.stringify(snap.result || snap.error || {},null,2);
  if (snap.phase==='pick' || snap.phase==='compose') {
    document.getElementById('goal').value=snap.goal || '';
    document.getElementById('hp').checked=!!snap.high_priority;
  }
  if (snap.target) { target=snap.target; document.getElementById('targetLabel').textContent=target; }
  controls();
}
async function post(path,body={}) {
  const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const j=await r.json();
  if (!r.ok) throw new Error(typeof j.code==='string' ? j.code : 'Request failed');
  return j.snapshot || j;
}
function schedule() {
  clearTimeout(timer);
  if (lastSnapshot.phase==='wait') timer=setTimeout(()=>action('poll'),2000);
}
async function action(kind) {
  if (busy || !loaded) return;
  if (kind==='run' && pending()) return;
  const body=kind==='run' ? {target,goal:document.getElementById('goal').value.trim(),high_priority:document.getElementById('hp').checked,max_ticks:1} : {};
  if (kind==='run' && !body.goal) return;
  if (kind==='run') requestUncertain=true;
  if (kind==='retry' && pending()) kind='poll'; // GET the stored original, even after a lost local run reply
  busy=true;controls();
  try { show(await post('/_sidecar/ux/'+kind,body)); }
  catch (e) {
    localFailure=true; // never clear task ownership on a presentation/fetch/HTTP failure
    document.getElementById('status').textContent='Error: '+e.message+(pending() ? ' — Retry checks the original task; Send stays locked.' : '');
  }
  finally { busy=false;controls();schedule(); }
}
roster.forEach(e=>{
  const b=document.createElement('button');b.textContent=e.display+(e.default ? ' (default)' : '');
  b.onclick=()=>{ if (!pending() && !busy) { target=e.key;document.getElementById('targetLabel').textContent=e.key; } };
  document.getElementById('roster').appendChild(b);
});
document.getElementById('send').onclick=()=>action('run');
document.getElementById('retry').onclick=()=>action('retry');
document.getElementById('end').onclick=()=>action('end');
async function bootstrap() {
  try {
    const r=await fetch('/_sidecar/ux');
    if (!r.ok) throw new Error('Task status unavailable');
    show(await r.json());loaded=true;
  } catch (e) { document.getElementById('status').textContent='Task status unavailable; reload to retry.'; }
  controls();schedule();
}
controls();bootstrap();
</script></body></html>
"""


def make_handler(cfg: SidecarConfig):
    client = BridgeClient(cfg)
    # One in-memory Bridge Control session per sidecar process (O4 session-only retention)
    live = {"shell": BridgeControlShell(cfg, client=client)}
    run_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = f"benchlink-mac-sidecar/{__version__}"

        def _json(self, status: int, payload: dict | bytes):
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _html(self, status: int, text: str):
            body = text.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0:
                return {}
            return json.loads(self.rfile.read(n) or b"{}")

        def _allow_request(self, *, mutation=False):
            denial = request_denial(self.headers, self.server.server_address[1], mutation=mutation)
            if not self.path.startswith("/") or self.path.startswith("//"):
                denial = (403, "Only local origin-form requests are allowed.")
            if denial:
                status, reason = denial
                self.close_connection = True
                self._json(status, error_envelope("auth.denied" if status == 403 else "validation", reason, False))
                return False
            return True

        def do_GET(self):  # noqa: N802
            if not self._allow_request():
                return
            path = self.path.split("?", 1)[0]
            if path in {"/", "/index.html"}:
                shell = BridgeControlShell(cfg)
                html = (HTML_SHELL
                        .replace("__ROSTER__", json.dumps(shell.roster()))
                        .replace("__OFFLINE__", json.dumps(shell.offline_banner))
                        .replace("__DEFAULT__", DEFAULT_TARGET))
                return self._html(200, html)
            if path == "/v1/health":
                try:
                    status, payload = client.health()
                    return self._json(status, payload)
                except ConfigError as e:
                    return self._json(503, error_envelope("offline", str(e), False))
                except OSError as e:
                    return self._json(502, error_envelope("offline", f"upstream unreachable: {e}", True))
            if path == "/_sidecar/status":
                return self._json(200, {"sidecar_version": __version__,
                                        "contract": CONTRACT_VERSION,
                                        "upstream_configured": cfg.upstream_configured,
                                        "config": describe(cfg),
                                        "roster_keys": [e.key for e in ROSTER]})
            if path == "/_sidecar/ux":
                with run_lock:
                    body = json.dumps(live["shell"].snapshot()).encode()
                return self._json(200, body)
            return self._json(404, error_envelope("validation", f"no route {path}", False))

        def _task_response(self, path):
            shell = live["shell"]
            if path == "/_sidecar/ux/run":
                if shell.has_unfinished_task():
                    return 409, error_envelope("validation", "Completion is not confirmed; Retry checks the original task", False)
                body = self._read_json()
                goal = str(body.get("goal") or "").strip()
                if not goal:
                    return 422, error_envelope("validation", "Compose a non-empty task", False)
                if shell.phase.value in {"done", "error"}:
                    shell.end_task()
                out = shell.start_and_wait(goal=goal,
                    target_key=str(body.get("target") or DEFAULT_TARGET),
                    high_priority=bool(body.get("high_priority")),
                    max_ticks=int(body.get("max_ticks") or 2))
                return 200, {"snapshot": out["snapshot"], "send": {"mode": out.get("send", {}).get("mode")}}
            if path == "/_sidecar/ux/end":
                shell.end_task()  # same central guard: End is NOT cancellation
            elif path == "/_sidecar/ux/retry":
                shell.retry_terminal_error()
            elif path == "/_sidecar/ux/poll":
                if shell.can_resume_interrupted_wait():
                    shell.resume_interrupted_wait()
                elif shell.phase.value == "wait":
                    shell.wait_once()
            return 200, shell.snapshot()

        def do_POST(self):  # noqa: N802
            if not self._allow_request(mutation=True):
                return
            path = self.path.split("?", 1)[0]
            if path not in {"/_sidecar/ux/run", "/_sidecar/ux/poll", "/_sidecar/ux/retry", "/_sidecar/ux/end"}:
                return self._json(404, error_envelope("validation", f"no route {path}", False))
            if not run_lock.acquire(blocking=False):
                return self._json(409, error_envelope("validation", "Task operation in progress", False))
            try:
                try:
                    status, payload = self._task_response(path)
                except TaskInFlight:
                    status, payload = 409, error_envelope("validation", "Completion is not confirmed; Retry checks the original task", False)
                except (ValueError, RuntimeError):
                    status, payload = 422, error_envelope("validation", "Task operation rejected", False)
                except Exception:
                    status, payload = 500, error_envelope("internal", "Bridge operation failed", False)
                body = json.dumps(payload).encode()  # freeze under lock, not during response transport
            finally:
                run_lock.release()
            return self._json(status, body)

        def log_message(self, fmt, *args):
            pass

    return Handler


def serve(cfg: SidecarConfig) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((cfg.listen_host, cfg.listen_port), make_handler(cfg))
