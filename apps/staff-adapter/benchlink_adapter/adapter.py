"""StaffAdapter — glue between bridge-daemon and Transport + ResultSource.

Dispatch payload (`benchlink.dispatch`, adapter↔bot, NON-contract — the frozen
0.2.0-mvp catalog has no bot-facing schema; documented in README):
{
  "v": "1", "type": "benchlink.dispatch", "request_id": "...",
  "target": {"key": "floyd", "agent_id": "<uuid|key>"},
  "task": <task.create body WITHOUT access_token>,
  "result_delivery": <ResultSource.delivery(task)>,
  "result_template": <task.result skeleton: v,type,session_id,task_id,agent_id>,
  "deadline_at": "<iso8601>"
}
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .config import AdapterConfig
from .results import DriveClient, ResultSource, build_result_source
from .targets import agent_id_for, resolve_target
from .transports import Transport, build_transport


class StaffAdapter:
    def __init__(self, cfg: AdapterConfig, source: ResultSource | None = None,
                 transport: Transport | None = None, drive_client: DriveClient | None = None):
        self.cfg = cfg
        self.source = source or build_result_source(cfg, drive_client)
        self.transport = transport or build_transport(cfg, self.source)

    resolve_target = staticmethod(resolve_target)

    def build_dispatch(self, task: dict, target_key: str, request_id: str) -> dict:
        clean = {k: v for k, v in task.items() if k != "access_token"}
        deadline = datetime.now(timezone.utc) + timedelta(seconds=self.cfg.task_timeout_s)
        return {
            "v": "1", "type": "benchlink.dispatch", "request_id": request_id,
            "target": {"key": target_key, "agent_id": agent_id_for(target_key)},
            "task": clean,
            "result_delivery": self.source.delivery(task),
            "result_template": {"v": "1", "type": "task.result",
                                "session_id": task["session_id"], "task_id": task["task_id"],
                                "agent_id": agent_id_for(target_key)},
            "deadline_at": deadline.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    def dispatch(self, task: dict, target_key: str, request_id: str) -> dict:
        """Fail closed: source must be ready before anything is sent."""
        self.source.check_ready()
        payload = self.build_dispatch(task, target_key, request_id)
        return self.transport.dispatch(target_key, payload, request_id)

    def fetch_result(self, task_id: str) -> dict | None:
        return self.source.fetch(task_id)
