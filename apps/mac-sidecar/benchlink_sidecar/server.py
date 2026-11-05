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
from .config import ConfigError, SidecarConfig, ui_config
from .ui.copy import CONNECTION, TASK_IN_FLIGHT_BODY, copy_tables_for_html, error_card
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
.opt{display:block;background:#1a1d23;color:#e8eaed;border:1px solid #3c4043;border-radius:6px;text-align:left;padding:7px 10px}.opt.selected{border-color:#7fa0ff;background:#1a2240}.leads{display:flex;flex-wrap:wrap;gap:4px}.leads .opt{display:inline-block;font-size:12px}button:disabled{opacity:.5;cursor:not-allowed}.pill.warning{background:#3a2a00;color:#f9ab00}.warning-text{color:#f9ab00}.quiet-link{font-size:12px;color:#9aa0a6;margin-left:6px}.loading-rows{visibility:hidden}.loading-rows.pending{animation:reveal 0s 300ms forwards}@keyframes reveal{to{visibility:visible}}</style></head><body>
<div style="display:flex;align-items:center;justify-content:space-between;gap:12px">
  <h1 style="margin:0">Benchlink · Bridge Control <span class="muted">[Beta]</span></h1>
  <button type="button" id="connPill" class="pill neutral" aria-label="Connection: __INITIAL_CONNECTION__. Open connection details">__INITIAL_CONNECTION__</button>
</div>
<p class="muted">Send a task to Floyd's Labs staff.</p>
<div id="banner" class="banner" role="alert" style="display:none"></div>
<div id="previewBanner" class="banner" style="display:none">Preview mode. The bridge address isn't set on this Mac, so tasks can't be sent.</div>
<div class="card" id="setup">
  <strong>Setup</strong>
  <div id="setupRows" class="loading-rows pending">
    <div id="bridgeRow">Bridge: <span id="bridgeLabel">Connecting</span></div>
    <div id="pairingRow">Pairing: <span id="pairingLabel" aria-live="polite" aria-atomic="true">Not paired</span></div>
    <div id="deviceRow">Device: <code id="deviceId"></code> <button type="button" id="copyDevice" aria-label="Copy device ID">Copy device ID</button></div>
  </div>
  <button type="button" id="pairBtn" hidden>Pair this Mac</button>
  <p id="pairHint" class="muted">Device-bound sign-in. Tokens are never shown.</p>
</div>
<div class="card" id="s4" style="display:none">
  <strong id="connectionHeading" tabindex="-1">Connection</strong><p id="s4Reason" class="muted"></p><p id="s4PairingLabel" aria-live="polite"></p>
  <button type="button" id="s4Retry">Retry</button><button type="button" id="s4Repair" hidden>Re-pair</button>
  <button type="button" id="copyDiagnostics">Copy diagnostics</button>
  <a id="connectionHelp" class="quiet-link" href="https://github.com/CaptainPhantasy/benchlink/blob/main/docs/PAIRING_PROOF_RUNBOOK.md" target="_blank" rel="noopener">Connection help</a>
  <details><summary>Details</summary>
    <div>Transport: <span id="s4Transport">Tailscale HTTPS</span></div>
    <div>Host: <code id="s4Host"></code></div>
    <div id="s4ErrRow" hidden>Last error: <code id="s4Err"></code></div>
  </details>
  <button type="button" id="s4Close">Close</button>
</div>
<div class="card" id="pick">
  <strong>1 · Pick target</strong>
  <div id="roster" role="radiogroup" aria-label="Staff target"></div><p id="rosterLock" class="muted" hidden>End the current task to change target.</p>
</div>
<div class="card" id="compose">
  <strong id="composeHeading">Compose task for Floyd</strong><span id="targetLabel" hidden>Floyd</span>
  <label><input type="checkbox" id="hp"/> High priority (also ping Floyd in chat)</label>
  <label for="goal">Staff task</label><p id="goalHelp" class="muted">Describe what you need.</p>
  <textarea id="goal" aria-describedby="goalHelp" placeholder="Describe what you need."></textarea>
  <p id="sendHint" class="muted"></p>
  <button id="send" aria-describedby="sendHint" disabled>Send task</button>
  <div id="inflightNotice" class="banner" role="alert" style="display:none"><strong>Task in progress</strong><p>A task is in flight. End task first, then pick a target and send again.</p><button id="noticeEnd">End task</button></div>
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

const rost = $('roster');
let target = '__DEFAULT__';
let recovering = false, polling = false, pollRequest = false, acceptedSnapshot = null;
let inflight = false, sending = false, lastSnapshot = {}, lastSent = null, viewKey = '';
const HELP_URL = 'https://github.com/CaptainPhantasy/benchlink/blob/main/docs/PAIRING_PROOF_RUNBOOK.md';
function displayName(key) { return (roster.find(x => x.key === key) || {}).display || key; }
function selectTarget(key, focus=false) {
  if (inflight || sending) return;
  target=key; $('targetLabel').textContent=displayName(target);
  $('composeHeading').textContent='Compose task for '+displayName(target).split('·')[0].trim();
  renderRoster();
  if (focus) rost.querySelector('[aria-checked="true"]').focus();
}
function renderRoster() {
  $('composeHeading').textContent='Compose task for '+displayName(target).split('·')[0].trim();
  rost.innerHTML='';
  const leadCaption=document.createElement('p'); leadCaption.className='muted'; leadCaption.textContent='Leads';
  const leads=document.createElement('div'); leads.className='leads';
  roster.forEach((e,i)=>{
    const b=document.createElement('button'); b.type='button'; b.className='opt'+(e.key===target ? ' selected' : '');
    b.setAttribute('role','radio'); b.setAttribute('aria-checked',e.key===target ? 'true' : 'false');
    b.tabIndex=e.key===target ? 0 : -1; b.disabled=inflight || sending;
    const check=document.createElement('span'); check.setAttribute('aria-hidden','true'); check.innerHTML=e.key===target ? '<svg class="lucide lucide-check" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m20 6-11 11-5-5"/></svg> ' : ''; b.appendChild(check);
    b.appendChild(document.createTextNode(e.display+(e.default ? ' · Default' : e.group==='canary' ? ' · Canary' : '')));
    b.onclick=()=>selectTarget(e.key,true);
    b.onkeydown=event=>{
      const delta={ArrowDown:1,ArrowRight:1,ArrowUp:-1,ArrowLeft:-1}[event.key];
      let index;
      if (delta) index=(i+delta+roster.length)%roster.length;
      else if (event.key==='Home') index=0;
      else if (event.key==='End') index=roster.length-1;
      else return;
      event.preventDefault(); selectTarget(roster[index].key,true);
    };
    if (e.group==='leads') leads.appendChild(b); else rost.appendChild(b);
  });
  rost.appendChild(leadCaption); rost.appendChild(leads);
  $('rosterLock').hidden=!inflight;
}
function updateSendEnabled() {
  const btn=$('send'); let hint='';
  if (previewMode) hint="Preview mode. The bridge address isn't set on this Mac, so tasks can't be sent.";
  else if (recovering) hint='Recovering task';
  else if (isInterruptedTask()) hint='Reconnect to resume the current task.';
  else if (sending) hint='Sending';
  else if (inflight) hint='A task is in flight. End task first, then pick a target and send again.';
  else if (connectionKey==='disconnected' || connectionKey==='connecting') hint='Reconnect to send.';
  else if (pairingBusy) hint=repairing ? 'Re-pairing…' : 'Pairing…';
  else if (pairingState==='not_allowed') hint="This Mac isn't allowed. Ask Larry or Claire to enroll it.";
  else if (autoRepairFailed || (pairingState==='expired' && autoRepairAttempted)) hint='Pairing expired. Re-pair to send.';
  else if (pairingState==='not_paired') hint='Pair this Mac to send tasks.';
  else if (pairingState==='failed') hint='Pairing failed. Re-pair to send.';
  btn.disabled=!!hint || !$('goal').value.trim(); $('sendHint').textContent=hint;
  $('goal').readOnly=inflight; $('hp').disabled=inflight;
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
async function endTask(clear=true) {
  const {r,j}=await post('/_sidecar/ux/end');
  if (!r.ok) { showError(j); return false; }
  $('inflightNotice').style.display='none'; show(j);
  if (clear) { $('goal').value=''; $('hp').checked=false; }
  updateSendEnabled(); $('goal').focus(); return true;
}
async function pollUntilSettled(snap) {
  if (polling) return;
  polling=true;
  try {
    while (snap.phase==='wait') {
      await new Promise(res=>setTimeout(res,2000));
      if (!inflight) break;
      const out=await post('/_sidecar/ux/poll');
      if (!out.r.ok) { showError(out.j); break; }
      snap=out.j; show(snap);
    }
  } catch (e) { showError({code:'offline'}); }
  finally { polling=false; updateSendEnabled(); }
}
async function retryPoll() {
  if (polling || pollRequest) return;
  pollRequest=true;
  try {
    const {r,j}=await post('/_sidecar/ux/poll');
    if (!r.ok) showError(j);
    else { show(j); await pollUntilSettled(j); }
  } catch (e) { showError({code:'offline'}); }
  finally { pollRequest=false; updateSendEnabled(); }
}
async function retryConnectionTask() {
  if (recovering || sending || polling) return;
  recovering=true; updateSendEnabled();
  let resetTerminal=false;
  try {
    await refreshStatus();
    if (connectionKey!=='connected') return;
    if (isInterruptedTask()) await retryPoll();
    else resetTerminal=lastSnapshot.phase==='error';
  } finally { recovering=false; updateSendEnabled(); }
  if (resetTerminal) return retryTerminal();
}
async function retryTerminal() {
  if (recovering || sending || inflight) return;
  recovering=true; updateSendEnabled();
  try {
    const {r,j}=await post('/_sidecar/ux/retry');
    if (!r.ok) {
      showError(j);
      if (r.status===409) { inflight=true; renderRoster(); updateSendEnabled(); }
      return;
    }
    // The server keeps the draft and never dispatches here. Send remains explicit.
    target=j.target || target; $('targetLabel').textContent=displayName(target);
    $('goal').value=j.goal || ''; $('hp').checked=!!j.high_priority;
    show(j); renderRoster();
  } catch (e) { showError({code:'offline'}); }
  finally { recovering=false; updateSendEnabled(); }
}
async function retryTask() {
  if (recovering || sending || inflight) return;
  if (lastSnapshot.phase==='error') return retryTerminal();
  return retryPoll();
}
async function resend(toFloyd=false) {
  const saved = lastSent || {goal:$('goal').value, target, high_priority:$('hp').checked};
  if (!await endTask(false)) return;
  target=toFloyd ? 'floyd' : saved.target; $('targetLabel').textContent=displayName(target);
  $('goal').value=saved.goal; $('hp').checked=saved.high_priority; renderRoster(); await sendTask();
}
const actions = {
  'Retry': async () => {
    // A known terminal result is task recovery, not a connection-only retry.
    if (lastSnapshot.phase==='error' && (lastSnapshot.result || {}).status==='failed') return retryTerminal();
    if (((lastSnapshot.presentation || {}).error || {}).details_code === 'offline') {
      try { const r=await fetch('/v1/health'); if (!r.ok) showError({code:'offline'}); else banner.style.display='none'; }
      catch (e) { showError({code:'offline'}); }
    } else await retryTask();
  },
  'End task': () => endTask(), 'New task': () => endTask(),
  'Edit task': () => { $('goal').readOnly=false; $('goal').focus(); },
  'Send again': () => resend(), 'Send to Floyd': () => resend(true),
  'Copy device ID': button => copyText(button, $('deviceId') ? $('deviceId').textContent : deviceId),
  'Copy diagnostics': button => copyText(button, safeDiagnostics()),
  'Details': () => { $('errDetails').open=true; $('errDetails').querySelector('summary').focus(); },
  'Connection help': () => window.open(HELP_URL, '_blank', 'noopener')
};
let deviceId = '';
function wireAction(button, label) {
  button.style.display = label && actions[label] ? 'inline-block' : 'none';
  button.textContent = label || '';
  button.onclick = actions[label] ? () => Promise.resolve(actions[label](button)).catch(()=>showError({code:'offline'})) : null;
}
function isInterruptedTask() {
  return !!(acceptedSnapshot && lastSnapshot.phase==='error' &&
    (lastSnapshot.error || {}).code==='offline' &&
    !['failed','succeeded'].includes((lastSnapshot.result || {}).status));
}
function showError(j) {
  const keep=j.code==='offline' && acceptedSnapshot ? acceptedSnapshot : {};
  show({...keep, phase:'error', wait_status:'error', error:{code:j.code},
    presentation:{status:COPY.status.error, error:(j.presentation || {}).error || fallbackError(j)}});
}
function show(snap) {
  // Retain accepted IDs across a failed local fetch as well as a daemon outage.
  if (snap.task_id && snap.session_id && (snap.task_accepted || snap.phase==='wait')) acceptedSnapshot={...snap};
  if (snap.phase==='pick' || snap.phase==='done' || (snap.result || {}).status==='failed') acceptedSnapshot=null;
  lastSnapshot=snap;
  const pres=snap.presentation || {}, st=pres.status || COPY.status[snap.wait_status] || COPY.status.idle;
  const key=JSON.stringify([snap.phase,snap.wait_status,snap.task_id,pres.error]);
  const changed=key!==viewKey; viewKey=key;
  const pill=$('statusPill'); pill.textContent=st.label; pill.className='pill '+(st.tone || 'neutral');
  inflight=snap.phase==='wait'; renderRoster(); updateSendEnabled();
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
    const err=pres.error || fallbackError(snap.error || {});
    $('errTitle').textContent=err.title; $('errBody').textContent=err.body;
    for (const [id,value] of [['errCode',err.details_code],['errHttp',err.http],['errCorr',err.correlation_id]]) {
      $(id).textContent=value == null ? '' : value; $(id+'Row').hidden=value == null || value==='';
    }
    wireAction($('errPrimary'),err.primary); wireAction($('errSecondary'),err.secondary);
    $('errTitle').focus();
  }
}
async function sendTask() {
  const goal=$('goal').value.trim(); if (!goal || isInterruptedTask() || recovering || sending || inflight || previewMode || pairingBusy || connectionKey!=='connected') return;
  if (pairingState==='expired' && !autoRepairAttempted) { if (!await pairDevice(true,true)) return; }
  if (pairingState!=='paired') return;
  $('inflightNotice').style.display='none'; sending=true; updateSendEnabled(); $('send').textContent='Sending';
  lastSent={target,goal,high_priority:$('hp').checked};
  try {
    const {r,j}=await post('/_sidecar/ux/run',lastSent);
    sending=false; $('send').textContent='Send task';
    if (r.status===409) { inflight=true; showInflight(); updateSendEnabled(); return; }
    if (!r.ok || !j.snapshot) { showError(j); return; }
    let snap=j.snapshot; show(snap); if (snap.phase==='wait') $('statusHeading').focus();
    await pollUntilSettled(snap);
  } catch (e) { showError({code:'offline'}); }
  finally { sending=false; $('send').textContent='Send task'; updateSendEnabled(); }
}
function showInflight() { $('inflightNotice').style.display='block'; $('noticeEnd').focus(); }
$('noticeEnd').onclick=()=>endTask().catch(()=>showError({code:'offline'}));
$('send').onclick=sendTask;
$('end').onclick=()=>endTask().catch(()=>showError({code:'offline'}));
$('goal').addEventListener('input',updateSendEnabled);
$('copySession').onclick=()=>copyText($('copySession'),$('resultSession').textContent);
$('copyTask').onclick=()=>copyText($('copyTask'),$('resultTask').textContent);
$('copyCorr').onclick=()=>copyText($('copyCorr'),$('errCorr').textContent);
const previewMode = __PREVIEW__;
let pairingState='not_paired', connectionKey=previewMode ? 'not_configured' : 'connecting';
let pairingBusy=false, repairing=false, autoRepairAttempted=false, autoRepairFailed=false, lastStatus={}, bannerKey='';
if (previewMode) $('previewBanner').style.display='block';
function showBanner(err) {
  const key=err.title+'|'+err.body;
  if (key===bannerKey && banner.style.display!=='none') return;
  bannerKey=key; banner.replaceChildren();
  const title=document.createElement('strong'); title.textContent=err.title;
  const body=document.createElement('p'); body.textContent=err.body;
  banner.appendChild(title); banner.appendChild(body);
  if (connectionKey==='disconnected') {
    const retry=document.createElement('button'); retry.textContent='Retry'; retry.onclick=retryConnectionTask; banner.appendChild(retry);
  }
  banner.style.display='block'; title.tabIndex=-1; title.focus();
}
function openConnection() { $('s4').style.display='block'; $('connectionHeading').focus(); }
function renderPairingBusy() {
  if (!pairingBusy) return;
  $('setup').hidden=false; $('pairBtn').hidden=false; $('pairBtn').disabled=true;
  const label=repairing ? 'Re-pairing…' : 'Pairing…';
  $('pairBtn').textContent=label; $('pairingLabel').textContent=label; $('s4PairingLabel').textContent=label;
}
async function refreshStatus(allowAutoRepair=true) {
  try {
    const r=await fetch('/_sidecar/status'); if (!r.ok) throw new Error('status');
    const st=await r.json(); lastStatus=st;
    const conn=st.connection || {}, pair=st.pairing || {};
    connectionKey=previewMode ? 'not_configured' : (conn.key || 'disconnected');
    pairingState=pair.state || 'not_paired';
    const connLabel=previewMode ? 'Preview mode (offline)' : (conn.label || 'Disconnected');
    $('bridgeLabel').textContent=connLabel; $('deviceId').textContent=pair.device_id || '';
    $('setupRows').className='';
    let label=pair.label || 'Not paired';
    const exp=pair.expires_at && new Date(pair.expires_at);
    if (pairingState==='paired' && exp && !Number.isNaN(exp.getTime())) {
      label='Paired · expires '+exp.toLocaleTimeString([], {hour:'numeric',minute:'2-digit',timeZone:'America/Indiana/Indianapolis'})+' ET';
      $('pairingLabel').className=(exp.getTime()-Date.now()<120000) ? 'warning-text' : '';
    } else $('pairingLabel').className='';
    $('pairingLabel').textContent=label; $('s4PairingLabel').textContent=label;
    $('s4PairingLabel').className=$('pairingLabel').className;
    $('pairBtn').hidden=previewMode || pairingState==='paired' || pairingState==='not_allowed';
    $('pairBtn').disabled=false;
    $('pairBtn').textContent=pairingState==='not_paired' ? 'Pair this Mac' : 'Re-pair';
    $('pairHint').textContent=previewMode ? 'Set the bridge address first.' : 'Device-bound sign-in. Tokens are never shown.';
    $('setup').hidden=connectionKey==='connected' && pairingState==='paired';
    const pill=$('connPill'); pill.textContent=connLabel;
    pill.className='pill '+(previewMode ? 'warning' : (conn.tone || 'neutral'));
    pill.setAttribute('aria-label','Connection: '+connLabel+'. Open connection details');
    let code=pair.error_code;
    if (pairingState==='not_allowed') code='auth.denied';
    else if (pairingState==='expired' && autoRepairFailed) code='auth.expired';
    const card=code ? fallbackError({code}) : null;
    $('s4Reason').textContent=previewMode ? COPY.errors['offline.preview'].body : connectionKey==='disconnected' ? COPY.errors.offline.body : card ? card.body : pairingState==='paired' ? 'This Mac is connected and paired.' : COPY.errors['auth.unpaired'].body;
    $('s4Host').textContent=(st.config || {}).bridge_host || 'Not configured';
    $('s4Transport').textContent='Tailscale HTTPS';
    $('s4Err').textContent=code || ''; $('s4ErrRow').hidden=!code;
    $('s4Repair').hidden=previewMode || pairingState==='paired' || pairingState==='not_allowed';
    if (connectionKey==='disconnected') showBanner({title:"Can't reach the bridge",body:'Check Tailscale is connected, then retry.'});
    else if (card) showBanner(card);
    else banner.style.display='none';
    renderPairingBusy(); updateSendEnabled();
    if (connectionKey==='connected' && isInterruptedTask() && !recovering && !sending && !polling) await retryPoll();
    if (allowAutoRepair && pairingState==='expired' && !autoRepairAttempted && !pairingBusy && connectionKey==='connected') {
      await pairDevice(true,true);
    }
  } catch (e) {
    connectionKey=previewMode ? 'not_configured' : 'disconnected';
    if (!previewMode) {
      $('connPill').textContent='Disconnected';
      $('connPill').setAttribute('aria-label','Connection: Disconnected. Open connection details');
      showBanner(fallbackError({code:'offline'}));
    }
    updateSendEnabled();
  }
}
async function pairDevice(repair=false, automatic=false) {
  if (previewMode || pairingBusy) return false;
  pairingBusy=true; repairing=repair;
  if (automatic || pairingState==='expired') autoRepairAttempted=true;
  renderPairingBusy(); updateSendEnabled();
  let ok=false, failure=null;
  try {
    const {r,j}=await post('/_sidecar/pair'); ok=r.ok;
    if (!ok) failure=(j.presentation || {}).error || fallbackError(j);
    else { autoRepairFailed=false; autoRepairAttempted=false; }
  } catch (e) { failure=fallbackError({code:'offline'}); }
  finally {
    if (!ok && automatic) autoRepairFailed=true;
    pairingBusy=false; repairing=false;
    // Refresh settled labels and enable Send without another keystroke.
    await refreshStatus(false); updateSendEnabled();
  }
  if (failure) {
    if (automatic && pairingState!=='not_allowed') failure=fallbackError({code:'auth.expired'});
    showBanner(failure); showError({code:failure.details_code,presentation:{error:failure}});
  }
  if (ok) $('statusHeading').focus();
  return ok;
}
actions['Pair this Mac']=()=>pairDevice(false);
actions['Re-pair']=()=>pairDevice(true);
actions['Open connection']=openConnection;
actions['Retry']=()=>((lastSnapshot.presentation || {}).error || {}).details_code==='offline' ? retryConnectionTask() : retryTask();
$('connPill').onclick=openConnection;
$('s4Close').onclick=()=>{ $('s4').style.display='none'; $('connPill').focus(); };
$('s4Retry').onclick=retryConnectionTask; $('s4Repair').onclick=()=>pairDevice(true);
$('pairBtn').onclick=()=>pairDevice(pairingState!=='not_paired');
$('copyDevice').onclick=()=>copyText($('copyDevice'),$('deviceId').textContent);
$('copyDiagnostics').onclick=()=>copyText($('copyDiagnostics'),JSON.stringify(lastStatus));
refreshStatus(); setInterval(refreshStatus,30000);
$('targetLabel').textContent=displayName(target); renderRoster(); updateSendEnabled();
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
                preview = bool(shell.dry_run_mode or not cfg.upstream_configured)
                html = (HTML_SHELL
                        .replace("__ROSTER__", json.dumps(shell.roster()))
                        .replace("__OFFLINE__", json.dumps(bool(shell.offline_banner)))
                        .replace("__PREVIEW__", "true" if preview else "false")
                        .replace("__INITIAL_CONNECTION__", "Preview mode (offline)" if preview else "Connecting")
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
                conn = connection_view(cfg, client)
                pairing = client.pairing_snapshot()
                return self._json(200, {"sidecar_version": __version__,
                                        "contract": CONTRACT_VERSION,
                                        "upstream_configured": cfg.upstream_configured,
                                        "preview_mode": conn["key"] == "not_configured",
                                        "connection": conn,
                                        "pairing": pairing,
                                        "config": ui_config(cfg),
                                        "roster_keys": [e.key for e in ROSTER]})
            if path == "/_sidecar/ux":
                return self._json(200, BridgeControlShell(cfg).snapshot())
            return self._json(404, error_envelope("validation", f"no route {path}", False))

        def _task_response(self, path: str):
            """Compute task state/reply while the caller owns the operation lock."""
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
                        return (503, env)
                    # Ruling 1 / O1: refuse Send while Queued or Running; never silent restart.
                    if shell.phase.value == "wait" or shell.can_resume_interrupted_wait():
                        env = error_envelope("validation", TASK_IN_FLIGHT_BODY, False)
                        env["presentation"] = {
                            "status": {"key": "error", "label": "Error", "tone": "danger"},
                            "error": error_card("task_in_flight"),
                        }
                        return (409, env)
                    goal = str(body.get("goal") or "").strip()
                    if not goal:
                        env = error_envelope("validation", "Compose a non-empty Staff task before Send", False)
                        env["presentation"] = {"error": error_card("validation")}
                        return (422, env)
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
                        return (422, env)
                    if shell.phase.value in {"done", "error"}:
                        shell.end_task()
                    out = shell.start_and_wait(
                        goal=goal,
                        target_key=target_key,
                        high_priority=bool(body.get("high_priority")),
                        max_ticks=int(body.get("max_ticks") or 2))
                    return (200, {"snapshot": out["snapshot"],
                                            "send": {"mode": out.get("send", {}).get("mode")}})
                except TaskInFlight:
                    env = error_envelope("validation", TASK_IN_FLIGHT_BODY, False)
                    env["presentation"] = {"error": error_card("task_in_flight")}
                    return (409, env)
                except ValueError:
                    env = error_envelope("validation", "Task couldn't be sent", False)
                    env["presentation"] = {"error": error_card("validation")}
                    return (422, env)
                except Exception:  # Local presentation, not a wire-contract change.
                    env = error_envelope("internal", "Something went wrong on the bridge", False)
                    env["presentation"] = {"error": error_card("internal")}
                    return (500, env)
            if path == "/_sidecar/ux/retry":
                shell = live["shell"]
                if shell.phase.value == "wait":
                    env = error_envelope("validation", "Task in progress", False)
                    env["presentation"] = {"error": error_card("task_in_flight")}
                    return (409, env)
                shell.retry_terminal_error()
                return (200, shell.snapshot())
            if path == "/_sidecar/ux/poll":
                shell = live["shell"]
                if shell.can_resume_interrupted_wait():
                    shell.resume_interrupted_wait()
                elif shell.phase.value == "wait":
                    shell.wait_once()
                return (200, shell.snapshot())

        def do_POST(self):  # noqa: N802
            if not self._allow_request(mutation=True):
                return
            path = self.path.split("?", 1)[0]
            if path in {"/_sidecar/ux/run", "/_sidecar/ux/retry", "/_sidecar/ux/poll"}:
                if not run_lock.acquire(blocking=False):
                    message = "Task in progress" if path.endswith("/retry") else TASK_IN_FLIGHT_BODY
                    env = error_envelope("validation", message, False)
                    env["presentation"] = {"error": error_card("task_in_flight")}
                    return self._json(409, env)
                try:
                    status, payload = self._task_response(path)
                    # Freeze the reply while state is protected. Transport is not
                    # task work: a received reply must permit the next operation.
                    body = json.dumps(payload).encode()
                finally:
                    run_lock.release()
                return self._json(status, body)
            if path == "/_sidecar/ux/end":
                live["shell"].end_task()
                return self._json(200, live["shell"].snapshot())
            if path == "/_sidecar/pair":
                if not _is_loopback(self):
                    env = error_envelope("auth.denied", "Pair is loopback-only", False)
                    env["presentation"] = {"error": error_card("auth.denied", device_id=cfg.device_id)}
                    return self._json(403, env)
                try:
                    info = client.pair()
                    return self._json(200, {"state": "paired", "expires_at": info.get("expires_at"),
                                             "device_id": cfg.device_id})
                except Exception as exc:
                    code = client.pairing_failure_code(exc)
                    card = error_card(code, device_id=cfg.device_id)
                    env = error_envelope(code, card["body"], False)
                    env.update(state=client.pairing_state, device_id=cfg.device_id)
                    card["correlation_id"] = env["correlation_id"]
                    env["presentation"] = {"error": card}
                    status = getattr(exc, "http_status", None) or {
                        "auth.denied": 403, "auth.expired": 401,
                        "offline": 503, "internal": 500}.get(code, 400)
                    return self._json(status, env)
            return self._json(404, error_envelope("validation", f"no route {path}", False))

        def log_message(self, fmt, *args):
            pass

    return Handler


def serve(cfg: SidecarConfig) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((cfg.listen_host, cfg.listen_port), make_handler(cfg))
