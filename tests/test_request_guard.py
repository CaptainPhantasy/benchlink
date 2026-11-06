"""Local browser requests must not borrow the enrolled sidecar authority."""
import json
import threading
from dataclasses import replace
from http.client import HTTPConnection

import pytest
from benchlink_sidecar import server
from benchlink_sidecar.config import load_config


@pytest.fixture
def chromium_browser():
    """Browser checks are optional unless Playwright and Chromium are installed."""
    sync = pytest.importorskip("playwright.sync_api")
    with sync.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        except sync.Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Chromium is unavailable; run playwright install chromium")
            raise
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture
def guarded_sidecar(monkeypatch):
    calls = []
    class Phase:
        value = "pick"
    class RecordingShell:
        phase = Phase()
        offline_banner = None
        def roster(self): return []
        def snapshot(self): return {"phase": "pick"}
        def start_and_wait(self, **kwargs):
            calls.append("run")
            return {"snapshot": {"phase": "pick"}, "send": {"mode": "live"}}
        def end_task(self): calls.append("end")
        def retry_terminal_error(self): calls.append("retry")
        def can_resume_interrupted_wait(self): return False
        def has_unfinished_task(self): return False
    monkeypatch.setattr(server, "BridgeControlShell", lambda *a, **kw: RecordingShell())
    monkeypatch.setattr(server.BridgeClient, "health", lambda self: (calls.append("health") or (200, {"type": "health.pong"})))
    cfg = load_config({"BENCHLINK_BRIDGE_BASE_URL": "http://127.0.0.1:9"})
    sc = server.serve(replace(cfg, listen_port=0))
    thread = threading.Thread(target=sc.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    thread.start()
    try: yield sc.server_address[1], calls
    finally: sc.shutdown(); sc.server_close(); thread.join()


def request(port, method="POST", path="/_sidecar/ux/run", headers=None):
    conn = HTTPConnection("127.0.0.1", port, timeout=3)
    defaults = {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json"}
    defaults.update(headers or {})
    conn.request(method, path, body=json.dumps({"goal": "boundary fixture"}) if method == "POST" else None, headers=defaults)
    response = conn.getresponse(); body = response.read(); status = response.status
    conn.close()
    return status, body


@pytest.mark.parametrize("path", ["/_sidecar/ux/run", "/_sidecar/ux/end", "/_sidecar/ux/poll", "/_sidecar/ux/retry", "/_sidecar/pair"])
def test_foreign_browser_request_rejected_before_any_effect(guarded_sidecar, path):
    port, calls = guarded_sidecar
    status, _ = request(port, path=path, headers={"Origin": "https://untrusted.invalid", "Host": "untrusted.invalid", "Content-Type": "text/plain"})
    assert status == 403
    assert calls == []


@pytest.mark.parametrize("header", [
    {"Origin": "https://untrusted.invalid"}, {"Origin": "null"},
    {"Origin": "http://localhost:1"}, {"Host": "untrusted.invalid"},
    {"Host": "127.0.0.1.attacker.invalid"}, {"Host": "127.0.0.1:1"},
    {"Host": "user@127.0.0.1"}, {"Host": "127.0.0.1,localhost"},
    {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
])
def test_untrusted_host_origin_or_fetch_metadata_rejected(guarded_sidecar, header):
    port, calls = guarded_sidecar
    assert request(port, headers=header)[0] == 403
    assert calls == []


@pytest.mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data", ""])
def test_mutations_require_json(guarded_sidecar, content_type):
    port, calls = guarded_sidecar
    assert request(port, headers={"Content-Type": content_type})[0] == 415
    assert calls == []


@pytest.mark.parametrize("with_origin", [False, True])
def test_same_origin_json_and_nonbrowser_cli_work(guarded_sidecar, with_origin):
    port, calls = guarded_sidecar
    headers = {"Origin": f"http://127.0.0.1:{port}", "Sec-Fetch-Site": "same-origin"} if with_origin else {}
    assert request(port, headers=headers)[0] == 200
    assert calls == ["run"]


def test_foreign_host_cannot_trigger_health_get(guarded_sidecar):
    port, calls = guarded_sidecar
    assert request(port, "GET", "/v1/health", {"Host": "rebinding.invalid"})[0] == 403
    assert calls == []


def test_same_origin_browser_json_reaches_loopback(guarded_sidecar, chromium_browser):
    port, calls = guarded_sidecar
    base = f"http://127.0.0.1:{port}"
    page = chromium_browser.new_page()
    page.route("**/*", lambda r: r.continue_() if r.request.url.startswith(base + "/") else r.abort())
    page.goto(base)
    response = page.evaluate("async () => {const r = await fetch('/_sidecar/ux/run', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({goal:'browser fixture'})}); return r.status}")
    assert response == 200 and calls == ["run"]


@pytest.mark.parametrize("name,values,expected", [
    ("Host", ["127.0.0.1", "localhost"], 403),
    ("Origin", ["null", "null"], 403),
    ("Content-Type", ["application/json", "text/plain"], 415),
])
def test_duplicate_security_headers_rejected(guarded_sidecar, name, values, expected):
    port, calls = guarded_sidecar
    conn = HTTPConnection("127.0.0.1", port, timeout=3)
    conn.putrequest("POST", "/_sidecar/ux/run", skip_host=True)
    if name != "Host": conn.putheader("Host", f"127.0.0.1:{port}")
    if name != "Content-Type": conn.putheader("Content-Type", "application/json")
    for value in values: conn.putheader(name, value)
    conn.putheader("Content-Length", "2"); conn.endheaders(b"{}")
    response = conn.getresponse(); response.read()
    assert response.status == expected and calls == []
    conn.close()


def test_cross_origin_browser_cannot_mutate_loopback(guarded_sidecar, chromium_browser):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    port, calls = guarded_sidecar
    class ForeignPage(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"<!doctype html><title>foreign fixture</title>"
            self.send_response(200); self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
        def log_message(self, *args): pass
    foreign = ThreadingHTTPServer(("127.0.0.1", 0), ForeignPage)
    thread = threading.Thread(target=foreign.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    thread.start()
    try:
        page = chromium_browser.new_page()
        page.goto(f"http://127.0.0.1:{foreign.server_address[1]}")
        page.evaluate("async url => {try {await fetch(url, {method:'POST',mode:'no-cors',headers:{'Content-Type':'text/plain'},body:'{\"goal\":\"untrusted browser fixture\"}'})} catch (_) {}}", f"http://127.0.0.1:{port}/_sidecar/ux/run")
        assert calls == []
    finally: foreign.shutdown(); foreign.server_close(); thread.join()
