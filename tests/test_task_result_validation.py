"""Frozen-schema structural oracle plus explicit RFC3339 format assertions."""
import pytest
from benchlink_testkit import valid
from benchlink_sidecar.task_result import is_task_result
from test_recovery_invariant import CASES, mutate

BASE={"v":"1","type":"task.result","task_id":"task_fixture","session_id":"sess_fixture",
      "status":"succeeded","summary":"","artifact_links":[],"agent_id":"",
      "created_at":"2026-11-05T16:15:00-05:00"}


@pytest.mark.parametrize("case", CASES)
def test_task_result_structure_matches_canonical_frozen_schema(case):
    body=mutate(BASE,case)
    if case in {"bad-date","date-no-zone","midday-leap","offset-shifted-leap"}:
        # jsonschema's optional RFC3339 plugin is absent from the pinned runtime.
        # The client explicitly enforces the format declared by the frozen schema.
        assert not is_task_result(body)
    else:
        assert is_task_result(body)==(not valid("task.result",body))


@pytest.mark.parametrize("date", ["2026-11-05T16:15:00+00:60", "2026-11-05T16:15:00+24:00",
                                  "2026-11-05T16:15:00-00:60", "2026-02-29T01:00:00Z",
                                  "1900-02-29T01:00:00Z", "2026-11-05T24:00:00Z",
                                  "2026-11-05 16:15:00Z", "2026-11-05T16:15Z", "2026-11-05T16:15:61Z"])
def test_invalid_rfc3339_calendar_clock_or_offset_is_not_terminal_proof(date):
    assert not is_task_result(dict(BASE,created_at=date))


@pytest.mark.parametrize("date", ["2000-02-29T00:00:00Z", "2026-11-05t16:15:00z",
                                  "2026-11-05T16:15:00.123456789+23:59", "1990-12-31T23:59:60Z"])
def test_declared_rfc3339_formats_are_accepted(date):
    assert is_task_result(dict(BASE,created_at=date))


@pytest.mark.parametrize("error", [None,"auth.expired","auth.denied","auth.unpaired","session.not_found",
                                   "task.not_found","validation","timeout","offline","routing","internal"])
def test_optional_error_class_uses_exact_frozen_enum_without_new_status_condition(error):
    body=dict(BASE,error_class=error)
    assert not valid("task.result",body) and is_task_result(body)
    body.pop("error_class")
    assert not valid("task.result",body) and is_task_result(body)


def test_frozen_artifact_url_is_a_nonempty_string_not_a_new_uri_restriction():
    body=dict(BASE,artifact_links=[{"title":" ","url":"artifact-ref:not-an-http-url"}])
    assert not valid("task.result",body) and is_task_result(body)


# Second 60 is valid only at an actually inserted leap second: 23:59:60 UTC after
# applying the numeric offset (RFC3339 sections 5.6, 5.7 and Appendix D).
@pytest.mark.parametrize("date", [
    "2026-11-05T16:15:60Z", "2016-12-31T23:58:60Z", "2016-12-30T23:59:60Z", "2016-12-31T22:59:60Z",
    "2017-12-31T23:59:60Z", "2016-06-30T23:59:60Z", "1971-12-31T23:59:60Z", "2016-12-31T23:59:60+01:00",
    "2016-12-31T23:59:60-05:00", "2016-12-31T23:59:60.5-05:00", "2017-01-01T00:59:60Z",
    "2016-12-31T23:59:61Z", "2016-12-31T24:59:60Z",
])
def test_misplaced_or_unannounced_leap_second_is_not_terminal_proof(date):
    assert not is_task_result(dict(BASE,created_at=date))


@pytest.mark.parametrize("date", [
    "2016-12-31T23:59:60Z", "2016-12-31T23:59:60.999999999Z", "2016-12-31T18:59:60-05:00",
    "2017-01-01T00:59:60+01:00", "1972-06-30t23:59:60z", "2015-06-30T23:59:60+00:00",
    "2015-06-30T23:59:60-00:00", "2012-07-01T05:29:60+05:30",
])
def test_inserted_leap_second_at_utc_day_end_is_accepted_with_any_offset(date):
    assert is_task_result(dict(BASE,created_at=date))


@pytest.mark.parametrize("date", [
    "2026-13-01T00:00:00Z", "2026-00-10T00:00:00Z", "2026-04-31T00:00:00Z", "2026-11-00T00:00:00Z",
    "2026-11-05T23:60:00Z", "2026-11-05T25:00:00Z", "2026-11-05T16:15:00+23:60", "2026-11-05T16:15:00+0500",
    "2026-11-05T16:15:00.Z", "2026-11-05T16:15:00,5Z", "2026-11-05T16:15:00Z\n", " 2026-11-05T16:15:00Z",
    "\uff12026-11-05T16:15:00Z", "2026-11-05T16:15:0\u0660Z", "+2026-11-05T16:15:00Z", "20261-11-05T16:15:00Z",
    "2026-11-05T16:15:00ZZ", "2026-11-05T16:15:00UTC", "", 1762377300, None,
])
def test_calendar_clock_offset_and_ascii_boundaries_fail_closed(date):
    assert not is_task_result(dict(BASE,created_at=date))


@pytest.mark.parametrize("date", ["2024-02-29T23:59:59.000001+14:00", "2000-02-29T00:00:00-23:59",
                                  "9999-12-31T23:59:59Z", "0001-01-01T00:00:00Z", "2026-11-05T16:15:00.0Z"])
def test_ordinary_calendar_and_offset_extremes_remain_valid(date):
    assert is_task_result(dict(BASE,created_at=date))
