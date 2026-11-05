"""D3 auth outcomes (Claire spec) — every path explicit, frozen error envelope."""
import logging
import time

import pytest
from benchlink_testkit import DEVICE_ID, Harness, valid

from benchlink_daemon.config import load_config

ALL = ["session.create", "session.message", "session.close", "task.create"]


@pytest.fixture
def h():
    with Harness() as hh:
        yield hh


def bearer(t):
    return {"Authorization": f"Bearer {t}"}


def test_pair_happy_path_schema_and_rfc3339(h):
    st, tok, ch = h.pair_raw()
    assert st == 200
    assert not valid("auth.pair.challenge", ch) and ch["expires_at"].endswith("Z")
    assert not valid("auth.session.token", tok)
    assert tok["token_type"] == "short_lived" and "refresh_token" not in tok
    assert tok["expires_at"].endswith("Z") and len(tok["access_token"]) >= 40


def test_unknown_device_denied():
    with Harness(enroll=False) as h:
        st, body, _ = h.pair_raw()
        assert st == 403 and body["code"] == "auth.denied" and not valid("error", body)


def test_bad_signature_denied(h):
    st, body, _ = h.pair_raw(sign=False)
    assert st == 403 and body["code"] == "auth.denied"


def test_replayed_challenge_denied(h):
    from benchlink_sidecar.pairing import b64url
    st, ch, _ = h.http("POST", "/v1/auth/pair", {"v": "1", "type": "auth.pair.request", "device_id": DEVICE_ID,
                                                 "client": "grok.app", "client_version": "t", "nonce": "n_123456789"})
    body = {"v": "1", "type": "auth.pair.complete", "device_id": DEVICE_ID,
            "proof": b64url(h.key.sign(f"{ch['challenge']}.{ch['nonce']}".encode())), "scopes": ALL}
    assert h.http("POST", "/v1/auth/pair", body)[0] == 200
    st, err, _ = h.http("POST", "/v1/auth/pair", body)
    assert st == 403 and err["code"] == "auth.denied"


def test_expired_challenge():
    now = [1_000_000.0]
    with Harness(auth_clock=lambda: now[0]) as h:
        from benchlink_sidecar.pairing import b64url
        st, ch, _ = h.http("POST", "/v1/auth/pair", {"v": "1", "type": "auth.pair.request", "device_id": DEVICE_ID,
                                                     "client": "grok.app", "client_version": "t",
                                                     "nonce": "n_123456789"})
        now[0] += 61
        st, err, _ = h.http("POST", "/v1/auth/pair", {
            "v": "1", "type": "auth.pair.complete", "device_id": DEVICE_ID,
            "proof": b64url(h.key.sign(f"{ch['challenge']}.{ch['nonce']}".encode())), "scopes": ALL})
        assert st == 401 and err["code"] == "auth.expired"


def test_pair_rejects_non_contract_scopes(h):
    # Claire's pairing-proof.sh sends scopes ["session","task"]; frozen enum rejects it
    st, err, _ = h.pair_raw(scopes=["session", "task"])
    assert st == 422 and err["code"] == "validation"


def test_unknown_pair_type(h):
    st, err, _ = h.http("POST", "/v1/auth/pair", {"v": "1", "type": "health.ping"})
    assert st == 422 and err["code"] == "validation"


def test_scope_denial_at_pair_and_at_use():
    with Harness(scopes=["task.create"]) as h:
        st, err, _ = h.pair_raw(scopes=["session.create"])
        assert st == 403 and err["code"] == "auth.denied"
        tok = h.token()  # granted = requested ∩ allowed = task.create only
        st, err, _ = h.session(tok)
        assert st == 403 and err["code"] == "auth.denied" and "scope" in err["message"]


def test_missing_bogus_query_and_mismatched_tokens(h):
    tok = h.token()
    body = {"v": "1", "type": "session.create", "access_token": tok, "intent": "staff_task", "target_agent": "floyd"}
    st, err, _ = h.http("POST", "/v1/sessions", body)
    assert st == 401 and err["code"] == "auth.unpaired"
    st, err, _ = h.http("POST", "/v1/sessions", body, bearer("bogus-token-000000000000"))
    assert st == 401 and err["code"] == "auth.unpaired"
    st, err, _ = h.http("POST", f"/v1/sessions?access_token={tok}", body, bearer(tok))
    assert st == 403 and err["code"] == "auth.denied"
    other = h.token()
    st, err, _ = h.http("POST", "/v1/sessions", dict(body, access_token=other), bearer(tok))
    assert st == 403 and err["code"] == "auth.denied"
    no_body_tok = {k: v for k, v in body.items() if k != "access_token"}
    st, err, _ = h.http("POST", "/v1/sessions", no_body_tok, bearer(tok))
    assert st == 403 and err["code"] == "auth.denied"
    st, ok, _ = h.http("POST", "/v1/sessions", body, bearer(tok))
    assert st == 201 and not valid("session.created", ok) and ok["created_at"].endswith("Z")


