"""Staff target routing (D1). Default = floyd; canary = ethan.

S2 static roster mirrors apps/mac-sidecar ux.ROSTER. Only Floyd/Ethan carry
known agent UUIDs (docs/api-contracts-mvp.md). Anything else -> `routing`.
"""
from __future__ import annotations

from .errors import BridgeError

FLOYD_ID = "3e5e1310-2535-4281-bcdc-f12f08d78f75"
ETHAN_ID = "bd8bb2b0-1df6-42f9-92ee-634c17736ccd"

DEFAULT_TARGET = "floyd"
CANARY_TARGET = "ethan"

ROSTER: dict[str, str | None] = {
    "floyd": FLOYD_ID,
    "ethan": ETHAN_ID,
    "amir": None, "sofia": None, "larry": None,
    "claire": None, "maya": None, "grace": None,
}
_BY_ID = {v: k for k, v in ROSTER.items() if v}


def resolve_target(raw: str | None) -> str:
    """Return roster key for a target_agent value (key or known UUID)."""
    if raw is None or str(raw).strip() == "":
        return DEFAULT_TARGET
    s = str(raw).strip()
    if s.lower() in ROSTER:
        return s.lower()
    if s.lower() in _BY_ID:
        return _BY_ID[s.lower()]
    raise BridgeError("routing", f"unknown staff target {s!r}; known={sorted(ROSTER)}"
                      " (default floyd, canary ethan)")


def agent_id_for(key: str) -> str:
    """agent_id used in bridge-minted task.result (UUID when known, else key)."""
    return ROSTER.get(key) or key
