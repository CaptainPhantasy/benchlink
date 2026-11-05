"""ResultSource — how a staff bot's `task.result` gets back to the bridge.

Interface (all implementations interchangeable; daemon only talks to this):
  kind                      'drive_folder' | 'local_folder' | 'http_callback'
  check_ready()             raise BridgeError('offline') if misconfigured (fail closed)
  delivery(task) -> dict    non-secret* instructions embedded in the dispatch payload
                            telling the staff bot where/how to write its result
  fetch(task_id) -> dict|None   raw task.result JSON once present, else None
  simulate_bot_write(task_id, payload)   test/fake-transport hook ("be the bot")
  describe() -> dict        redacted diagnostics
(* http_callback includes a short-lived per-task token — the only credential
   that ever leaves the bridge, scoped to one task's result.)

Default (GM decision 2026-11-05): drive_folder, read via a dedicated Drive service
account (BENCHLINK_DRIVE_SA_KEY_PATH, drive.readonly; Claire 2026-11-05). Staff bot writes ONE JSON file
per task named `<task_id>.json` (frozen task_result schema) into the folder
BENCHLINK_RESULTS_DRIVE_FOLDER_ID. local_folder mimics that layout on disk.
http_callback is kept behind the same interface so it can replace Drive later.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

from .errors import BridgeError
from .http_transport import open_no_redirects

RESULT_TOKEN_HEADER = "X-Benchlink-Result-Token"
CALLBACK_PATH_PREFIX = "/_bridge/results/"   # non-contract (OpenAPI is frozen)


def result_file_name(task_id: str) -> str:
    if not task_id or "/" in task_id or "\\" in task_id or task_id in {".", ".."}:
        raise BridgeError("validation", f"task_id {task_id!r} not usable as a result file name")
    return f"{task_id}.json"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(raw: bytes | str, where: str) -> dict | None:
    try:
        data = json.loads(raw)
    except ValueError:
        return None  # partially written; try again next poll
    if not isinstance(data, dict):
        raise BridgeError("validation", f"{where} is not a JSON object")
    return data


class ResultSource(ABC):
    kind: str = "abstract"

    def check_ready(self) -> None:  # pragma: no cover - default ok
        return None

    @abstractmethod
    def delivery(self, task: dict) -> dict: ...

    @abstractmethod
    def fetch(self, task_id: str) -> dict | None: ...

    @abstractmethod
    def simulate_bot_write(self, task_id: str, payload: dict) -> None: ...

    def describe(self) -> dict:
        return {"kind": self.kind}


# --------------------------------------------------------------------------- local
class LocalFolderResultSource(ResultSource):
    """Directory on disk laid out like the Drive results folder."""
    kind = "local_folder"

    def __init__(self, directory: str | None):
        self.directory = Path(directory) if directory else None

    def check_ready(self):
        if not self.directory:
            raise BridgeError("offline", "BENCHLINK_RESULTS_LOCAL_DIR is unset (fail closed)")
        if not self.directory.is_dir():
            raise BridgeError("offline", f"results dir {self.directory} does not exist")

    def _path(self, task_id: str) -> Path:
        self.check_ready()
        p = (self.directory / result_file_name(task_id)).resolve()
        if p.parent != self.directory.resolve():
            raise BridgeError("validation", "result path escapes results dir")
        return p

    def delivery(self, task):
        return {"mode": "local_folder", "folder": str(self.directory),
                "file_name": result_file_name(task["task_id"])}

    def fetch(self, task_id):
        p = self._path(task_id)
        if not p.is_file():
            return None
        return _parse(p.read_bytes(), p.name)

    def simulate_bot_write(self, task_id, payload):
        p = self._path(task_id)
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp_", suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f)
        os.replace(tmp, p)

    def describe(self):
        return {"kind": self.kind, "dir": str(self.directory) if self.directory else None}


# --------------------------------------------------------------------------- drive
class DriveClient(ABC):
    """Minimal Drive surface the bridge needs (read-only in prod)."""

    @abstractmethod
    def find_file(self, folder_id: str, name: str) -> str | None: ...

    @abstractmethod
    def download(self, file_id: str) -> bytes: ...

    def upload(self, folder_id: str, name: str, data: bytes) -> str:  # fake/test only
        raise NotImplementedError("bridge never writes to Drive in prod")


class InMemoryDriveClient(DriveClient):
    """Fake Drive for tests/smoke — no network."""

    def __init__(self):
        self._files: dict[str, tuple[str, str, bytes]] = {}
        self._lock = threading.Lock()
        self.calls: list[tuple] = []

    def find_file(self, folder_id, name):
        self.calls.append(("find", folder_id, name))
        with self._lock:
            for fid, (fol, n, _) in self._files.items():
                if fol == folder_id and n == name:
                    return fid
        return None

    def download(self, file_id):
        self.calls.append(("download", file_id))
        return self._files[file_id][2]

    def upload(self, folder_id, name, data):
        with self._lock:
            fid = "fake_" + secrets.token_hex(6)
            self._files[fid] = (folder_id, name, data)
            return fid


DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"


def _b64url(b: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _default_http_post(url: str, form: dict, timeout: float) -> dict:
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with open_no_redirects(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        if e.code in (400, 401, 403):
            raise BridgeError("auth.denied", f"Google token endpoint refused service account ({e.code})") from None
        raise BridgeError("offline", f"Google token endpoint HTTP {e.code}") from None
    except TimeoutError:
        raise BridgeError("timeout", "Google token endpoint timed out") from None
    except urllib.error.URLError as e:
        raise BridgeError("offline", f"Google token endpoint unreachable: {e.reason}") from None


class ServiceAccountCredentials:
    """Dedicated Drive service account (Claire, 2026-11-05). Minimal JWT-bearer flow.

    Key file: BENCHLINK_DRIVE_SA_KEY_PATH (Google SA JSON, on inference, outside the repo,
    mode 0600 or stricter). Contents are never logged. Scope: drive.readonly.
    Access tokens are minted on demand and refreshed ~60 s before expiry.
    """

    def __init__(self, key_path: str | None, scope: str = DRIVE_READONLY_SCOPE,
                 http_post=None, clock=time.time, timeout: float = 10.0):
        self.key_path = Path(key_path).expanduser() if key_path else None
        self.scope = scope
        self.http_post = http_post or _default_http_post
        self.clock = clock
        self.timeout = timeout
        self._token: str | None = None
        self._exp = 0.0
        self._lock = threading.Lock()
        self.mint_count = 0

    @staticmethod
    def check_key_file(path) -> None:
        """Raise BridgeError('offline') unless the key file exists with mode <= 0600."""
        if not path:
            raise BridgeError("offline", "BENCHLINK_DRIVE_SA_KEY_PATH is unset (fail closed)")
        p = Path(path).expanduser()
        if not p.is_file():
            raise BridgeError("offline", "BENCHLINK_DRIVE_SA_KEY_PATH does not point at a file (fail closed)")
        mode = p.stat().st_mode & 0o777
        if mode & 0o077:
            raise BridgeError("offline", f"BENCHLINK_DRIVE_SA_KEY_PATH mode {oct(mode)} is looser than 0600; "
                                         "chmod 600 it (fail closed)")

    def _load(self) -> dict:
        self.check_key_file(self.key_path)
        try:
            info = json.loads(self.key_path.read_text())
        except ValueError:
            raise BridgeError("offline", "service account key file is not valid JSON") from None
        for k in ("client_email", "private_key"):
            if not info.get(k):
                raise BridgeError("offline", f"service account key file lacks {k!r}")
        if info.get("token_uri", GOOGLE_TOKEN_URI) != GOOGLE_TOKEN_URI:
            raise BridgeError("offline", "service account token_uri is not Google's token endpoint")
        return info

    def _assertion(self, info: dict, now: float) -> str:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        header = {"alg": "RS256", "typ": "JWT"}
        if info.get("private_key_id"):
            header["kid"] = info["private_key_id"]
        claims = {"iss": info["client_email"], "scope": self.scope, "aud": GOOGLE_TOKEN_URI,
                  "iat": int(now), "exp": int(now) + 3600}
        signing = (_b64url(json.dumps(header, separators=(",", ":")).encode()) + "." +
                   _b64url(json.dumps(claims, separators=(",", ":")).encode()))
        key = serialization.load_pem_private_key(info["private_key"].encode(), password=None)
        sig = key.sign(signing.encode(), padding.PKCS1v15(), hashes.SHA256())
        return signing + "." + _b64url(sig)

    def token(self) -> str:
        with self._lock:
            now = self.clock()
            if self._token and now < self._exp - 60:
                return self._token
            info = self._load()
            resp = self.http_post(GOOGLE_TOKEN_URI, {
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": self._assertion(info, now)}, self.timeout)
            tok = resp.get("access_token")
            if not tok:
                raise BridgeError("auth.denied", "Google token endpoint returned no access_token")
            self._token = tok
            self._exp = now + float(resp.get("expires_in", 3600))
            self.mint_count += 1
            return tok


class GoogleDriveRestClient(DriveClient):
    """Drive v3 REST, read-only, authenticated by the dedicated service account.

    Never exercised against real Drive by Computer tests/smoke.
    """
    API = "https://www.googleapis.com/drive/v3/files"

    def __init__(self, credentials: ServiceAccountCredentials, timeout: float = 10.0, opener=None):
        self.credentials = credentials
        self.timeout = timeout
        self.opener = opener or urllib.request.urlopen

    def _get(self, url: str) -> bytes:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.credentials.token()}"})
        try:
            with self.opener(req, timeout=self.timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise BridgeError("auth.denied", f"Drive refused service account ({e.code})") from None
            raise BridgeError("offline", f"Drive HTTP {e.code}") from None
        except TimeoutError:
            raise BridgeError("timeout", "Drive request timed out") from None
        except urllib.error.URLError as e:
            raise BridgeError("offline", f"Drive unreachable: {e.reason}") from None

    def find_file(self, folder_id, name):
        q = f"'{folder_id}' in parents and name = '{name}' and trashed = false"
        qs = urllib.parse.urlencode({"q": q, "fields": "files(id,name,modifiedTime)",
                                     "orderBy": "modifiedTime desc", "pageSize": "1",
                                     "supportsAllDrives": "true",
                                     "includeItemsFromAllDrives": "true"})
        data = json.loads(self._get(f"{self.API}?{qs}") or b"{}")
        files = data.get("files") or []
        return files[0]["id"] if files else None

    def download(self, file_id):
        return self._get(f"{self.API}/{urllib.parse.quote(file_id)}?alt=media&supportsAllDrives=true")


class DriveFolderResultSource(ResultSource):
    kind = "drive_folder"

    def __init__(self, folder_id: str | None, client: DriveClient):
        self.folder_id = folder_id
        self.client = client

    def check_ready(self):
        if not self.folder_id:
            raise BridgeError("offline", "BENCHLINK_RESULTS_DRIVE_FOLDER_ID is unset (fail closed)")
        if isinstance(self.client, GoogleDriveRestClient):
            ServiceAccountCredentials.check_key_file(self.client.credentials.key_path)

    def delivery(self, task):
        return {"mode": "drive_folder", "drive_folder_id": self.folder_id,
                "file_name": result_file_name(task["task_id"])}

    def fetch(self, task_id):
        self.check_ready()
        name = result_file_name(task_id)
        fid = self.client.find_file(self.folder_id, name)
        if not fid:
            return None
        return _parse(self.client.download(fid), name)

    def simulate_bot_write(self, task_id, payload):
        self.client.upload(self.folder_id, result_file_name(task_id), json.dumps(payload).encode())

    def describe(self):
        return {"kind": self.kind, "folder_id_set": bool(self.folder_id),
                "client": type(self.client).__name__}


# --------------------------------------------------------------------------- callback
class HttpCallbackResultSource(ResultSource):
    """Staff bot POSTs task.result to the daemon, gated by a short-lived per-task token."""
    kind = "http_callback"

    def __init__(self, base_url: str | None, token_ttl_s: float = 900.0):
        self.base_url = base_url.rstrip("/") if base_url else None
        self.token_ttl_s = token_ttl_s
        self._tokens: dict[str, tuple[str, float]] = {}
        self._inbox: dict[str, dict] = {}
        self._lock = threading.Lock()

    def check_ready(self):
        if not self.base_url:
            raise BridgeError("offline", "callback base URL unknown (BENCHLINK_CALLBACK_BASE_URL unset)")

    def callback_url(self, task_id):
        return self.base_url + CALLBACK_PATH_PREFIX + urllib.parse.quote(task_id, safe="")

    def delivery(self, task):
        self.check_ready()
        tid = task["task_id"]
        tok = secrets.token_urlsafe(32)
        exp = time.time() + self.token_ttl_s
        with self._lock:
            self._tokens[tid] = (tok, exp)
        return {"mode": "http_callback", "url": self.callback_url(tid),
                "token_header": RESULT_TOKEN_HEADER, "token": tok, "expires_at": _iso(exp)}

    def accept(self, task_id: str, token: str | None, payload: dict) -> None:
        """Called by the daemon endpoint (payload already schema-validated)."""
        if not token:
            raise BridgeError("auth.unpaired", f"missing {RESULT_TOKEN_HEADER}")
        # Lookup, validation, final consume and publication are one transaction.
        # Otherwise concurrent callbacks can both authorize a single-use token.
        with self._lock:
            entry = self._tokens.get(task_id)
            if not entry or not hmac.compare_digest(entry[0].encode(), token.encode("utf-8", "replace")):
                raise BridgeError("auth.denied", "result token not valid for this task")
            if time.time() > entry[1]:
                raise BridgeError("auth.expired", "result token expired")
            self._inbox[task_id] = payload
            if payload.get("status") in {"succeeded", "failed"}:
                self._tokens.pop(task_id, None)  # single use for final results

    def expire_token_for_test(self, task_id):
        with self._lock:
            tok, _ = self._tokens[task_id]
            self._tokens[task_id] = (tok, time.time() - 1)

    def fetch(self, task_id):
        with self._lock:
            return self._inbox.get(task_id)

    def simulate_bot_write(self, task_id, payload):
        raise NotImplementedError("http_callback is simulated over HTTP by the fake transport")

    def describe(self):
        return {"kind": self.kind, "base_url": self.base_url, "pending_tokens": len(self._tokens)}


def post_callback(delivery: dict, payload: dict, timeout: float = 5.0) -> int:
    """What a staff bot does in http_callback mode (used by fake transport/tests)."""
    req = urllib.request.Request(
        delivery["url"], data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", delivery["token_header"]: delivery["token"]})
    try:
        with open_no_redirects(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def build_result_source(cfg, drive_client: DriveClient | None = None) -> ResultSource:
    """drive_folder with the real client refuses to start unless the SA key file is present
    with mode 0600 or stricter (raises AdapterConfigError). Injected fake clients skip that."""
    if cfg.result_source == "local_folder":
        return LocalFolderResultSource(cfg.local_dir)
    if cfg.result_source == "http_callback":
        return HttpCallbackResultSource(cfg.callback_base_url, cfg.callback_token_ttl_s)
    if drive_client is None:
        from .config import AdapterConfigError
        try:
            ServiceAccountCredentials.check_key_file(cfg.drive_sa_key_path)
        except BridgeError as e:
            raise AdapterConfigError(f"drive_folder result source refused to start: {e.message}") from None
        drive_client = GoogleDriveRestClient(ServiceAccountCredentials(cfg.drive_sa_key_path))
    return DriveFolderResultSource(cfg.drive_folder_id, drive_client)
