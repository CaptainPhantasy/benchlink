"""Checklist B1–B4: explicit, frozen error classes end to end (daemon + sidecar)."""
import socket
import time

import pytest
from benchlink_testkit import Harness, valid

from benchlink_sidecar.config import load_config as sidecar_config
from benchlink_sidecar.ux import BridgeControlShell


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ---------------------------------------------------------------- B1 auth
def test_b1_sidecar_without_token_or_key_is_auth_unpaired():
    with Harness() as h:
        cfg = sidecar_config({"BENCHLINK_BRIDGE_BASE_URL": h.base,
                              "BENCHLINK_DEVICE_KEY_PATH": str(h.dir / "nope.pem")})
        snap = BridgeControlShell(cfg).start_and_wait("x")["snapshot"]
        assert snap["phase"] == "error" and snap["error"]["code"] == "auth.unpaired"


def test_b1_unenrolled_device_surfaces_auth_denied_in_sidecar():
    with Harness(enroll=False) as h:
        snap = BridgeControlShell(h.sidecar_cfg, client=h.client).start_and_wait("x")["snapshot"]
        assert snap["error"]["code"] == "auth.denied"


def test_b1_staff_webhook_rejects_sender_key():
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE": "reject"}) as h:
        snap = BridgeControlShell(h.sidecar_cfg, client=h.client).start_and_wait("x")["snapshot"]
        assert snap["error"]["code"] == "auth.denied" and snap["error"]["http"] == 403


# ---------------------------------------------------------------- B2 offline
def test_b2_bridge_url_unset_fails_closed():
    shell = BridgeControlShell(sidecar_config({}))
    assert shell.dry_run_mode and shell.offline_banner  # existing fail-closed behaviour kept


def test_b2_bridge_down_is_offline():
    cfg = sidecar_config({"BENCHLINK_BRIDGE_BASE_URL": f"http://127.0.0.1:{_free_port()}",
                          "BENCHLINK_ACCESS_TOKEN": "tok_unused_0123456789"})
    snap = BridgeControlShell(cfg).start_and_wait("x")["snapshot"]
    assert snap["phase"] == "error" and snap["error"]["code"] == "offline"


def test_b2_target_webhook_unset_is_offline():
    with Harness(adapter_env={"BENCHLINK_ADAPTER_TRANSPORT": "webhook"}) as h:
        tok = h.token()
        _, s, _ = h.session(tok)
        st, err, _ = h.task(tok, s["session_id"], "task_b2")
        assert st == 503 and err["code"] == "offline" and err["retryable"] is True
        assert "BENCHLINK_TARGET_FLOYD_WEBHOOK_URL" in err["message"] and not valid("error", err)


def test_b2_webhook_upstream_down_is_offline():
    env = {"BENCHLINK_ADAPTER_TRANSPORT": "webhook",
           "BENCHLINK_TARGET_FLOYD_WEBHOOK_URL": f"http://127.0.0.1:{_free_port()}/hook",
           "BENCHLINK_TARGET_FLOYD_SENDER_KEY": "test-sender-key"}
    with Harness(adapter_env=env) as h:
        snap = BridgeControlShell(h.sidecar_cfg, client=h.client).start_and_wait("x")["snapshot"]
        assert snap["error"]["code"] == "offline" and snap["error"]["http"] == 503


def test_b2_fake_offline_mode():
    with Harness(adapter_env={"BENCHLINK_FAKE_TRANSPORT_MODE": "offline"}) as h:
        tok = h.token()
        _, s, _ = h.session(tok)
        st, err, _ = h.task(tok, s["session_id"], "task_b2f")
        assert st == 503 and err["code"] == "offline"


# ---------------------------------------------------------------- B3 routing
def test_b3_unknown_target_routing_error():
    with Harness() as h:
        tok = h.token()
        st, err, _ = h.session(tok, "zed-not-a-bot")
        assert st == 422 and err["code"] == "routing" and not valid("error", err)
        c = h.client
        st, body = c.send(c.build_session_create("00000000-dead-beef-0000-000000000000"))
        assert st == 422 and body["code"] == "routing"


def test_b3_task_target_must_match_session():
    with Harness() as h:
        tok = h.token()
        _, s, _ = h.session(tok, "floyd")
        st, err, _ = h.task(tok, s["session_id"], "task_b3", target="ethan")
        assert st == 422 and err["code"] == "routing"
        st, err, _ = h.task(tok, s["session_id"], "task_b3b", target="nobody")
        assert st == 422 and err["code"] == "routing"


def test_b3_sidecar_roster_rejects_unknown_key():
    with Harness() as h:
        with pytest.raises(ValueError):
            BridgeControlShell(h.sidecar_cfg, client=h.client).pick("nobody")


# ---------------------------------------------------------------- B4 timeout
def test_b4_task_timeout_configurable():
    env = {"BENCHLINK_FAKE_TRANSPORT_MODE": "silent", "BENCHLINK_TASK_TIMEOUT_S": "0.3"}
    with Harness(adapter_env=env) as h:
        shell = BridgeControlShell(h.sidecar_cfg, client=h.client, poll_sleep=0.1, max_wait_ticks=30)
        t0 = time.monotonic()
        snap = shell.start_and_wait("never answered")["snapshot"]
        assert snap["phase"] == "error" and snap["error"]["code"] == "timeout"
        assert snap["result"]["error_class"] == "timeout" and not valid("task.result", snap["result"])
        assert time.monotonic() - t0 < 5


def test_b4_webhook_dispatch_timeout():
    from test_webhook import Receiver
    with Receiver(delay=1.0) as rx:
        env = {"BENCHLINK_ADAPTER_TRANSPORT": "webhook", "BENCHLINK_TARGET_FLOYD_WEBHOOK_URL": rx.url,
               "BENCHLINK_TARGET_FLOYD_SENDER_KEY": "k", "BENCHLINK_WEBHOOK_TIMEOUT_S": "0.2"}
        with Harness(adapter_env=env) as h:
            tok = h.token()
            _, s, _ = h.session(tok)
            st, err, _ = h.task(tok, s["session_id"], "task_b4w")
            assert st == 504 and err["code"] == "timeout"
