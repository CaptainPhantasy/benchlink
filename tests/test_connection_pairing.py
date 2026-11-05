"""PR3: connection, pairing, Preview mode (Amir polish rulings 2–3)."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from dataclasses import replace

from benchlink_testkit import Harness

from benchlink_sidecar.config import load_config
from benchlink_sidecar.server import serve
from benchlink_sidecar.ui.copy import CONNECTION, PAIRING


def _serve(cfg):
    sc = serve(replace(cfg, listen_port=0))
    threading.Thread(target=sc.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    return sc, f"http://127.0.0.1:{sc.server_address[1]}"


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=3) as r:
        return r.status, r.read().decode() if path in {"/", "/index.html"} else json.loads(r.read())


def _post(base, path, body=None):
    req = urllib.request.Request(
        base + path, data=json.dumps(body or {}).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_connection_and_pairing_vocabularies():
    assert CONNECTION["not_configured"][0] == "Preview mode (offline)"
    assert set(PAIRING) == {"paired", "not_paired", "expired", "failed", "not_allowed"}


def test_preview_mode_html_disables_send_and_run_refuses_simulation():
    cfg = load_config({})  # URL unset
    sc, base = _serve(cfg)
    try:
        st, html = _get(base, "/")
        assert st == 200
        assert "Preview mode" in html
        assert 'id="previewBanner"' in html or "Preview mode" in html
        # Send must be disabled in markup when preview
        assert "previewMode" in html or "Send is disabled" in html or "tasks can't be sent" in html
        st2, body = _post(base, "/_sidecar/ux/run", {"target": "floyd", "goal": "should not simulate"})
        assert st2 in {503, 400}
        assert body["type"] == "error"
        assert body["code"] == "offline"
        blob = json.dumps(body)
        assert "[offline demo]" not in blob
        assert "access_token" not in blob
    finally:
        sc.shutdown(); sc.server_close()


def test_pair_enrolled_device_returns_state_without_token():
    with Harness() as h:
        sc, base = _serve(h.sidecar_cfg)
        try:
            st, body = _post(base, "/_sidecar/pair", {})
            assert st == 200, body
            assert body["state"] == "paired"
            assert body["device_id"]
            assert body.get("expires_at")
            assert "access_token" not in body
            assert "token" not in json.dumps(body).lower().replace("token_type", "")
            # status reflects paired
            st2, status = _get(base, "/_sidecar/status")
            assert st2 == 200
            assert status["pairing"]["state"] == "paired"
            assert status["pairing"]["expires_at"]
            assert "access_token" not in json.dumps(status)
            assert status["connection"]["key"] in {"connected", "connecting", "disconnected", "not_configured"}
        finally:
            sc.shutdown(); sc.server_close()


def test_pair_unenrolled_device_is_not_allowed():
    with Harness() as h:
        # Point sidecar at a fresh unenrolled key
        import tempfile
        from pathlib import Path
        from benchlink_sidecar.pairing import generate_key
        td = tempfile.mkdtemp()
        key = str(Path(td) / "unenrolled.pem")
        generate_key(key)
        cfg = replace(h.sidecar_cfg, device_key_path=key, device_id="stranger-mac")
        sc, base = _serve(cfg)
        try:
            st, body = _post(base, "/_sidecar/pair", {})
            assert st in {403, 401, 400}
            assert body.get("state") == "not_allowed" or body.get("code") == "auth.denied"
            assert "access_token" not in json.dumps(body)
            st2, status = _get(base, "/_sidecar/status")
            assert status["pairing"]["state"] == "not_allowed"
            assert status["pairing"]["device_id"] == "stranger-mac"
        finally:
            sc.shutdown(); sc.server_close()


def test_html_has_s1_rows_and_pair_controls():
    with Harness() as h:
        sc, base = _serve(h.sidecar_cfg)
        try:
            _, html = _get(base, "/")
            assert 'id="bridgeRow"' in html
            assert 'id="pairingRow"' in html
            assert 'id="deviceRow"' in html
            assert 'id="pairBtn"' in html
            assert "Tokens are never shown" in html
            assert "[offline demo]" not in html
        finally:
            sc.shutdown(); sc.server_close()


def test_status_five_pairing_states_shape():
    """Status pairing.state is always one of the five vocabulary keys."""
    cfg = load_config({})
    sc, base = _serve(cfg)
    try:
        _, status = _get(base, "/_sidecar/status")
        assert status["pairing"]["state"] in PAIRING
        assert status["connection"]["label"] == CONNECTION["not_configured"][0]
    finally:
        sc.shutdown(); sc.server_close()
