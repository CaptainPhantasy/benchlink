"""Dedicated Drive service account (BENCHLINK_DRIVE_SA_KEY_PATH): mocked credential, no real Google calls."""
import base64
import io
import json
import os

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from benchlink_adapter.config import AdapterConfigError, load_adapter_config
from benchlink_adapter.errors import BridgeError
from benchlink_adapter.results import (DRIVE_READONLY_SCOPE, GOOGLE_TOKEN_URI, DriveFolderResultSource,
                                       GoogleDriveRestClient, ServiceAccountCredentials, build_result_source)


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


@pytest.fixture
def sa(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    p = tmp_path / "sa.json"
    p.write_text(json.dumps({"type": "service_account", "client_email": "bridge@test.iam.gserviceaccount.com",
                             "private_key": pem, "private_key_id": "kid1", "token_uri": GOOGLE_TOKEN_URI}))
    os.chmod(p, 0o600)
    return p, key


def test_drive_folder_refuses_to_start_without_key():
    with pytest.raises(AdapterConfigError, match="BENCHLINK_DRIVE_SA_KEY_PATH"):
        build_result_source(load_adapter_config({"BENCHLINK_RESULTS_DRIVE_FOLDER_ID": "f"}))


def test_drive_folder_refuses_missing_file_and_loose_mode(sa, tmp_path):
    with pytest.raises(AdapterConfigError):
        build_result_source(load_adapter_config({"BENCHLINK_DRIVE_SA_KEY_PATH": str(tmp_path / "nope.json")}))
    p, _ = sa
    os.chmod(p, 0o640)
    with pytest.raises(AdapterConfigError, match="looser than 0600"):
        build_result_source(load_adapter_config({"BENCHLINK_DRIVE_SA_KEY_PATH": str(p)}))
    os.chmod(p, 0o400)
    src = build_result_source(load_adapter_config({"BENCHLINK_DRIVE_SA_KEY_PATH": str(p),
                                                   "BENCHLINK_RESULTS_DRIVE_FOLDER_ID": "f"}))
    assert isinstance(src, DriveFolderResultSource) and isinstance(src.client, GoogleDriveRestClient)


def test_jwt_bearer_mint_and_refresh(sa):
    p, key = sa
    now = [1_000_000.0]
    calls = []

    def fake_post(url, form, timeout):
        calls.append((url, form))
        return {"access_token": f"ya29.fake-{len(calls)}", "expires_in": 3600}

    creds = ServiceAccountCredentials(str(p), http_post=fake_post, clock=lambda: now[0])
    assert creds.token() == "ya29.fake-1"
    assert creds.token() == "ya29.fake-1" and creds.mint_count == 1  # cached
    url, form = calls[0]
    assert url == GOOGLE_TOKEN_URI and form["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
    h, c, sig = form["assertion"].split(".")
    header, claims = json.loads(_unb64(h)), json.loads(_unb64(c))
    assert header == {"alg": "RS256", "typ": "JWT", "kid": "kid1"}
    assert claims["scope"] == DRIVE_READONLY_SCOPE and claims["aud"] == GOOGLE_TOKEN_URI
    assert claims["iss"] == "bridge@test.iam.gserviceaccount.com" and claims["exp"] - claims["iat"] == 3600
    key.public_key().verify(_unb64(sig), f"{h}.{c}".encode(), padding.PKCS1v15(), hashes.SHA256())
    now[0] += 3600 - 30  # inside the 60 s refresh window
    assert creds.token() == "ya29.fake-2" and creds.mint_count == 2


def test_rejects_foreign_token_uri(sa):
    p, _ = sa
    info = json.loads(p.read_text())
    info["token_uri"] = "https://evil.example/token"
    p.write_text(json.dumps(info))
    os.chmod(p, 0o600)
    with pytest.raises(BridgeError, match="token_uri"):
        ServiceAccountCredentials(str(p), http_post=lambda *a: {}).token()


def test_drive_rest_client_uses_minted_bearer_and_finds_result(sa):
    p, _ = sa
    creds = ServiceAccountCredentials(str(p), http_post=lambda *a: {"access_token": "ya29.mock", "expires_in": 3600})
    seen = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout):
        seen.append((req.full_url, req.headers.get("Authorization")))
        if "alt=media" in req.full_url:
            return Resp(b'{"v":"1"}')
        return Resp(json.dumps({"files": [{"id": "fid1", "name": "task_1.json"}]}).encode())

    src = DriveFolderResultSource("folder_x", GoogleDriveRestClient(creds, opener=opener))
    src.check_ready()
    assert src.fetch("task_1") == {"v": "1"}
    assert all(auth == "Bearer ya29.mock" for _, auth in seen)
    assert "%27folder_x%27+in+parents" in seen[0][0] and "task_1.json" in seen[0][0]


def test_key_contents_never_in_describe_or_errors(sa):
    from benchlink_adapter.config import describe_adapter
    p, _ = sa
    d = json.dumps(describe_adapter(load_adapter_config({"BENCHLINK_DRIVE_SA_KEY_PATH": str(p)})))
    assert "PRIVATE KEY" not in d and str(p) not in d
