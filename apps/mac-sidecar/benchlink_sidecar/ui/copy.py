"""User-facing status and error copy for Bridge Control (Amir polish spec §4.4).

Maps frozen error classes to title / body / actions. Never surfaces raw JSON,
daemon developer messages, tokens, or key paths as the headline.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class StatusToken:
    key: str
    label: str
    tone: str  # neutral | info | success | danger


STATUS: dict[str, StatusToken] = {
    "idle": StatusToken("idle", "Idle", "neutral"),
    "queued": StatusToken("queued", "Queued", "info"),
    "running": StatusToken("running", "Running", "info"),
    "result": StatusToken("result", "Result", "success"),
    "error": StatusToken("error", "Error", "danger"),
}


@dataclass(frozen=True)
class ErrorCard:
    code: str
    title: str
    body: str
    primary: str
    secondary: str | None
    correlation_id: str | None = None
    http: int | None = None
    details_code: str | None = None


# Spec §4.4. {Name} and {id} are filled by error_card().
_ERROR_TEMPLATES: dict[str, tuple[str, str, str, str | None]] = {
    "auth.expired": (
        "Pairing expired",
        "Benchlink couldn't renew this Mac's sign-in. Re-pair to continue.",
        "Re-pair",
        "Details",
    ),
    "auth.unpaired": (
        "This Mac isn't paired",
        "Pair once to send tasks.",
        "Pair this Mac",
        "Connection help",
    ),
    "auth.denied": (
        "This Mac isn't allowed",
        "Ask Larry or Claire to enroll this device. Device ID: {id}",
        "Copy device ID",
        "Details",
    ),
    "offline": (
        "Can't reach the bridge",
        "Check Tailscale is connected, then retry.",
        "Retry",
        "Open connection",
    ),
    "offline.preview": (
        "Preview mode",
        "The bridge address isn't set on this Mac, so tasks can't be sent.",
        "Connection help",
        "Details",
    ),
    "task_in_flight": (
        "Task in progress",
        "A task is in flight. End task first, then pick a target and send again.",
        "End task",
        None,
    ),
    "timeout": (
        "No reply from {Name}",
        "The task ran past the time limit. You can send it again or pick someone else.",
        "Send again",
        "End task",
    ),
    "routing": (
        "Can't route to {Name}",
        "That target isn't available. Pick another or send to Floyd.",
        "Send to Floyd",
        "End task",
    ),
    "validation": (
        "Task couldn't be sent",
        "Something in the request was rejected. Edit and try again.",
        "Edit task",
        "Details",
    ),
    "session.not_found": (
        "Task session ended",
        "The bridge no longer has this task. Start a new one.",
        "New task",
        "Details",
    ),
    "task.not_found": (
        "Task session ended",
        "The bridge no longer has this task. Start a new one.",
        "New task",
        "Details",
    ),
    "internal": (
        "Something went wrong on the bridge",
        "Try again. If it repeats, copy diagnostics for Sofia.",
        "Retry",
        "Copy diagnostics",
    ),
}

ERROR_CARDS = _ERROR_TEMPLATES  # exported for tests / HTML injection

TASK_IN_FLIGHT_TITLE = _ERROR_TEMPLATES["task_in_flight"][0]
TASK_IN_FLIGHT_BODY = _ERROR_TEMPLATES["task_in_flight"][1]


def status_token(wait_status: str | None) -> dict[str, str]:
    """Return the user-facing status pill for a WaitStatus value."""
    key = (wait_status or "idle").lower()
    tok = STATUS.get(key) or STATUS["idle"]
    return {"key": tok.key, "label": tok.label, "tone": tok.tone}


def _first_name(display_or_key: str | None) -> str:
    if not display_or_key:
        return "Floyd"
    # "Floyd · General Manager" -> Floyd; bare key "floyd" -> Floyd
    head = display_or_key.split("·", 1)[0].strip()
    return head[:1].upper() + head[1:] if head else "Floyd"


def error_card(
    code: str | None,
    *,
    target_name: str | None = None,
    device_id: str | None = None,
    correlation_id: str | None = None,
    http: int | None = None,
    preview: bool = False,
) -> dict[str, Any]:
    """Build an error card from a frozen error class. Never uses daemon message as title."""
    raw = (code or "internal").strip()
    if preview and raw == "offline":
        raw = "offline.preview"
    if raw not in _ERROR_TEMPLATES:
        # Unknown / HTTP-ish leftovers map to internal; never show "HTTP 502" as title.
        raw = "internal"
    title, body, primary, secondary = _ERROR_TEMPLATES[raw]
    name = _first_name(target_name)
    body = body.replace("{Name}", name).replace("{id}", device_id or "unknown")
    title = title.replace("{Name}", name)
    return asdict(
        ErrorCard(
            code=code or "internal",
            title=title,
            body=body,
            primary=primary,
            secondary=secondary,
            correlation_id=correlation_id,
            http=http,
            details_code=code or "internal",
        )
    )


def result_card(result: dict | None, *, target_name: str | None = None) -> dict[str, Any]:
    """Readable result card: summary + artifact links + meta. No raw JSON dump."""
    result = result or {}
    summary = result.get("summary") or "No summary returned."
    links = []
    for item in result.get("artifact_links") or []:
        if isinstance(item, str):
            links.append({"url": item, "label": _truncate_middle(item, 48)})
        elif isinstance(item, dict) and item.get("url"):
            url = str(item["url"])
            links.append({"url": url, "label": str(item.get("label") or _truncate_middle(url, 48))})
    task_id = result.get("task_id") or ""
    name = _first_name(target_name)
    return {
        "summary": summary,
        "artifact_links": links,
        "meta": f"{name} · Task ID {task_id}".strip(" ·") if task_id else name,
        "task_id": task_id,
        "session_id": result.get("session_id"),
        "has_links": bool(links),
        "empty_links_note": None if links else "No links returned",
    }


def _truncate_middle(s: str, width: int) -> str:
    if len(s) <= width:
        return s
    keep = max(4, (width - 1) // 2)
    return s[:keep] + "…" + s[-keep:]


def copy_tables_for_html() -> dict[str, Any]:
    """JSON-serializable tables injected into the HTML shell."""
    return {
        "status": {k: asdict(v) for k, v in STATUS.items()},
        "errors": {
            k: {"title": t, "body": b, "primary": p, "secondary": s}
            for k, (t, b, p, s) in _ERROR_TEMPLATES.items()
        },
        "task_in_flight": {
            "title": TASK_IN_FLIGHT_TITLE,
            "body": TASK_IN_FLIGHT_BODY,
        },
    }
