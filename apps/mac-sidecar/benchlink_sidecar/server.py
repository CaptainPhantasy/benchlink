"""Minimal loopback Bridge Control stub on 127.0.0.1:8787.

Routes:
  GET /                  -> Bridge Control HTML shell (Pick→Compose→Wait chrome)
  GET /v1/health         -> proxies upstream {base}/v1/health when configured;
                            otherwise 503 with an `error` envelope (code=offline).
                            The sidecar never fabricates a health.pong (that
                            schema asserts runtime=inference).
  GET /_sidecar/status   -> NON-CONTRACT local diagnostics (redacted config + connection/pairing)
  GET /_sidecar/ux       -> NON-CONTRACT JSON snapshot helper for the HTML shell
  POST /_sidecar/ux/run|poll|end -> NON-CONTRACT live Bridge Control session (pairs + bearer via client)
  POST /_sidecar/pair    -> NON-CONTRACT loopback-only Pair/Re-pair for enrolled devices
Everything else -> 404 error envelope.
"""
from __future__ import annotations

import json
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import CONTRACT_VERSION, __version__
from .client import BridgeClient
from .config import ConfigError, SidecarConfig, describe
from .ui.copy import (
    CONNECTION,
    TASK_IN_FLIGHT_BODY,
    copy_tables_for_html,
    error_card,
)
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
 .opt{display:block;text-align:left;background:#1a1d23;color:#e8eaed;border:1px solid #3c4043;border-radius:6px;padding:8px 10px;margin:4px 0}
 .opt.selected{background:#1a2240;border-color:#7fa0ff}
 .opt:disabled{opacity:0.5;cursor:not-allowed}
 button:disabled{opacity:0.5;cursor:not-allowed}
</style></head><body>
<div style="display:flex;align-items:center;justify-content:space-between;gap:12px">
  <h1 style="margin:0">Benchlink · Bridge Control <span class="muted">[Beta]</span></h1>
  <button type="button" id="connPill" class="pill warning" title="Open connection">Preview mode (offline)</button>
</div>
<p class="muted">Companion sidecar UI. Pick → Compose/Send → Wait.</p>
<div id="banner" class="banner" style="display:none"></div>
<div id="previewBanner" class="banner" style="display:none">Preview mode. The bridge address isn't set on this Mac, so tasks can't be sent.</div>
<div class="card" id="setup">
  <strong>Setup</strong>
  <div id="bridgeRow">Bridge: <span id="bridgeLabel">…</span></div>
  <div id="pairingRow">Pairing: <span id="pairingLabel">…</span></div>
  <div id="deviceRow">Device: <code id="deviceId"></code>
    <button type="button" id="copyDevice">Copy</button></div>
  <button type="button" id="pairBtn">Pair this Mac</button>
  <p class="muted">Device-bound sign-in. Tokens are never shown.</p>
</div>
<div class="card" id="s4" style="display:none">
  <strong>Connection</strong>
  <p id="s4Reason" class="muted"></p>
  <button type="button" id="s4Retry">Retry</button>
  <button type="button" id="s4Repair">Re-pair</button>
  <details><summary>Details</summary>
    <div>transport: <span id="s4Transport"></span></div>
    <div>host: <span id="s4Host"></span></div>
    <div>last error: <span id="s4Err"></span></div>
  </details>
  <button type="button" id="s4Close">Close</button>
</div>
<div class="card" id="pick">
  <strong>1 · Pick target</strong>
  <div id="roster" role="radiogroup" aria-label="Staff target"></div>
  <p id="rosterLock" class="muted" style="display:none">End the current task to change target.</p>
</div>
<div class="card" id="compose">
  <strong>2 · Compose + Send</strong><br/>
  Target: <span id="targetLabel">Floyd · General Manager</span>
  <label><input type="checkbox" id="hp"/> High priority (also ping Floyd in chat)</label>
  <textarea id="goal" placeholder="Describe what you need."></textarea>
  <p id="sendHint" class="muted"></p>
  <button id="send" disabled>Send task</button>
  <p id="inflightNotice" class="banner" style="display:none"></p>
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
const previewMode = __PREVIEW__;
const banner = document.getElementById('banner');
const previewBanner = document.getElementById('previewBanner');
if (previewMode) {
  previewBanner.style.display = 'block';
} else if (offline) {
  banner.style.display='block';
  banner.textContent = 'Disconnected. Check Tailscale, then retry.';
}
const rost = document.getElementById('roster');
let target = '__DEFAULT__';
let inflight = false;
let pairingState = 'not_paired';

async function refreshStatus() {
  try {
    const st = await (await fetch('/_sidecar/status')).json();
    const conn = st.connection || {};
    const pair = st.pairing || {};
    document.getElementById('bridgeLabel').textContent = conn.label || '…';
    let pl = pair.label || 'Not paired';
    if (pair.state === 'paired' && pair.expires_at) {
      try {
        const d = new Date(pair.expires_at);
        pl = 'Paired · expires ' + d.toLocaleTimeString([], {hour:'numeric', minute:'2-digit'});
      } catch (e) { pl = 'Paired'; }
    }
    document.getElementById('pairingLabel').textContent = pl;
    document.getElementById('deviceId').textContent = pair.device_id || '';
    pairingState = pair.state || 'not_paired';
    const btn = document.getElementById('pairBtn');
    if (pairingState === 'not_allowed') {
      btn.style.display = 'none';
    } else if (pairingState === 'paired') {
      btn.style.display = 'none';
    } else if (pairingState === 'expired' || pairingState === 'failed') {
      btn.style.display = 'inline-block'; btn.textContent = 'Re-pair'; btn.disabled = false;
    } else {
      btn.style.display = 'inline-block'; btn.textContent = 'Pair this Mac'; btn.disabled = false;
    }
    const pill = document.getElementById('connPill');
    pill.textContent = conn.label || '…';
    pill.className = 'pill ' + (conn.tone || 'neutral');
    document.getElementById('s4Reason').textContent = conn.label || '';
    document.getElementById('s4Host').textContent = (st.config && (st.config.BENCHLINK_BRIDGE_BASE_URL || st.config.base_url)) || '(unset)';
    document.getElementById('s4Transport').textContent = st.upstream_configured ? 'configured' : 'preview';
    document.getElementById('s4Err').textContent = (pair.error || '');
    document.getElementById('s4Repair').style.display = (pairingState === 'not_allowed') ? 'none' : 'inline-block';
  } catch (e) { /* ignore */ }
}
document.getElementById('connPill').onclick = () => {
  document.getElementById('s4').style.display = 'block';
};
document.getElementById('s4Close').onclick = () => {
  document.getElementById('s4').style.display = 'none';
};
document.getElementById('s4Retry').onclick = () => refreshStatus();
document.getElementById('s4Repair').onclick = () => document.getElementById('pairBtn').click();

document.getElementById('copyDevice').onclick = async () => {
  const id = document.getElementById('deviceId').textContent;
  try { await navigator.clipboard.writeText(id); } catch (e) {}
};
document.getElementById('pairBtn').onclick = async () => {
  const btn = document.getElementById('pairBtn');
  btn.disabled = true; btn.textContent = 'Pairing';
  const r = await fetch('/_sidecar/pair', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
  const j = await r.json();
  await refreshStatus();
  if (!r.ok) {
    banner.style.display = 'block';
    banner.textContent = (j.presentation && j.presentation.error && j.presentation.error.title)
      || j.message || 'Pairing failed';
  }
};
refreshStatus();
setInterval(refreshStatus, 30000);
function displayName(key) {
  const e = roster.find(x => x.key === key);
  return e ? e.display : key;
}
function renderRoster() {
  rost.innerHTML = '';
  roster.forEach(e => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'opt' + (e.key === target ? ' selected' : '');
    b.setAttribute('role', 'radio');
    b.setAttribute('aria-checked', e.key === target ? 'true' : 'false');
    b.disabled = inflight;
    let label = e.display;
    if (e.default) label += ' · Default';
    if (e.group === 'canary') label += ' · Canary';
    b.textContent = label;
    b.onclick = () => {
      if (inflight) return;
      target = e.key;
      document.getElementById('targetLabel').textContent = e.display;
      renderRoster();
    };
    rost.appendChild(b);
  });
  document.getElementById('rosterLock').style.display = inflight ? 'block' : 'none';
}
function updateSendEnabled() {
  const goal = document.getElementById('goal').value.trim();
  const btn = document.getElementById('send');
  const hint = document.getElementById('sendHint');
  if (previewMode) {
    btn.disabled = true;
    hint.textContent = "Preview mode. The bridge address isn't set on this Mac, so tasks can't be sent.";
  } else if (inflight) {
    btn.disabled = true;
    hint.textContent = 'A task is in flight. End task first, then pick a target and send again.';
  } else if (pairingState === 'not_paired' || pairingState === 'expired'
             || pairingState === 'failed' || pairingState === 'not_allowed') {
    btn.disabled = true;
    hint.textContent = 'Pair this Mac before sending.';
  } else if (!goal) {
    btn.disabled = true;
    hint.textContent = '';
  } else {
    btn.disabled = false;
    hint.textContent = '';
  }
}
document.getElementById('goal').addEventListener('input', updateSendEnabled);
document.getElementById('targetLabel').textContent = displayName(target);
renderRoster();
updateSendEnabled();

function show(snap) {
  const pres = snap.presentation || {};
  const st = pres.status || COPY.status[snap.wait_status] || COPY.status.idle;
  const pill = document.getElementById('statusPill');
  pill.textContent = st.label;
  pill.className = 'pill ' + (st.tone || 'neutral');
  inflight = snap.phase === 'wait';
  renderRoster();
  updateSendEnabled();

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
    const sbtn = document.getElementById('errSecondary');
    if (err.primary) { p.style.display = 'inline-block'; p.textContent = err.primary; } else { p.style.display = 'none'; }
    if (err.secondary) { sbtn.style.display = 'inline-block'; sbtn.textContent = err.secondary; } else { sbtn.style.display = 'none'; }
  }
}

document.getElementById('send').onclick = async () => {
  const goal = document.getElementById('goal').value.trim();
  if (!goal || inflight || previewMode) return;
  const notice = document.getElementById('inflightNotice');
  notice.style.display = 'none';
  const r = await fetch('/_sidecar/ux/run', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({target, goal, high_priority: document.getElementById('hp').checked})});
  const j = await r.json();
  if (r.status === 409) {
    const msg = (j.presentation && j.presentation.error && j.presentation.error.body)
      || j.message
      || 'A task is in flight. End task first, then pick a target and send again.';
    notice.style.display = 'block';
    notice.textContent = 'Task in progress. ' + msg;
    return;
  }
  if (!j.snapshot) {
    const err = (j.presentation && j.presentation.error) || {
      title: r.status === 422 ? 'Can\'t route to target' : 'Task couldn\'t be sent',
      body: j.message || '', details_code: j.code || 'validation',
      primary: 'Edit task', secondary: 'Details'
    };
    show({wait_status:'error', phase:'error', error:j, presentation:{status:COPY.status.error, error:err}});
    return;
  }
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
  document.getElementById('inflightNotice').style.display = 'none';
  show(snap);
  document.getElementById('goal').value = '';
  document.getElementById('hp').checked = false;
  updateSendEnabled();
};
</script></body></html>
"""


def _is_loopback(handler: BaseHTTPRequestHandler) -> bool:
    host = handler.client_address[0] if handler.client_address else ""
    return host in {"127.0.0.1", "::1", "localhost"}


def connection_view(cfg: SidecarConfig, client: BridgeClient) -> dict:
    """Header / S1 connection pill. Preview when upstream unset/placeholder."""
    if not cfg.upstream_configured:
        key = "not_configured"
    else:
        try:
            st, body = client.health()
            if st == 200 and isinstance(body, dict) and body.get("type") == "health.pong":
                key = "connected"
            else:
                key = "disconnected"
        except (ConfigError, OSError):
            # ConfigError on placeholder without allow → still preview
            try:
                _ = cfg.api_base
                key = "disconnected"
            except ConfigError:
                key = "not_configured"
    label, tone = CONNECTION[key]
    return {"key": key, "label": label, "tone": tone}



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
                preview = bool(shell.dry_run_mode or not cfg.upstream_configured)
                html = (HTML_SHELL
                        .replace("__ROSTER__", json.dumps(shell.roster()))
                        .replace("__OFFLINE__", json.dumps(shell.offline_banner))
                        .replace("__COPY__", json.dumps(copy_tables_for_html()))
                        .replace("__PREVIEW__", "true" if preview else "false")
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
                conn = connection_view(cfg, client)
                pairing = client.pairing_snapshot()
                return self._json(200, {"sidecar_version": __version__,
                                        "contract": CONTRACT_VERSION,
                                        "upstream_configured": cfg.upstream_configured,
                                        "preview_mode": conn["key"] == "not_configured",
                                        "connection": conn,
                                        "pairing": pairing,
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
                    # Ruling 2: Preview mode — HTML never simulates an offline success.
                    if shell.dry_run_mode or not cfg.upstream_configured:
                        env = error_envelope(
                            "offline",
                            "Preview mode. The bridge address isn't set on this Mac, so tasks can't be sent.",
                            False)
                        env["presentation"] = {
                            "status": {"key": "error", "label": "Error", "tone": "danger"},
                            "error": error_card("offline", preview=True),
                        }
                        return self._json(503, env)
                    # Ruling 1 / O1: refuse Send while Queued or Running; never silent restart.
                    if shell.phase.value == "wait":
                        env = error_envelope("validation", TASK_IN_FLIGHT_BODY, False)
                        env["presentation"] = {
                            "status": {"key": "error", "label": "Error", "tone": "danger"},
                            "error": error_card("task_in_flight"),
                        }
                        return self._json(409, env)
                    goal = str(body.get("goal") or "").strip()
                    if not goal:
                        return self._json(400, error_envelope(
                            "validation", "Compose a non-empty Staff task before Send", False))
                    target_key = str(body.get("target") or DEFAULT_TARGET)
                    known = {e.key for e in ROSTER}
                    if target_key not in known:
                        env = error_envelope(
                            "routing",
                            f"unknown roster key {target_key!r}; known={sorted(known)}",
                            False)
                        env["presentation"] = {
                            "status": {"key": "error", "label": "Error", "tone": "danger"},
                            "error": error_card("routing", target_name=target_key),
                        }
                        return self._json(422, env)
                    if shell.phase.value in {"done", "error"}:
                        shell.end_task()
                    out = shell.start_and_wait(
                        goal=goal,
                        target_key=target_key,
                        high_priority=bool(body.get("high_priority")),
                        max_ticks=int(body.get("max_ticks") or 2))
                    return self._json(200, out)
                except ValueError as e:
                    env = error_envelope(
                        "routing" if "unknown roster" in str(e) else "validation",
                        str(e), False)
                    code = 422 if env["code"] == "routing" else 400
                    return self._json(code, env)
                except RuntimeError as e:
                    if "in flight" in str(e) or "mid-task" in str(e) or "mid-wait" in str(e):
                        env = error_envelope("validation", TASK_IN_FLIGHT_BODY, False)
                        env["presentation"] = {
                            "status": {"key": "error", "label": "Error", "tone": "danger"},
                            "error": error_card("task_in_flight"),
                        }
                        return self._json(409, env)
                    return self._json(400, error_envelope("validation", str(e), False))
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
            if path == "/_sidecar/pair":
                if not _is_loopback(self):
                    return self._json(403, error_envelope(
                        "auth.denied", "pair is loopback-only", False))
                try:
                    # Drop any prior bearer so Re-pair always hits the daemon.
                    client._live_token = None  # noqa: SLF001 — intentional re-pair
                    client.pairing_state = "not_paired"
                    info = client.pair()
                    return self._json(200, {
                        "state": "paired",
                        "expires_at": info.get("expires_at"),
                        "device_id": cfg.device_id,
                    })
                except Exception as e:  # noqa: BLE001
                    from .pairing import PairingError
                    if isinstance(e, PairingError):
                        state = client.pairing_state
                        code = e.code or "internal"
                        http = e.http_status or (403 if code == "auth.denied" else 400)
                        return self._json(http, {
                            "v": "1", "type": "error", "code": code,
                            "message": e.message, "retryable": False,
                            "correlation_id": "sc_pair",
                            "state": state,
                            "device_id": cfg.device_id,
                            "presentation": {
                                "error": error_card(code, device_id=cfg.device_id),
                            },
                        })
                    client.pairing_state = "failed"
                    client.pairing_error = str(e)
                    return self._json(400, {
                        "v": "1", "type": "error", "code": "internal",
                        "message": str(e), "retryable": False,
                        "correlation_id": "sc_pair",
                        "state": "failed",
                        "device_id": cfg.device_id,
                    })
            return self._json(404, error_envelope("validation", f"no route {path}", False))

        def log_message(self, fmt, *args):
            pass

    return Handler


def serve(cfg: SidecarConfig) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((cfg.listen_host, cfg.listen_port), make_handler(cfg))
