"""Explicit error classes (frozen common.schema.json#/$defs/errorClass)."""
from __future__ import annotations

ERROR_CLASSES = (
    "auth.expired", "auth.denied", "auth.unpaired", "session.not_found",
    "task.not_found", "validation", "timeout", "offline", "routing", "internal",
)

HTTP_STATUS = {
    "auth.expired": 401, "auth.denied": 403, "auth.unpaired": 401,
    "session.not_found": 404, "task.not_found": 404, "validation": 422,
    "timeout": 504, "offline": 503, "routing": 422, "internal": 500,
}

RETRYABLE = {"timeout", "offline"}


class BridgeError(Exception):
    """An error that maps 1:1 onto the frozen `error` envelope."""

    def __init__(self, code: str, message: str, retryable: bool | None = None,
                 http_status: int | None = None):
        if code not in ERROR_CLASSES:
            raise ValueError(f"unknown error class {code!r}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = (code in RETRYABLE) if retryable is None else retryable
        self.http_status = http_status or HTTP_STATUS[code]

    def __repr__(self):
        return f"BridgeError({self.code!r}, {self.message!r})"
