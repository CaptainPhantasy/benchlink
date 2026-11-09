"""Wire-valid task fixtures; partial objects are reserved for adversarial tests."""
from benchlink_testkit import valid


def wire_result(task_id, session_id, status, **fields):
    body = {"v":"1", "type":"task.result", "task_id":task_id, "session_id":session_id,
            "status":status, "summary":"Fixture task status", "artifact_links":[],
            "error_class":None, "agent_id":"fixture", "created_at":"2026-11-05T21:15:00Z", **fields}
    assert not valid("task.result", body), "ordinary recovery fixtures must satisfy the frozen wire schema"
    return body
