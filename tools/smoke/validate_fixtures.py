#!/usr/bin/env python3
"""Offline fixture validation against packages/bridge-protocol/schemas (stdlib + jsonschema if present)."""
from __future__ import annotations
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "packages/bridge-protocol/schemas"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

try:
    import jsonschema
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
except Exception as e:
    print("jsonschema/referencing not installed:", e)
    print("Attempting structural checks only.")
    jsonschema = None

def load(p: Path):
    return json.loads(p.read_text())

def main() -> int:
    fixtures = {
        "health.ping.json": "health_ping.schema.json",
        "health.pong.json": "health_pong.schema.json",
        "task.create.json": "task_create.schema.json",
        "task.result.json": "task_result.schema.json",
        "error.json": "error.schema.json",
    }
    if jsonschema is None:
        # minimal required-key check
        ok = True
        for fix, sch in fixtures.items():
            data = load(FIXTURES / fix)
            schema = load(SCHEMAS / sch)
            for key in schema.get("required", []):
                if key not in data:
                    print(f"FAIL {fix}: missing {key}")
                    ok = False
                else:
                    print(f"OK   {fix}: has {key}")
            if data.get("type") != schema.get("properties", {}).get("type", {}).get("const"):
                # still ok if const matches
                const = schema.get("properties", {}).get("type", {}).get("const")
                if data.get("type") != const:
                    print(f"FAIL {fix}: type {data.get('type')} != {const}")
                    ok = False
        return 0 if ok else 1

    resources = []
    for p in SCHEMAS.glob("*.schema.json"):
        raw = load(p)
        resources.append((raw.get("$id", p.name), Resource.from_contents(raw, default_specification=DRAFT202012)))
    registry = Registry().with_resources(resources)

    failed = 0
    for fix, sch in fixtures.items():
        schema = load(SCHEMAS / sch)
        data = load(FIXTURES / fix)
        # Rewrite relative $ref to $id where needed by using registry
        validator = Draft202012Validator(schema, registry=registry)
        errors = sorted(validator.iter_errors(data), key=lambda e: e.path)
        if errors:
            failed += 1
            print(f"FAIL {fix}")
            for e in errors:
                print(" ", e.message)
        else:
            print(f"OK   {fix}")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
