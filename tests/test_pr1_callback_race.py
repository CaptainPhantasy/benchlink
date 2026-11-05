"""Deterministic stale-token interleaving, without timing sleeps or real credentials."""
import threading

import pytest

from benchlink_adapter.errors import BridgeError
from benchlink_adapter.results import HttpCallbackResultSource


@pytest.mark.parametrize("second_status", ["succeeded", "failed", "running"])
def test_terminal_callback_consume_and_publish_are_atomic(second_status):
    source = HttpCallbackResultSource("http://127.0.0.1:1")
    delivery = source.delivery({"task_id": "task_atomic"})
    token = delivery["token"]
    released, second_done = threading.Event(), threading.Event()
    accepted, rejected, failures = [], [], []

    class InterleavingLock:
        """After A's first critical section, run B to completion before A resumes.

        Old code releases after lookup: B reuses A's token, then A publishes stale.
        Fixed code releases only after validation+final consume+publication.
        """
        def __init__(self):
            self.lock = threading.Lock()
            self.armed = True

        def __enter__(self):
            self.lock.acquire()

        def __exit__(self, *exc):
            self.lock.release()
            if threading.current_thread().name == "callback-A" and self.armed:
                self.armed = False
                released.set()
                assert second_done.wait(5), "second callback did not finish"

    source._lock = InterleavingLock()
    first = {"status": "succeeded", "summary": "first terminal result"}
    second = {"status": second_status, "summary": "must not overwrite final"}

    def submit(label, payload):
        try:
            source.accept("task_atomic", token, payload)
            accepted.append(label)
        except BridgeError as error:
            rejected.append((label, error.code))
        except BaseException as error:
            failures.append(error)
        finally:
            if label == "B":
                second_done.set()

    a = threading.Thread(target=submit, name="callback-A", args=("A", first))
    a.start()
    assert released.wait(5)
    b = threading.Thread(target=submit, name="callback-B", args=("B", second))
    b.start()
    a.join(5)
    b.join(5)
    assert not a.is_alive() and not b.is_alive() and not failures
    assert accepted == ["A"]
    assert rejected == [("B", "auth.denied")]
    assert source.fetch("task_atomic") == first
    assert "task_atomic" not in source._tokens


def test_nonterminal_callbacks_remain_reusable_until_exactly_one_final():
    source = HttpCallbackResultSource("http://127.0.0.1:1")
    token = source.delivery({"task_id": "task_progress"})["token"]
    for status in ["accepted", "running", "succeeded"]:
        source.accept("task_progress", token, {"status": status})
        assert source.fetch("task_progress")["status"] == status
    with pytest.raises(BridgeError) as error:
        source.accept("task_progress", token, {"status": "failed"})
    assert error.value.code == "auth.denied"
    assert source.fetch("task_progress")["status"] == "succeeded"
