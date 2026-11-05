"""Runtime bearer boundaries and executable enrollment documentation (loopback only)."""
import json
import re
from pathlib import Path

from benchlink_testkit import DEVICE_ID, Harness, valid

ROOT = Path(__file__).resolve().parents[1]


def test_pairing_runbook_enrollment_includes_required_tailnet_identity():
    text = (ROOT / "docs/PAIRING_PROOF_RUNBOOK.md").read_text()
    examples = [json.loads(block) for block in re.findall(r"```json\n(.*?)```", text, re.S)]
    entries = [entry for example in examples for entry in example.values() if isinstance(entry, dict)]
    assert any(entry.get("public_key_b64url") and entry.get("tailscale_identity") for entry in entries)


def test_pair_is_bearer_free_but_identity_bound_and_health_is_optional_bearer():
    login = "fixture-owner@example.invalid"
    with Harness(daemon_env={"BENCHLINK_REQUIRE_TAILSCALE_IDENTITY":"1"}, ts_identity=login) as h:
        status, health, _ = h.http("GET", "/v1/health")
        assert status == 200 and health["auth"] == "unpaired" and not valid("health.pong", health)
        assert h.pair_raw()[0] == 403
        assert h.pair_raw(ts_login="different-fixture@example.invalid")[0] == 403
        status, token, _ = h.pair_raw(ts_login=login)
        assert status == 200 and not valid("auth.session.token", token)
        headers = {"Authorization":"Bearer " + token["access_token"], "Tailscale-User-Login":login}
        status, health, _ = h.http("GET", "/v1/health", headers=headers)
        assert status == 200 and health["auth"] == "paired"
        status, health, _ = h.http("GET", "/v1/health", headers={"Authorization":"Bearer fake-test-invalid"})
        assert status == 200 and health["auth"] == "unpaired"
        status, error, _ = h.http("POST", "/v1/sessions", {"v":"1", "type":"session.create"})
        assert status == 401 and error["code"] == "auth.unpaired"


def test_auth_errata_explicitly_flags_frozen_openapi_security_mismatch():
    text = (ROOT / "docs/PROTOCOL_AUTH_ERRATA.md").read_text()
    assert "/auth/pair" in text and "/health" in text
    assert "security: []" in text and "frozen" in text and "approval" in text
