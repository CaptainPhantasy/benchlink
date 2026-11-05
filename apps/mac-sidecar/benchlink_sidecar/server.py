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
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import CONTRACT_VERSION, __version__
from .client import BridgeClient
from .config import ConfigError, SidecarConfig, describe
from .ui.copy import copy_tables_for_html
from .ux import DEFAULT_TARGET, ROSTER, BridgeControlShell


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
<p class="muted">Companion sidecar UI. Pick → Compose/Send → Wait.</p>
<div id="banner" class="banner" style="display:none"></div>
<div class="card" id="pick">
  <strong>1 · Pick target</strong>
  <div id="roster"></div>
</div>
<div class="card" id="compose">
  <strong>2 · Compose + Send</strong><br/>
  Target: <span id="targetLabel">Floyd</span>
  <label><input type="checkbox" id="hp"/> High priority (also ping Floyd in chat)</label>
  <textarea id="goal" placeholder="Describe what you need."></textarea>
  <button id="send">Send task</button>
</div>
<div class="card" id="wait">
  <strong>3 · Status</strong>
  <div><span id="statusPill" class="pill neutral">Idle</span></div>
  <div id="resultCard" style="display:none;margin-top:12px">
    <p class="result-summary" id="resultSummary"></p>
    <div class="links" id="resultLinks"></div>
    <p class="meta" id="resultMeta"></p>
  </div>
  <div id="errorCard" style="display:none;margin-top:12px">
    <p class="err-title" id="errTitle"></p>
    <p class="err-body" id="errBody"></p>
    <button type="button" id="errPrimary" style="display:none"></button>
    <button type="button" id="errSecondary" style="display:none"></button>
    <details id="errDetails"><summary>Details</summary>
      <div>code: <span id="errCode"></span></div>
      <div>HTTP: <span id="errHttp"></span></div>
      <div>correlation: <span id="errCorr"></span></div>
    </details>
  </div>
  <button id="end">End task</button>
</div>
<script>
const roster = __ROSTER__;
const offline = __OFFLINE__;
const COPY = __COPY__;
const banner = document.getElementById('banner');
if (offline) { banner.style.display='block'; banner.textContent = 'Offline / fail-closed: ' + offline + '. Send uses dry-run payloads (no DNS invented).'; }
const rost = document.getElementById('roster');
let target = '__DEFAULT__';
function displayName(key) {
  const e = roster.find(x => x.key === key);
  return e ? e.display : key;
}
roster.forEach(e => {
  const b = document.createElement('button');
  b.textContent = e.display + (e.default ? ' (default)' : '');
  b.onclick = () => { target = e.key; document.getElementById('targetLabel').textContent = e.display; };
  rost.appendChild(b);
});
document.getElementById('targetLabel').textContent = displayName(target);

function show(snap) {
  const pres = snap.presentation || {};
  const st = pres.status || COPY.status[snap.wait_status] || COPY.status.idle;
  const pill = document.getElementById('statusPill');
  pill.textContent = st.label;
  pill.className = 'pill ' + (st.tone || 'neutral');

  const rc = document.getElementById('resultCard');
  const ec = document.getElementById('errorCard');
  rc.style.display = 'none';
  ec.style.display = 'none';

  if (pres.result && snap.wait_status === 'result') {
    rc.style.display = 'block';
    document.getElementById('resultSummary').textContent = pres.result.summary || '';
    const links = document.getElementById('resultLinks');
    links.innerHTML = '';
    (pres.result.artifact_links || []).forEach(l => {
      const a = document.createElement('a');
      a.href = l.url; a.target = '_blank'; a.rel = 'noopener';
      a.textContent = l.label || l.url; a.title = l.url;
      links.appendChild(a);
    });
    if (!pres.result.has_links) {
      const note = document.createElement('p');
      note.className = 'muted';
      note.textContent = pres.result.empty_links_note || 'No links returned';
      links.appendChild(note);
    }
    document.getElementById('resultMeta').textContent = pres.result.meta || '';
  }

  if (pres.error || snap.error) {
    const err = pres.error || {};
    ec.style.display = 'block';
    document.getElementById('errTitle').textContent = err.title || 'Something went wrong on the bridge';
    document.getElementById('errBody').textContent = err.body || '';
    document.getElementById('errCode').textContent = err.details_code || (snap.error && snap.error.code) || '';
    document.getElementById('errHttp').textContent = (err.http != null ? err.http : '');
    document.getElementById('errCorr').textContent = err.correlation_id || '';
    const p = document.getElementById('errPrimary');
    const s = document.getElementById('errSecondary');
    if (err.primary) { p.style.display = 'inline-block'; p.textContent = err.primary; } else { p.style.display = 'none'; }
    if (err.secondary) { s.style.display = 'inline-block'; s.textContent = err.secondary; } else { s.style.display = 'none'; }
  }
}

