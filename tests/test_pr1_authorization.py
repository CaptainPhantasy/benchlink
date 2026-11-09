"""Explicit enrollment restrictions must fail closed, never become ALL_SCOPES."""
import json

import pytest
from benchlink_testkit import DEVICE_ID, Harness, valid


@pytest.mark.parametrize("configured", [[], None, False, "task.create", [["task.create"]]])
def test_explicit_empty_or_invalid_scope_allowlist_never_issues_token(configured):
    with Harness() as h:
        entries = json.loads(h.allowlist.read_text())
        entries[DEVICE_ID]["scopes"] = configured
        h.allowlist.write_text(json.dumps(entries))
        status, error, _ = h.pair_raw()
        assert status == 403
        assert error["code"] == "auth.denied" and not valid("error", error)
        assert not h.state.auth._tokens


def test_omitted_scope_allowlist_retains_documented_defaults():
    with Harness() as h:
        token = h.token()
        assert h.session(token)[0] == 201
