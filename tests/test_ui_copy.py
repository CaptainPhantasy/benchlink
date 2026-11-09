"""PR1: readable status pills and error/result cards (Amir polish §4.4)."""
from __future__ import annotations

import json
import threading
import urllib.request
from dataclasses import replace

from benchlink_sidecar.server import serve
from benchlink_sidecar.ui.copy import (
    ERROR_CARDS,
    STATUS,
    error_card,
    result_card,
    status_token,
)
from benchlink_testkit import Harness


def test_status_labels_match_wireframe_vocabulary():
    assert {k: v.label for k, v in STATUS.items()} == {
        "idle": "Idle",
        "queued": "Queued",
        "running": "Running",
        "result": "Result",
        "error": "Error",
    }
    for key in ("idle", "queued", "running", "result", "error"):
        tok = status_token(key)
        assert tok["label"] == STATUS[key].label
        assert "status=" not in tok["label"]
        assert "phase=" not in tok["label"]


def test_error_card_maps_every_frozen_class_without_raw_json_or_http_fallback():
    for code in (
        "auth.expired", "auth.unpaired", "auth.denied", "offline", "timeout",
        "routing", "validation", "session.not_found", "task.not_found", "internal",
    ):
        card = error_card(code, target_name="Ethan · Product", device_id="mac-mini",
                          correlation_id="corr_abc", http=502)
        assert card["title"]
        assert card["body"]
        assert "HTTP " not in card["title"]
        assert "HTTP " not in card["body"]
        assert "{" not in card["title"] and "{" not in card["body"]
        assert card["details_code"] == code
        assert card["correlation_id"] == "corr_abc"
    # Unknown / bare HTTP leftovers → internal human copy, never "HTTP 502"
    mystery = error_card(None, http=502)
    assert mystery["title"] == "Something went wrong on the bridge"
    assert "HTTP" not in mystery["title"]


def test_offline_preview_variant_and_name_substitution():
    preview = error_card("offline", preview=True)
    assert preview["title"] == "Preview mode"
    assert "isn't set" in preview["body"]
    denied = error_card("auth.denied", device_id="mac-mini-01")
    assert "mac-mini-01" in denied["body"]
    timeout = error_card("timeout", target_name="floyd")
    assert "Floyd" in timeout["title"]


def test_result_card_summary_and_links_no_raw_dump():
    card = result_card({
        "summary": "Staff ack from Floyd",
        "task_id": "task_ab12",
        "session_id": "sess_1",
        "artifact_links": ["https://example.test/a/very/long/path/to/artifact.pdf"],
    }, target_name="Floyd · General Manager")
    assert card["summary"] == "Staff ack from Floyd"
    assert card["has_links"] is True
    assert card["artifact_links"][0]["url"].startswith("https://")
    assert "…" in card["artifact_links"][0]["label"] or len(card["artifact_links"][0]["label"]) <= 48
    assert "task_ab12" in card["meta"]
    empty = result_card({"summary": "ok", "artifact_links": []})
    assert empty["empty_links_note"] == "No links returned"


def test_result_card_prefers_wire_artifact_titles_and_preserves_fallbacks():
    url = "https://example.test/artifact.pdf"
    card = result_card({"artifact_links": [
        {"url": url, "title": "Quarterly report"},
        {"url": url, "title": "Wire title", "label": "Legacy label"},
        {"url": url, "label": "Legacy report"},
        {"url": url},
    ]})
    assert card["artifact_links"] == [
        {"url": url, "label": "Quarterly report"},
        {"url": url, "label": "Wire title"},
        {"url": url, "label": "Legacy report"},
        {"url": url, "label": url},
    ]


def test_error_card_templates_cover_spec_table():
    # Spec §4.4 required rows (plus task_in_flight / offline.preview variants)
    required = {
        "auth.expired", "auth.unpaired", "auth.denied", "offline", "timeout",
        "routing", "validation", "session.not_found", "task.not_found", "internal",
        "task_in_flight", "offline.preview",
    }
    assert required <= set(ERROR_CARDS)


def test_html_shell_has_status_pill_and_cards_not_debug_dump():
    with Harness() as h:
        sc = serve(replace(h.sidecar_cfg, listen_port=0))
        threading.Thread(target=sc.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{sc.server_address[1]}"
            with urllib.request.urlopen(base + "/", timeout=3) as r:
                html = r.read().decode()
            assert "Bridge Control" in html
            assert 'id="statusPill"' in html
            assert 'id="resultCard"' in html
            assert 'id="errorCard"' in html
            # No debug status dump template (old: status= + phase= + err)
            assert "status=' + snap.wait_status" not in html
            assert "phase=' + snap.phase" not in html
            assert "JSON.stringify(snap.result" not in html
            assert "JSON.stringify(snap.error" not in html
            # Copy tables present for client rendering
            assert "Idle" in html and "Queued" in html and "Running" in html
        finally:
            sc.shutdown()
            sc.server_close()


def test_ux_run_snapshot_includes_presentation_cards():
    with Harness() as h:
        sc = serve(replace(h.sidecar_cfg, listen_port=0))
        threading.Thread(target=sc.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        base = f"http://127.0.0.1:{sc.server_address[1]}"

        def post(path, body=None):
            req = urllib.request.Request(
                base + path, data=json.dumps(body or {}).encode(), method="POST",
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read())

        try:
            out = post("/_sidecar/ux/run", {"target": "ethan", "goal": "HTML send"})
            snap = out["snapshot"]
            assert "presentation" in snap
            assert snap["presentation"]["status"]["label"] in {"Queued", "Running", "Result", "Error", "Idle"}
            # Poll until done
            for _ in range(50):
                if snap["phase"] != "wait":
                    break
                import time
                time.sleep(0.05)
                snap = post("/_sidecar/ux/poll")
            assert snap["phase"] == "done"
            assert snap["presentation"]["status"]["label"] == "Result"
            assert snap["presentation"]["result"]["summary"]
            assert "status=" not in json.dumps(snap["presentation"])
        finally:
            sc.shutdown()
            sc.server_close()
