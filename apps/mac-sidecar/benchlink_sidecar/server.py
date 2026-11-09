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
import socket
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import CONTRACT_VERSION, __version__
from .client import BridgeClient
from .config import ConfigError, SidecarConfig, describe
from .ui.copy import copy_tables_for_html, error_card
from .ux import DEFAULT_TARGET, ROSTER, BridgeControlShell, TaskInFlight
from .request_guard import request_denial


def task_in_flight_error() -> dict:
    env = error_envelope("validation", "Completion is not confirmed; Retry checks the original task", False)
    env["presentation"] = {"error": error_card("task_in_flight")}
    return env


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
 label[for="goal"]{display:block;margin-top:8px} button{margin:4px 4px 4px 0;padding:6px 10px;cursor:pointer}
 textarea{box-sizing:border-box;width:100%;min-height:80px;background:#0f1115;color:#e8eaed;border:1px solid #3c4043;border-radius:6px;padding:8px}
 .banner{background:#3a2a00;border:1px solid #f9ab00;padding:8px;border-radius:6px;margin:8px 0}
 .pill{display:inline-block;padding:2px 10px;border-radius:999px;font-weight:600;font-size:12px}
 .pill.neutral{background:#2c2e33;color:#b4b6bc}
 .pill.info{background:#16203a;color:#7faeff}
 .pill.success{background:#16271d;color:#5cc98a}
 .pill.danger{background:#2e1816;color:#ff7b70}
 .err-title{font-weight:600;margin:0 0 4px} .err-body{margin:0 0 8px;color:#b4b6bc}
 .result-summary{font-size:15px;margin:0 0 8px} .links a{color:#7fa0ff;display:block;margin:2px 0}
 .meta{color:#8e9198;font-size:12px;margin-top:8px}
 details{margin-top:8px;color:#8e9198;font-family:ui-monospace,monospace;font-size:12px}
</style></head><body>
<h1>Benchlink · Bridge Control <span class="muted">[Beta]</span></h1>
<p class="muted">Send a task to Floyd's Labs staff.</p>
<div id="banner" class="banner" style="display:none"></div>
<div class="card" id="pick">
  <strong>1 · Pick target</strong>
  <div id="roster"></div>
</div>
<div class="card" id="compose">
  <strong>2 · Compose + Send</strong><br/>
  Target: <span id="targetLabel">Floyd</span>
  <label><input type="checkbox" id="hp"/> High priority (also ping Floyd in chat)</label>
  <label for="goal">Staff task</label><p id="goalHelp" class="muted">Describe what you need.</p>
  <textarea id="goal" aria-describedby="goalHelp" placeholder="Describe what you need."></textarea>
  <button id="send" disabled>Send task</button>
</div>
<div class="card" id="wait">
  <strong id="statusHeading" tabindex="-1">3 · Status</strong>
  <div role="status" aria-live="polite" aria-atomic="true"><span id="statusPill" class="pill neutral">Idle</span></div>
  <div id="resultCard" style="display:none;margin-top:12px">
    <p class="result-summary" id="resultSummary" tabindex="-1"></p>
    <div class="links" id="resultLinks"></div>
    <p class="meta" id="resultMeta"></p>
    <details id="resultDetails"><summary>Details</summary>
      <div>Session ID: <code id="resultSession"></code><button id="copySession">Copy session ID</button></div>
      <div>Task ID: <code id="resultTask"></code><button id="copyTask">Copy task ID</button></div>
    </details>
  </div>
  <div role="alert" aria-atomic="true" id="errorCard" style="display:none;margin-top:12px">
    <p class="err-title" id="errTitle" tabindex="-1"></p>
    <p class="err-body" id="errBody"></p>
    <button type="button" id="errPrimary" style="display:none"></button>
    <button type="button" id="errSecondary" style="display:none"></button>
    <details id="errDetails"><summary>Details</summary>
      <div id="errCodeRow">Error code: <code id="errCode"></code></div>
      <div id="errHttpRow">HTTP status: <code id="errHttp"></code></div>
      <div id="errCorrRow">Correlation ID: <code id="errCorr"></code><button id="copyCorr">Copy correlation ID</button></div>
    </details>
  </div>
  <button id="end">End task</button>
</div>
<script>
const roster = __ROSTER__;
const offline = __OFFLINE__;
const COPY = __COPY__;
const $ = id => document.getElementById(id);
const banner = $('banner');
if (offline) { banner.style.display='block'; banner.textContent = "Preview mode. The bridge address isn't set on this Mac."; }
const rost = $('roster');
let target = '__DEFAULT__';
let recovering = false, busy = false, loaded = false, localFailure = false;
let requestUncertain = false, ownedSnapshot = null, timer = null;
let lastSnapshot = {}, lastSent = null, viewKey = '';
function pending() { return requestUncertain || lastSnapshot.task_in_flight===true || ownedSnapshot!==null; }
const HELP_URL = 'https://github.com/CaptainPhantasy/benchlink/blob/main/docs/PAIRING_PROOF_RUNBOOK.md';
function displayName(key) { return (roster.find(x => x.key === key) || {}).display || key; }
function renderRoster() {
  rost.innerHTML = '';
  roster.forEach(e => {
    const b = document.createElement('button'); b.type = 'button';
    b.textContent = e.display + (e.default ? ' (default)' : '');
    b.onclick = () => { if (!busy && !recovering && !pending()) { target=e.key; $('targetLabel').textContent=e.display; } };
    rost.appendChild(b);
  });
}
function updateSendEnabled() {
  const locked = !loaded || recovering || busy || pending();
  $('send').disabled = locked || !$('goal').value.trim();
  $('end').disabled = locked;
  document.querySelectorAll('#roster button').forEach(b=>b.disabled=locked);
}
function fallbackError(j) {
  const t = COPY.errors[j.code] || COPY.errors.internal;
  const name = displayName(target).split('·')[0].trim();
  const id = $('deviceId') ? $('deviceId').textContent : '';
  return {...t, title:t.title.replace('{Name}', name), body:t.body.replace('{Name}', name).replace('{id}', id || 'unknown'), details_code:j.code || 'internal', correlation_id:j.correlation_id};
}
async function copyText(button, value) {
  const label = button.textContent;
  try { await navigator.clipboard.writeText(value || ''); button.textContent='Copied'; }
  catch (e) { button.textContent='Copy unavailable'; }
  setTimeout(() => { button.textContent=label; }, 2000);
}
function safeDiagnostics() {
  const e = (lastSnapshot.presentation || {}).error || {};
  return JSON.stringify({status:lastSnapshot.wait_status, task_id:lastSnapshot.task_id, session_id:lastSnapshot.session_id,
    code:e.details_code, correlation_id:e.correlation_id});
}
async function post(path, body) {
  const r=await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body || {})});
  return {r, j:await r.json()};
}
function schedule() {
  clearTimeout(timer);
  if (loaded && !localFailure && lastSnapshot.phase==='wait') timer=setTimeout(retryPoll,2000);
}
async function action(kind, body={}, recoverySequence=false) {
  if (!loaded || busy) return false;
  if (kind==='run' && (pending() || (recovering && !recoverySequence))) return false;
  if (kind==='retry' && pending()) kind='poll';
  if (kind==='run') requestUncertain=true;
  busy=true; updateSendEnabled();
  try {
    const {r,j}=await post('/_sidecar/ux/'+kind,body);
    if (!r.ok) {
      // A conflict can belong to another window. Only a snapshot can release it.
      if (r.status===409) requestUncertain=true;
      showError(j); return false;
    }
    show(j.snapshot || j);
    if (kind==='run' && lastSnapshot.phase==='wait') $('statusHeading').focus();
    return true;
  } catch (e) { showError({code:'offline'}); return false; }
  finally { busy=false; updateSendEnabled(); schedule(); }
}
async function endTask(clear=true) {
  if (recovering || busy || pending()) return false;
  const ended=await action('end');
  if (ended && clear) { $('goal').value=''; $('hp').checked=false; }
  updateSendEnabled(); if (ended) $('goal').focus(); return ended;
}
async function retryPoll() {
  if (recovering || busy) return;
  return action('poll');
}
async function retryTerminal() {
  if (recovering || busy) return;
  recovering=true; updateSendEnabled();
  try { return await action('retry'); }
  finally { recovering=false; updateSendEnabled(); }
}
async function retryTask() {
  if (recovering || busy) return;
  if (!loaded) return bootstrap();
  if (pending() || localFailure) return retryPoll();
  return retryTerminal();
}
async function resend(toFloyd=false) {
  if (recovering || busy || pending() || !loaded) return;
  // Own the entire End-and-Send sequence before the first asynchronous request.
  recovering=true; updateSendEnabled();
  // A reload or another window can restore a task that this page never sent.
  // Capture its authoritative draft before End clears the server snapshot.
  const saved = lastSnapshot.task_id
    ? {goal:lastSnapshot.goal, target:lastSnapshot.target, high_priority:lastSnapshot.high_priority}
    : lastSent || {goal:$('goal').value, target, high_priority:$('hp').checked};
  try {
    if (!await action('end')) return;
    target=toFloyd ? 'floyd' : saved.target; $('targetLabel').textContent=displayName(target);
    $('goal').value=saved.goal; $('hp').checked=saved.high_priority; renderRoster();
    lastSent={...saved,target};
    await action('run',{...lastSent,max_ticks:1},true);
  } finally { recovering=false; updateSendEnabled(); }
}
const actions = {
  'Retry': () => retryTask(),
  'End task': () => endTask(), 'New task': () => endTask(),
  'Edit task': () => { $('goal').readOnly=false; $('goal').focus(); },
  'Send again': () => resend(), 'Send to Floyd': () => resend(true),
  'Copy device ID': button => copyText(button, $('deviceId') ? $('deviceId').textContent : deviceId),
  'Copy diagnostics': button => copyText(button, safeDiagnostics()),
  'Details': () => { $('errDetails').open=true; $('errDetails').querySelector('summary').focus(); },
  'Connection help': () => window.open(HELP_URL, '_blank', 'noopener')
};
let deviceId = '';
fetch('/_sidecar/status').then(r=>r.json()).then(st=>{ deviceId=(st.pairing || {}).device_id || (st.config || {}).BENCHLINK_DEVICE_ID || ''; }).catch(()=>{});
function wireAction(button, label) {
  button.style.display = label && actions[label] ? 'inline-block' : 'none';
  button.textContent = label || '';
  button.onclick = actions[label] ? () => Promise.resolve(actions[label](button)).catch(()=>showError({code:'offline'})) : null;
}
function showError(j) {
  // Presentation failures must not erase the original task's ownership or IDs.
  localFailure=true;
  renderSnapshot({phase:'error', wait_status:'error', error:{code:j.code},
    presentation:{status:COPY.status.error, error:(j.presentation || {}).error || fallbackError(j)}});
  updateSendEnabled();
}
function show(snap) {
  if (!snap || typeof snap.task_in_flight!=='boolean') throw new Error('Task ownership unavailable');
  lastSnapshot=snap;
  if (snap.task_in_flight) ownedSnapshot={...snap};
  else { ownedSnapshot=null; requestUncertain=false; }
  localFailure=false;
  if (snap.phase==='pick' || snap.phase==='compose') {
    $('goal').value=snap.goal || ''; $('hp').checked=!!snap.high_priority;
  }
  if (snap.target) { target=snap.target; $('targetLabel').textContent=displayName(target); }
  renderSnapshot(snap); updateSendEnabled();
}
function renderSnapshot(snap) {
  const pres=snap.presentation || {}, st=pres.status || COPY.status[snap.wait_status] || COPY.status.idle;
  const key=JSON.stringify([snap.phase,snap.wait_status,snap.task_id,pres.error,pending()]);
  const changed=key!==viewKey; viewKey=key;
  const pill=$('statusPill'); pill.textContent=st.label; pill.className='pill '+(st.tone || 'neutral');
  const rc=$('resultCard'), ec=$('errorCard');
  rc.style.display=pres.result && snap.wait_status==='result' ? 'block' : 'none';
  ec.style.display=pres.error || snap.error ? 'block' : 'none';
  if (pres.result && snap.wait_status==='result') {
    $('resultSummary').textContent=pres.result.summary || '';
    const links=$('resultLinks'); links.innerHTML='';
    (pres.result.artifact_links || []).forEach(l=>{
      if (!(l.url || '').startsWith('https://') && !(l.url || '').startsWith('http://')) return;
      const a=document.createElement('a'); a.href=l.url; a.target='_blank'; a.rel='noopener';
      const icon=document.createElement('span'); icon.setAttribute('aria-hidden','true'); icon.innerHTML='<svg class="lucide lucide-link-2" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 15 6-6"/><path d="m11 7 1.5-1.5a5 5 0 0 1 7 7L18 14"/><path d="m13 17-1.5 1.5a5 5 0 0 1-7-7L6 10"/></svg> '; a.appendChild(icon); a.appendChild(document.createTextNode(l.label || l.url)); a.title=l.url; a.setAttribute('aria-label', l.url);
      links.appendChild(a);
    });
    if (!pres.result.has_links) { const p=document.createElement('p'); p.className='muted'; p.textContent='No links returned'; links.appendChild(p); }
    $('resultMeta').textContent=pres.result.meta || '';
    $('resultSession').textContent=pres.result.session_id || snap.session_id || '';
    $('resultTask').textContent=pres.result.task_id || snap.task_id || '';
    if (changed) $('resultSummary').focus();
  }
  if ((pres.error || snap.error) && changed) {
    let err=pres.error || fallbackError(snap.error || {});
    if (pending()) {
      err={...err, body:'Completion is not confirmed. Retry checks the original task; sending stays locked.',
        primary:'Retry', secondary:'Details'};
    }
    $('errTitle').textContent=err.title; $('errBody').textContent=err.body;
    for (const [id,value] of [['errCode',err.details_code],['errHttp',err.http],['errCorr',err.correlation_id]]) {
      $(id).textContent=value == null ? '' : value; $(id+'Row').hidden=value == null || value==='';
    }
    wireAction($('errPrimary'),err.primary); wireAction($('errSecondary'),err.secondary);
    $('errTitle').focus();
  }
}
async function sendTask() {
  const goal=$('goal').value.trim(); if (!goal || recovering || busy || pending() || !loaded) return;
  lastSent={target,goal,high_priority:$('hp').checked};
  $('send').textContent='Sending';
  try { await action('run',{...lastSent,max_ticks:1}); }
  finally { $('send').textContent='Send task'; updateSendEnabled(); }
}
$('send').onclick=sendTask;
$('end').onclick=()=>endTask().catch(()=>showError({code:'offline'}));
$('goal').addEventListener('input',updateSendEnabled);
$('copySession').onclick=()=>copyText($('copySession'),$('resultSession').textContent);
$('copyTask').onclick=()=>copyText($('copyTask'),$('resultTask').textContent);
$('copyCorr').onclick=()=>copyText($('copyCorr'),$('errCorr').textContent);
async function bootstrap() {
  if (busy) return;
  busy=true; updateSendEnabled();
  try {
    const r=await fetch('/_sidecar/ux');
    if (!r.ok) throw new Error('Task status unavailable');
    show(await r.json()); loaded=true;
  } catch (e) { showError({code:'offline'}); }
  finally { busy=false; }
  updateSendEnabled(); schedule();
}
$('targetLabel').textContent=displayName(target); renderRoster(); updateSendEnabled(); bootstrap();
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
                        .replace("__COPY__", json.dumps(copy_tables_for_html()))
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
                    return 409, task_in_flight_error()
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
                return self._json(409, task_in_flight_error())
            try:
                try:
                    status, payload = self._task_response(path)
                except TaskInFlight:
                    status, payload = 409, task_in_flight_error()
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


class _IPv6ThreadingHTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def serve(cfg: SidecarConfig) -> ThreadingHTTPServer:
    # config accepts only 127.0.0.1, localhost and ::1; ::1 needs an AF_INET6 socket.
    cls = _IPv6ThreadingHTTPServer if ":" in cfg.listen_host else ThreadingHTTPServer
    return cls((cfg.listen_host, cfg.listen_port), make_handler(cfg))


def listen_url(host: str, port: int) -> str:
    """Browser URL for a listen address; IPv6 literals need brackets."""
    return f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}"