def test_no_partial_execution_on_auth_failure(h):
    tok = h.token()
    _, s, _ = h.session(tok)
    st, err, _ = h.task("bogus-token-000000000000", s["session_id"], "task_noauth")
    assert st == 401
    assert "task_noauth" not in h.state.tasks and h.adapter.transport.dispatched == []


def test_health_paired_only_with_valid_bearer(h):
    st, body, hdr = h.http("GET", "/v1/health")
    assert body["auth"] == "unpaired" and "X-Benchlink-Tailscale-Identity" not in hdr
    st, body, hdr = h.http("GET", "/v1/health", headers=bearer("bogus-token-000000000000"))
    assert st == 200 and body["auth"] == "unpaired"
    tok = h.token()
    st, body, hdr = h.http("GET", "/v1/health", headers=bearer(tok))
    assert body["auth"] == "paired" and hdr["X-Benchlink-Tailscale-Identity"] == "none"
    assert not valid("health.pong", body)
    tok2 = h.token(ts_login="douglas@example.com")
    _, _, hdr = h.http("GET", "/v1/health", headers=bearer(tok2))
    assert hdr["X-Benchlink-Tailscale-Identity"] == "douglas@example.com"


def test_require_tailscale_identity():
    with Harness(daemon_env={"BENCHLINK_REQUIRE_TAILSCALE_IDENTITY": "1"}, ts_identity="douglas@example.com") as h:
        assert h.pair_raw()[0] == 403
        assert h.pair_raw(ts_login="mallory@example.com")[0] == 403
        st, tok, _ = h.pair_raw(ts_login="douglas@example.com")
        assert st == 200
        t = tok["access_token"]
        body = {"v": "1", "type": "session.create", "access_token": t, "intent": "staff_task", "target_agent": "floyd"}
        st, err, _ = h.http("POST", "/v1/sessions", body, bearer(t))
        assert st == 403 and err["code"] == "auth.denied"
        st, _, _ = h.http("POST", "/v1/sessions", body, dict(bearer(t), **{"Tailscale-User-Login": "douglas@example.com"}))
        assert st == 201


def test_access_ttl_accepts_small_values():
    assert load_config({"BENCHLINK_ACCESS_TTL_SECONDS": "5"}).access_ttl_s == 5.0
    with pytest.raises(Exception):
        load_config({"BENCHLINK_ACCESS_TTL_SECONDS": "0"})


def test_token_expiry_401_then_sidecar_repairs_once():
    with Harness(daemon_env={"BENCHLINK_ACCESS_TTL_SECONDS": "1"}) as h:
        tok = h.token()
        time.sleep(1.2)
        st, err, _ = h.session(tok)
        assert st == 401 and err["code"] == "auth.expired" and not valid("error", err)
        # sidecar client: pairs, token expires, gets auth.expired, re-pairs once, succeeds
        c = h.client
        st, body = c.send(c.build_session_create("floyd"))
        assert st == 201 and c.pair_count == 1
        time.sleep(1.2)
        st, body = c.send(c.build_session_create("ethan"))
        assert st == 201 and c.pair_count == 2


def test_logs_are_redacted(h, caplog):
    caplog.set_level(logging.INFO, logger="benchlink")
    st, tok, ch = h.pair_raw()
    t = tok["access_token"]
    h.session(t)
    text = caplog.text
    assert t not in text and ch["challenge"] not in text
    assert "Bearer" not in text and "proof" not in text.lower().replace("proof signature", "")
    from benchlink_daemon.auth import token_fingerprint
    assert token_fingerprint(t) in text and DEVICE_ID in text and "token_issued" in text


@pytest.mark.parametrize("hdr", ["X-Forwarded-For", "Tailscale-User-Login", "Forwarded"])
def test_daemon_status_refuses_proxied_requests(h, hdr):
    st, body, _ = h.http("GET", "/_daemon/status")
    assert st == 200 and "bridge_status" in body
    st, err, _ = h.http("GET", "/_daemon/status", headers={hdr: "100.64.0.9"})
    assert st == 403 and err["code"] == "auth.denied"