document.getElementById('send').onclick = async () => {
  const goal = document.getElementById('goal').value.trim();
  if (!goal) { alert('Compose a Staff task first'); return; }
  const r = await fetch('/_sidecar/ux/run', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({target, goal, high_priority: document.getElementById('hp').checked})});
  const j = await r.json();
  if (!j.snapshot) { show({wait_status:'error', error:j, presentation:{status:COPY.status.error, error:{title:'Task couldn\\'t be sent', body:j.message || '', details_code:j.code || 'validation', primary:'Edit task', secondary:'Details'}}}); return; }
  let snap = j.snapshot;
  show(snap);
  while (snap.phase === 'wait') {
    await new Promise(res => setTimeout(res, 2000));
    snap = await (await fetch('/_sidecar/ux/poll', {method:'POST'})).json();
    show(snap);
  }
};
document.getElementById('end').onclick = async () => {
  const snap = await (await fetch('/_sidecar/ux/end', {method:'POST'})).json();
  show(snap);
  document.getElementById('goal').value = '';
  document.getElementById('hp').checked = false;
};
</script></body></html>
"""


def make_handler(cfg: SidecarConfig):
    client = BridgeClient(cfg)
    # One in-memory Bridge Control session per sidecar process (O4 session-only retention)
    live = {"shell": BridgeControlShell(cfg, client=client)}

    class Handler(BaseHTTPRequestHandler):
        server_version = f"benchlink-mac-sidecar/{__version__}"

        def _json(self, status: int, payload: dict):
            body = json.dumps(payload).encode()
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

        def do_GET(self):  # noqa: N802
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
                return self._json(200, BridgeControlShell(cfg).snapshot())
            return self._json(404, error_envelope("validation", f"no route {path}", False))

        def do_POST(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path == "/_sidecar/ux/run":
                try:
                    body = self._read_json()
                    shell = live["shell"]
                    if shell.phase.value in {"done", "error", "wait"}:
                        shell.end_task()
                    out = shell.start_and_wait(
                        goal=str(body.get("goal") or "Bridge Control HTML send"),
                        target_key=str(body.get("target") or DEFAULT_TARGET),
                        high_priority=bool(body.get("high_priority")),
                        max_ticks=int(body.get("max_ticks") or 2))
                    return self._json(200, out)
                except Exception as e:  # noqa: BLE001 — surface to UI
                    return self._json(400, error_envelope("validation", str(e), False))
            if path == "/_sidecar/ux/poll":
                shell = live["shell"]
                if shell.phase.value == "wait":
                    shell.wait_once()
                return self._json(200, shell.snapshot())
            if path == "/_sidecar/ux/end":
                live["shell"].end_task()
                return self._json(200, live["shell"].snapshot())
            return self._json(404, error_envelope("validation", f"no route {path}", False))

        def log_message(self, fmt, *args):
            pass

    return Handler


def serve(cfg: SidecarConfig) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((cfg.listen_host, cfg.listen_port), make_handler(cfg))
