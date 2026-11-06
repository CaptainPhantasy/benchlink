"""Frozen-schema validation (packages/bridge-protocol/schemas, 0.2.0-mvp). Read-only."""
from __future__ import annotations

import json
import os
from importlib.resources import files
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

_packaged_schemas = files("benchlink_daemon").joinpath("schemas")
if os.environ.get("BENCHLINK_SCHEMAS_DIR"):
    SCHEMAS_DIR = Path(os.environ["BENCHLINK_SCHEMAS_DIR"])
elif _packaged_schemas.is_dir():
    SCHEMAS_DIR = _packaged_schemas
else:  # source-tree execution only; installed wheels carry package resources.
    SCHEMAS_DIR = Path(__file__).resolve().parents[3] / "packages/bridge-protocol/schemas"

TYPE_TO_SCHEMA = {
    "health.ping": "health_ping", "health.pong": "health_pong",
    "auth.pair.request": "auth_pair_request", "auth.pair.challenge": "auth_pair_challenge",
    "auth.pair.complete": "auth_pair_complete", "auth.session.token": "auth_session_token",
    "session.create": "session_create", "session.created": "session_created",
    "session.attach": "session_attach", "session.message": "session_message",
    "session.event": "session_event", "session.close": "session_close",
    "task.create": "task_create", "task.result": "task_result", "error": "error",
}


@lru_cache(maxsize=1)
def _registry():
    res = []
    for p in SCHEMAS_DIR.iterdir():
        if not p.name.endswith(".schema.json"):
            continue
        raw = json.loads(p.read_text())
        res.append((raw["$id"], Resource.from_contents(raw, default_specification=DRAFT202012)))
    return Registry().with_resources(res)


@lru_cache(maxsize=None)
def _validator(type_name: str) -> Draft202012Validator:
    stem = TYPE_TO_SCHEMA[type_name]
    schema = json.loads((SCHEMAS_DIR / f"{stem}.schema.json").read_text())
    return Draft202012Validator(schema, registry=_registry(),
                                format_checker=Draft202012Validator.FORMAT_CHECKER)


def errors_for(type_name: str, data) -> list[str]:
    if type_name not in TYPE_TO_SCHEMA:
        return [f"unknown message type {type_name!r}"]
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
            for e in _validator(type_name).iter_errors(data)]
