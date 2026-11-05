"""Same-origin boundary for the non-contract loopback HTTP companion UI."""
from __future__ import annotations

from urllib.parse import urlsplit


def _authority(value: str):
    """Parse only an explicit localhost authority, never DNS or forwarded headers."""
    if not value or any(c.isspace() for c in value) or any(c in value for c in "/?#@,\\"):
        return None
    try:
        parsed = urlsplit("//" + value)
        if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            return None
        return parsed.hostname, parsed.port or 80
    except ValueError:
        return None


def request_denial(headers, port: int, *, mutation: bool = False):
    """Return (HTTP status, safe reason) or None; no upstream or credential access.

    Browser requests must match this listener's Host and Origin. CLI clients may
    omit Origin, but mutations still require JSON. Fetch metadata rejects browser
    cross-site requests even when a browser omits Origin on a read.
    """
    hosts = headers.get_all("Host", [])
    authority = _authority(hosts[0]) if len(hosts) == 1 else None
    if authority is None or authority[1] != port:
        return 403, "Untrusted local request host."
    sites = headers.get_all("Sec-Fetch-Site", [])
    if len(sites) > 1 or (sites and sites[0] not in {"same-origin", "none"}):
        return 403, "Cross-site local requests are not allowed."
    origins = headers.get_all("Origin", [])
    if origins:
        if len(origins) != 1:
            return 403, "Untrusted local request origin."
        try:
            origin = urlsplit(origins[0])
            valid = (origin.scheme == "http" and not origin.path and
                     not origin.query and not origin.fragment and
                     _authority(origin.netloc) == authority)
        except ValueError:
            valid = False
        if not valid:
            return 403, "Untrusted local request origin."
    if mutation:
        types = headers.get_all("Content-Type", [])
        if len(types) != 1 or headers.get_content_type() != "application/json":
            return 415, "Local mutations require application/json."
    return None
