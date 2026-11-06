"""Keep query-token rejection smoke semantic, synthetic and unambiguous."""
import importlib.util
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from benchlink_testkit import Harness, valid


@pytest.fixture
def smoke():
    path = Path(__file__).resolve().parents[1] / "apps/bridge-daemon/smoke_daemon.py"
    spec = importlib.util.spec_from_file_location("benchlink_smoke_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_query_fixture_is_obviously_fake_and_generated_at_runtime(smoke, monkeypatch):
    monkeypatch.setattr(smoke.os, "getpid", lambda: 12345)
    url = smoke.query_token_fixture_url("http://127.0.0.1:9")
    parts = urlsplit(url)
    assert parts.path == "/v1/tasks/task_x"
    assert parse_qs(parts.query) == {"access_token": ["fixture-not-a-real-token-12345"]}
    monkeypatch.setattr(smoke.os, "getpid", lambda: 54321)
    assert smoke.query_token_fixture_url("http://127.0.0.1:9") != url


def test_daemon_rejects_synthetic_query_token_even_with_valid_header(smoke):
    with Harness() as h:
        token = h.token()
        parts = urlsplit(smoke.query_token_fixture_url(h.base))
        status, error, _ = h.http("GET", parts.path + "?" + parts.query, headers={"Authorization": "Bearer " + token})
        assert status == 403 and error["code"] == "auth.denied"
        assert not valid("error", error)


def test_smoke_really_uses_runtime_query_fixture(smoke, monkeypatch):
    calls = []
    original = smoke.query_token_fixture_url
    def fixture(base):
        calls.append(base)
        return original(base)
    monkeypatch.setattr(smoke, "query_token_fixture_url", fixture)
    assert smoke.main() == 0
    assert len(calls) == 1 and calls[0].startswith("http://127.0.0.1:")
    assert len(smoke.RESULTS) == 14 and all(ok for ok, _ in smoke.RESULTS)
