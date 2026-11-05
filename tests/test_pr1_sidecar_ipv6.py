"""Codex T0D6: the accepted IPv6 loopback listen host must actually serve (real ::1 HTTP)."""
import json
import socket
import threading
from contextlib import contextmanager
from dataclasses import replace
from http.client import HTTPConnection

import pytest

from benchlink_sidecar import server
from benchlink_sidecar.__main__ import main as sidecar_main
from benchlink_sidecar.config import load_config


def _ipv6_loopback_available():
    try:
        with socket.socket(socket.AF_INET6) as s:
            s.bind(("::1", 0))
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _ipv6_loopback_available(), reason="no IPv6 loopback")


@contextmanager
def ipv6_sidecar():
    cfg = load_config({"BENCHLINK_SIDECAR_LISTEN_HOST": "::1", "BENCHLINK_SIDECAR_LISTEN_PORT": "8787"})
    assert cfg.listen_host == "::1"
    sc = server.serve(replace(cfg, listen_port=0))
    thread = threading.Thread(target=sc.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    thread.start()
    try:
        yield sc
    finally:
        sc.shutdown(); sc.server_close(); thread.join()


def _get(port, path, headers=None, method="GET", body=None):
    conn = HTTPConnection("::1", port, timeout=3)  # sends Host: [::1]:<port>
    try:
        conn.request(method, path, body=body, headers=headers or {})
        r = conn.getresponse()
        return r.status, r.read()
    finally:
        conn.close()


def test_ipv6_listen_host_binds_ipv6_socket_and_serves_bridge_control():
    with ipv6_sidecar() as sc:
        assert sc.address_family == socket.AF_INET6
        host, port = sc.server_address[:2]
        assert host == "::1" and port > 0
        st, html = _get(port, "/")
        assert st == 200 and b"Bridge Control" in html
        st, snap = _get(port, "/_sidecar/ux")
        assert st == 200 and json.loads(snap)["phase"] == "pick"
        st, status = _get(port, "/_sidecar/status")
        assert st == 200 and json.loads(status)["config"]["BENCHLINK_SIDECAR_LISTEN_HOST"] == "::1"


def test_ipv6_listener_keeps_same_origin_guard():
    with ipv6_sidecar() as sc:
        port = sc.server_address[1]
        same = {"Origin": f"http://[::1]:{port}", "Sec-Fetch-Site": "same-origin"}
        assert _get(port, "/_sidecar/ux", same)[0] == 200
        for bad in ({"Origin": "http://evil.invalid"}, {"Origin": f"http://[::1]:{port + 1}"},
                    {"Sec-Fetch-Site": "cross-site"}, {"Host": "evil.invalid"},
                    {"Host": f"[::1]:{port + 1}"}):
            assert _get(port, "/_sidecar/ux", bad)[0] == 403, bad
        # mutations still need same-origin JSON; a cross-origin JSON POST is denied
        st, _ = _get(port, "/_sidecar/ux/end", {"Origin": "http://evil.invalid", "Content-Type": "application/json"},
                     method="POST", body=b"{}")
        assert st == 403
        st, _ = _get(port, "/_sidecar/ux/end", {"Content-Type": "text/plain"}, method="POST", body=b"{}")
        assert st == 415
        st, body = _get(port, "/_sidecar/ux/end", {**same, "Content-Type": "application/json"},
                        method="POST", body=b"{}")
        assert st == 200 and json.loads(body)["phase"] == "pick"


def test_ipv4_listen_hosts_still_use_ipv4_sockets():
    for host in ("127.0.0.1", "localhost"):
        cfg = load_config({"BENCHLINK_SIDECAR_LISTEN_HOST": host})
        sc = server.serve(replace(cfg, listen_port=0))
        try:
            assert sc.address_family == socket.AF_INET
        finally:
            sc.server_close()


def test_serve_banner_brackets_ipv6_urls(monkeypatch, capsys):
    class Stop:
        def serve_forever(self):
            raise KeyboardInterrupt
    monkeypatch.setattr(server, "serve", lambda cfg: Stop())
    monkeypatch.setenv("BENCHLINK_SIDECAR_LISTEN_HOST", "::1")
    assert sidecar_main(["serve"]) == 0
    out = capsys.readouterr().out
    assert "http://[::1]:8787/v1" in out and "http://[::1]:8787/" in out and "http://::1:" not in out


def test_daemon_ipv6_listen_host_serves_and_brackets_http_callback_url():
    from benchlink_testkit import Harness
    from benchlink_daemon.server import listen_url
    from benchlink_sidecar.ux import BridgeControlShell
    env = {"BENCHLINK_RESULT_SOURCE": "http_callback", "BENCHLINK_FAKE_TRANSPORT_MODE": "echo"}
    with Harness(adapter_env=env, daemon_env={"BENCHLINK_BRIDGE_LISTEN_HOST": "::1"}) as h:
        assert h.srv.address_family == socket.AF_INET6
        port = h.srv.server_address[1]
        assert h.base == f"http://[::1]:{port}" == listen_url("::1", port)
        assert h.adapter.source.base_url == f"http://[::1]:{port}"
        st, pong, _ = h.http("GET", "/v1/health")
        assert st == 200 and pong["type"] == "health.pong"
        shell = BridgeControlShell(h.sidecar_cfg, client=h.client, poll_sleep=0.05, max_wait_ticks=100)
        snap = shell.start_and_wait("ipv6 callback round trip")["snapshot"]
        assert snap["phase"] == "done" and snap["result"]["status"] == "succeeded", snap


def test_daemon_banner_and_health_use_bracketed_ipv6_urls(monkeypatch, capsys):
    from benchlink_daemon import __main__ as daemon_main
    from benchlink_daemon import server as daemon_server
    seen = []

    class Stop:
        def serve_forever(self):
            raise KeyboardInterrupt
    monkeypatch.setattr(daemon_server, "serve", lambda cfg: Stop())
    monkeypatch.setenv("BENCHLINK_BRIDGE_LISTEN_HOST", "::1")
    assert daemon_main.main(["serve"]) == 0
    assert "http://[::1]:8790/v1" in capsys.readouterr().out

    def fake_urlopen(url, timeout):
        seen.append(url)
        raise OSError("not running")
    monkeypatch.setattr(daemon_main.urllib.request, "urlopen", fake_urlopen)
    assert daemon_main.main(["health"]) == 4
    assert seen == ["http://[::1]:8790/v1/health"]
