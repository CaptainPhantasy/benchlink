"""Bridge Control UX shell — Amir v0.2 Pick → Compose/Send → Wait.

Soft locks (Floyd GM / SSoT-design.md):
  O1 no mid-task handoff (end → re-pick)
  O2 no auto-launch with Grok.app
  O3 HP Floyd ping default OFF (reply_destination=bridge_status)
  O4 session-only retention (in-memory transcript; no durable store)
  O5 window chrome (CLI TUI + optional loopback HTML)
  O6 Bridge Control IS the sidecar UI

Static roster S2: Floyd (default), Ethan (canary), Amir, Sofia, Larry, Claire, Maya, Grace.
Only Floyd/Ethan carry known agent UUIDs from api-contracts-mvp.md; others are name keys.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable

from .client import AuthNotPaired, BridgeClient, PreparedRequest
from .ui.copy import error_card, result_card, status_token
from .config import ConfigError, SidecarConfig, ui_config

# Soft-lock constants (must stay true for MVP shell)
O1_NO_MID_TASK_HANDOFF = True
O2_NO_AUTO_LAUNCH = True
O3_HP_PING_DEFAULT_OFF = True
O4_SESSION_ONLY_RETENTION = True
O5_WINDOW_CHROME = True
O6_SIDECAR_IS_BRIDGE_CONTROL = True

FLOYD_ID = "3e5e1310-2535-4281-bcdc-f12f08d78f75"
ETHAN_ID = "bd8bb2b0-1df6-42f9-92ee-634c17736ccd"


@dataclass(frozen=True)
class RosterEntry:
    key: str                 # target_agent value for session.create / task.create
    display: str
    role: str
    group: str               # recommended | canary | leads
    agent_id: str | None = None  # known UUID only when in contracts


# S2 static roster — Floyd default
ROSTER: tuple[RosterEntry, ...] = (
    RosterEntry("floyd", "Floyd · General Manager", "General Manager", "recommended", FLOYD_ID),
    RosterEntry("ethan", "Ethan · Product", "Product", "canary", ETHAN_ID),
    RosterEntry("amir", "Amir · Design", "Design", "leads", None),
    RosterEntry("sofia", "Sofia · Full-Stack", "Full-Stack", "leads", None),
    RosterEntry("larry", "Larry · Delivery", "Delivery", "leads", None),
    RosterEntry("claire", "Claire · Ops", "Ops", "leads", None),
    RosterEntry("maya", "Maya · Marketing", "Marketing", "leads", None),
    RosterEntry("grace", "Grace · Client Success", "Client Success", "leads", None),
)
DEFAULT_TARGET = "floyd"


class TaskInFlight(RuntimeError):
    """The current task must be explicitly ended before another dispatch."""


class Phase(str, Enum):
    PICK = "pick"
    COMPOSE = "compose"
    WAIT = "wait"
    DONE = "done"
    ERROR = "error"


class WaitStatus(str, Enum):
    IDLE = "idle"
    QUEUED = "queued"
    RUNNING = "running"
    RESULT = "result"
    ERROR = "error"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _redact(body):
    if isinstance(body, dict) and "access_token" in body:
        body = dict(body, access_token="<redacted>")
    return body


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class SessionTranscript:
    """O4: in-memory only; discarded when shell ends or End task."""
    entries: list[dict] = field(default_factory=list)

    def add(self, kind: str, **payload):
        self.entries.append({"kind": kind, "at": _now_iso(), **payload})


@dataclass
class BridgeControlShell:
    cfg: SidecarConfig
    client: BridgeClient | None = None
    phase: Phase = Phase.PICK
    target_key: str = DEFAULT_TARGET
    goal: str = ""
    high_priority: bool = False  # O3 default off
    session_id: str | None = None
    task_id: str | None = None
    wait_status: WaitStatus = WaitStatus.IDLE
    result: dict | None = None
    error: dict | None = None
    dry_run_mode: bool = True
    offline_banner: str | None = None
    last_session_req: PreparedRequest | None = None
    last_task_req: PreparedRequest | None = None
    transcript: SessionTranscript = field(default_factory=SessionTranscript)
    poll_sleep: float = 0.05  # short for smoke; interactive may raise
    max_wait_ticks: int = 8

    def __post_init__(self):
        if self.client is None:
            self.client = BridgeClient(self.cfg)
        # Fail-closed: unset/placeholder → dry-run + banner (no invented DNS)
        try:
            _ = self.cfg.api_base
            self.dry_run_mode = False
            self.offline_banner = None
        except ConfigError as e:
            self.dry_run_mode = True
            self.offline_banner = str(e)

    # ---- soft-lock assertions (smoke) ------------------------------------
    @staticmethod
    def soft_locks() -> dict:
        return {
            "O1_no_mid_task_handoff": O1_NO_MID_TASK_HANDOFF,
            "O2_no_auto_launch": O2_NO_AUTO_LAUNCH,
            "O3_hp_ping_default_off": O3_HP_PING_DEFAULT_OFF,
            "O4_session_only_retention": O4_SESSION_ONLY_RETENTION,
            "O5_window_chrome": O5_WINDOW_CHROME,
            "O6_sidecar_is_bridge_control": O6_SIDECAR_IS_BRIDGE_CONTROL,
        }

    def roster(self) -> list[dict]:
        return [
            {"key": e.key, "display": e.display, "role": e.role,
             "group": e.group, "agent_id": e.agent_id,
             "default": e.key == DEFAULT_TARGET}
            for e in ROSTER
        ]

    def pick(self, target_key: str = DEFAULT_TARGET) -> None:
        """Phase 1: Pick target. O1: only allowed from PICK / DONE / ERROR (not mid-WAIT)."""
        if self.phase == Phase.WAIT and O1_NO_MID_TASK_HANDOFF:
            raise TaskInFlight("O1: mid-task handoff forbidden; End task first, then re-pick")
        keys = {e.key for e in ROSTER}
        if target_key not in keys:
            raise ValueError(f"unknown roster key {target_key!r}; known={sorted(keys)}")
        # Ending prior session clears in-memory transcript retention boundary
        if self.phase in {Phase.DONE, Phase.ERROR, Phase.COMPOSE} and self.session_id:
            self.end_task()
        self.target_key = target_key
        self.goal = ""
        self.high_priority = False  # O3 reset
        self.session_id = None
        self.task_id = None
        self.result = None
        self.error = None
        self.wait_status = WaitStatus.IDLE
        self.last_session_req = None
        self.last_task_req = None
        self.phase = Phase.COMPOSE
        self.transcript.add("pick", target=target_key)

    def compose(self, goal: str, high_priority: bool = False) -> None:
        """Phase 2a: set Staff task text (A5 typing in Bridge Control)."""
        if self.phase not in {Phase.COMPOSE, Phase.PICK}:
            # Allow re-compose only before send; after send must End first (O1)
            if self.phase == Phase.WAIT and O1_NO_MID_TASK_HANDOFF:
                raise TaskInFlight("O1: cannot recompose mid-wait; End task first")
        if self.phase == Phase.PICK:
            self.pick(self.target_key)
        goal = (goal or "").strip()
        if not goal:
            raise ValueError("goal must be non-empty (task.create.goal)")
        self.goal = goal
        self.high_priority = bool(high_priority)
        self.phase = Phase.COMPOSE
        self.transcript.add("compose", goal=goal, high_priority=self.high_priority)

    def send(self) -> dict:
        """Phase 2b: Send = session.create then task.create. Dry-run when URL unset."""
        if self.phase != Phase.COMPOSE or not self.goal:
            raise RuntimeError("Compose a non-empty Staff task before Send")
        priority = "high" if self.high_priority else "normal"
        # O3: default reply_destination is bridge_status; high only if operator enabled
        reply = "both" if self.high_priority else "bridge_status"
        self.session_id = _new_id("sess")
        self.task_id = _new_id("task")
        dry = self.dry_run_mode
        self.last_session_req = self.client.build_session_create(
            self.target_key, {"origin": "bridge-control", "ux": "pick-compose-wait"},
            dry_run=dry)
        # Override body session consistency: builders don't embed our local ids —
        # task.create carries session_id/task_id we minted for the Wait screen.
        self.last_task_req = self.client.build_task_create(
            self.session_id, self.task_id, self.goal,
            target_agent=self.target_key, priority=priority,
            reply_destination=reply, dry_run=dry)

        payloads = {
            "mode": "dry-run" if dry else "live",
            "offline_banner": self.offline_banner,
            "session.create": self.last_session_req.redacted(),
            "task.create": self.last_task_req.redacted(),
        }

        if not dry:
            try:
                sc_live = self.client.build_session_create(
                    self.target_key, {"origin": "bridge-control"}, dry_run=False)
                st, body = self.client.send(sc_live)
                payloads["session_response"] = {"status": st, "body": _redact(body)}
                if st >= 400 or not isinstance(body, dict) or body.get("type") != "session.created":
                    return self._fail_send(st, body, payloads)
                self.session_id = body["session_id"]
                tc_live = self.client.build_task_create(
                    self.session_id, self.task_id, self.goal,
                    target_agent=self.target_key, priority=priority,
                    reply_destination=reply, dry_run=False)
                st2, body2 = self.client.send(tc_live)
                payloads["task_response"] = {"status": st2, "body": _redact(body2)}
                if st2 >= 400 or not isinstance(body2, dict) or body2.get("type") != "task.result":
                    return self._fail_send(st2, body2, payloads)
                self.task_id = body2["task_id"]
            except AuthNotPaired as e:
                return self._fail_send(401, {"code": "auth.unpaired", "message": str(e)}, payloads)
            except ConfigError as e:
                return self._fail_send(503, {"code": "offline", "message": str(e)}, payloads)
            except OSError as e:
                return self._fail_send(502, {"code": "offline", "message": f"bridge unreachable: {e}"}, payloads)

        self.wait_status = WaitStatus.QUEUED
        self.phase = Phase.WAIT
        self.transcript.add("send", session_id=self.session_id, task_id=self.task_id,
                            mode=payloads["mode"])
        return payloads

    def _fail_send(self, status: int, body, payloads: dict) -> dict:
        """B1–B4: surface the frozen error class in bridge status; never silently drop."""
        body = body if isinstance(body, dict) else {}
        self.phase = Phase.ERROR
        self.wait_status = WaitStatus.ERROR
        self.error = {"code": body.get("code") or body.get("error_class") or "internal",
                      "message": body.get("message") or body.get("summary") or f"HTTP {status}",
                      "http": status, "correlation_id": body.get("correlation_id")}
        self.transcript.add("send_error", error=self.error)
        payloads["error"] = self.error
        return payloads

    def wait_once(self) -> dict:
        """One Wait tick: poll GET /tasks/{id} when live; simulate locally when dry-run."""
        if self.phase != Phase.WAIT:
            raise RuntimeError(f"wait_once only in WAIT phase (now {self.phase})")
        if self.dry_run_mode:
            return self._simulate_wait_tick()
        assert self.task_id
        try:
            st, body = self.client.send(self.client.build_task_get(self.task_id))
        except AuthNotPaired as e:
            self.wait_status = WaitStatus.ERROR
            self.phase = Phase.ERROR
            self.error = {"code": "auth.unpaired", "message": str(e)}
            return {"wait_status": self.wait_status.value, "error": self.error}
        except (ConfigError, OSError) as e:
            self.wait_status = WaitStatus.ERROR
            self.phase = Phase.ERROR
            self.error = {"code": "offline", "message": str(e)}
            return {"wait_status": self.wait_status.value, "error": self.error}
        # All live errors fail closed, even malformed bodies or a spurious result.
        if st >= 400 or not isinstance(body, dict) or body.get("type") != "task.result":
            code = body.get("code", "internal") if isinstance(body, dict) else "internal"
            if st == 501:
                code = "internal"
            card = error_card(code)
            self.wait_status = WaitStatus.ERROR
            self.phase = Phase.ERROR
            self.error = {"code": card["details_code"], "message": card["body"],
                          "http": st, "correlation_id": body.get("correlation_id") if isinstance(body, dict) else None}
            return {"wait_status": self.wait_status.value, "http": st, "error": self.error}
        if isinstance(body, dict) and body.get("type") == "task.result":
            status = body.get("status")
            if status in {"accepted"}:
                self.wait_status = WaitStatus.QUEUED
            elif status == "running":
                self.wait_status = WaitStatus.RUNNING
            elif status == "succeeded":
                self.wait_status = WaitStatus.RESULT
                self.result = body
                self.phase = Phase.DONE
            elif status == "failed":
                self.wait_status = WaitStatus.ERROR
                self.result = body
                self.error = {"code": body.get("error_class") or "internal",
                              "message": body.get("summary"), "http": st,
                              "correlation_id": None}
                self.phase = Phase.ERROR
            else:
                self.wait_status = WaitStatus.RUNNING
            return {"wait_status": self.wait_status.value, "http": st, "body": body}

    def _simulate_wait_tick(self) -> dict:
        """Offline demo: local status only — does NOT invent DNS or call upstream."""
        if not self.dry_run_mode:
            raise RuntimeError("Simulation is available only in explicit dry-run mode")
        progression = {
            WaitStatus.QUEUED: WaitStatus.RUNNING,
            WaitStatus.RUNNING: WaitStatus.RESULT,
            WaitStatus.IDLE: WaitStatus.QUEUED,
        }
        nxt = progression.get(self.wait_status, WaitStatus.RESULT)
        self.wait_status = nxt
        if nxt == WaitStatus.RESULT:
            entry = next(e for e in ROSTER if e.key == self.target_key)
            self.result = {
                "v": "1", "type": "task.result",
                "session_id": self.session_id, "task_id": self.task_id,
                "status": "succeeded",
                "summary": f"[offline demo] Staff task to {entry.display} completed (dry-run; no upstream).",
                "artifact_links": [],
                "error_class": None,
                "agent_id": entry.agent_id or entry.key,
                "created_at": _now_iso(),
            }
            self.phase = Phase.DONE
            self.transcript.add("result", summary=self.result["summary"])
        return {"wait_status": self.wait_status.value, "mode": "offline-demo",
                "result": self.result}

    def wait_until_done(self, max_ticks: int | None = None) -> dict:
        ticks = []
        for _ in range(max_ticks or self.max_wait_ticks):
            if self.phase != Phase.WAIT:
                break
            ticks.append(self.wait_once())
            if self.phase in {Phase.DONE, Phase.ERROR}:
                break
            time.sleep(self.poll_sleep)
        return {"phase": self.phase.value, "wait_status": self.wait_status.value,
                "ticks": ticks, "result": self.result, "error": self.error}

    def end_task(self) -> None:
        """O1: end session → back to Pick. Clears session-only transcript (O4)."""
        if self.session_id and not self.dry_run_mode and self.client:
            try:
                req = self.client.build_session_close(self.session_id, "user_done")
                self.client.send(req)
            except (ConfigError, OSError):
                pass
        self.transcript = SessionTranscript()  # O4: drop durable state
        self.session_id = None
        self.task_id = None
        self.goal = ""
        self.result = None
        self.error = None
        self.wait_status = WaitStatus.IDLE
        self.phase = Phase.PICK
        self.last_session_req = None
        self.last_task_req = None

    def snapshot(self) -> dict:
        entry = next((e for e in ROSTER if e.key == self.target_key), None)
        target_name = entry.display if entry else self.target_key
        presentation = {"status": status_token(self.wait_status.value)}
        if self.result and self.wait_status == WaitStatus.RESULT:
            presentation["result"] = result_card(self.result, target_name=target_name)
        if self.error:
            preview = bool(self.dry_run_mode and (self.error.get("code") == "offline"))
            presentation["error"] = error_card(
                self.error.get("code"),
                target_name=target_name,
                device_id=getattr(self.cfg, "device_id", None),
                correlation_id=self.error.get("correlation_id"),
                http=self.error.get("http"),
                preview=preview,
            )
        return {
            "phase": self.phase.value,
            "target": self.target_key,
            "goal": self.goal,
            "high_priority": self.high_priority,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "wait_status": self.wait_status.value,
            "dry_run_mode": self.dry_run_mode,
            "offline_banner": self.offline_banner,
            "result": self.result,
            "error": ({**self.error, "message": presentation["error"]["body"]} if self.error else None),
            "presentation": presentation,
            "soft_locks": self.soft_locks(),
            "config": ui_config(self.cfg),
            "roster": self.roster(),
        }

    def start_and_wait(self, goal: str, target_key: str = DEFAULT_TARGET,
                       high_priority: bool = False, max_ticks: int | None = None) -> dict:
        """Pick → Compose → Send → Wait (bounded); leaves state for later polls / End."""
        if self.phase == Phase.WAIT:
            raise TaskInFlight("O1: a task is in flight; End task first")
        self.pick(target_key)
        self.compose(goal, high_priority=high_priority)
        send_info = self.send()
        wait_info = self.wait_until_done(max_ticks)
        return {"send": send_info, "wait": wait_info, "snapshot": self.snapshot()}

    # ---- non-interactive happy path (smoke) --------------------------------
    def run_happy_path(self, goal: str = "Confirm Bridge Control Pick→Compose→Wait",
                       target_key: str = DEFAULT_TARGET) -> dict:
        self.pick(target_key)
        self.compose(goal, high_priority=False)
        assert self.high_priority is False  # O3
        send_info = self.send()
        wait_info = self.wait_until_done()
        snap = self.snapshot()
        # End → re-pick proves O1
        self.end_task()
        assert self.phase == Phase.PICK
        return {"send": send_info, "wait": wait_info, "final_before_end": snap,
                "phase_after_end": self.phase.value}


def render_tui(shell: BridgeControlShell) -> str:
    """Simple window-chrome TUI text (O5)."""
    s = shell.snapshot()
    lines = [
        "┌─ Benchlink · Bridge Control ─────────────────────────────┐",
        f"│  Phase: {s['phase']:<10}  Target: {s['target']:<12}  Bridge: "
        + ("DRY-RUN/offline" if s["dry_run_mode"] else "live-URL") + "  │",
    ]
    if s["offline_banner"]:
        lines.append("│  ! Offline — base URL unset/placeholder (fail closed)     │")
    if s["phase"] == "pick":
        lines.append("│  S2 Pick target (Floyd default):                          │")
        for e in ROSTER:
            mark = "→" if e.key == DEFAULT_TARGET else " "
            lines.append(f"│  {mark} [{e.key}] {e.display} ({e.group})".ljust(60) + "│")
    elif s["phase"] == "compose":
        lines.append(f"│  Compose Staff task → {s['target']}   HP: {s['high_priority']} (O3 default off) │")
        lines.append(f"│  goal: {(s['goal'] or '(type here…)')[:48]}".ljust(60) + "│")
    elif s["phase"] in {"wait", "done", "error"}:
        lines.append(f"│  Bridge status: {s['wait_status']}".ljust(60) + "│")
        if s["result"]:
            lines.append(f"│  summary: {str(s['result'].get('summary', ''))[:46]}".ljust(60) + "│")
        if s["error"]:
            lines.append(f"│  error: {str(s['error'])[:48]}".ljust(60) + "│")
    lines.append("└──────────────────────────────────────────────────────────┘")
    return "\n".join(lines)


def run_cli_demo(cfg: SidecarConfig, goal: str | None = None) -> int:
    """Non-interactive demo used by `python -m benchlink_sidecar ux`."""
    shell = BridgeControlShell(cfg)
    print(render_tui(shell))
    out = shell.run_happy_path(goal or "Confirm Bridge Control Pick→Compose→Wait")
    print(render_tui(shell))  # back at Pick after End
    print(json.dumps({
        "ok": True,
        "mode": out["send"]["mode"],
        "phases": ["pick", "compose", "wait", out["wait"]["phase"], "pick"],
        "wait_status_final": out["wait"]["wait_status"],
        "soft_locks": BridgeControlShell.soft_locks(),
        "session.create_op": out["send"]["session.create"]["operationId"],
        "task.create_op": out["send"]["task.create"]["operationId"],
        "offline_banner_set": bool(out["send"].get("offline_banner")),
    }, indent=2))
    return 0
