"""Stdlib validation of the full frozen task.result/common envelopes.

Keep required/optional fields identical to the canonical schemas. The sidecar
also strictly checks the declared RFC3339 date-time format, including
leap-second placement, even when optional jsonschema format plugins are absent.
Tests use the frozen-schema structural oracle.
"""
import re
from datetime import date, datetime, time, timedelta

ERROR_CODES = {"auth.expired", "auth.denied", "auth.unpaired", "session.not_found",
               "task.not_found", "validation", "timeout", "offline", "routing", "internal"}
REQUIRED = {"v", "type", "session_id", "task_id", "status", "summary", "artifact_links", "created_at", "agent_id"}
STATUSES = {"accepted", "running", "succeeded", "failed"}


# RFC3339 section 5.6 grammar, ASCII digits only. Calendar/clock ranges are
# checked separately, never by rewriting the wire literal.
_DATE_TIME = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})[Tt]([0-9]{2}):([0-9]{2}):([0-9]{2})(\.[0-9]+)?"
    r"(?:([Zz])|([+-])([0-9]{2}):([0-9]{2}))", re.ASCII)
# Days whose final UTC second was an inserted leap second (IERS, 1972-2016).
# None has been inserted since; an unannounced :60 is not terminal proof.
LEAP_SECOND_UTC_DATES = frozenset(date.fromisoformat(d) for d in (
    "1972-06-30", "1972-12-31", "1973-12-31", "1974-12-31", "1975-12-31", "1976-12-31",
    "1977-12-31", "1978-12-31", "1979-12-31", "1981-06-30", "1982-06-30", "1983-06-30",
    "1985-06-30", "1987-12-31", "1989-12-31", "1990-12-31", "1992-06-30", "1993-06-30",
    "1994-06-30", "1995-12-31", "1997-06-30", "1998-12-31", "2005-12-31", "2008-12-31",
    "2012-06-30", "2015-06-30", "2016-12-31"))


def _date_time(value):
    if not isinstance(value, str):
        return False
    match = _DATE_TIME.fullmatch(value)
    if not match:
        return False
    year, month, day, hour, minute, second = (int(match.group(i)) for i in range(1, 7))
    if hour > 23 or minute > 59 or second > 60:
        return False
    try:
        day_start = datetime(year, month, day)  # validates year 1-9999, month and mday
    except ValueError:
        return False
    if match.group(8):
        offset = timedelta(0)
    else:
        offset_hour, offset_minute = int(match.group(10)), int(match.group(11))
        if offset_hour > 23 or offset_minute > 59:
            return False
        offset = timedelta(hours=offset_hour, minutes=offset_minute)
        if match.group(9) == "-":
            offset = -offset
    if second < 60:
        return True
    # A leap second is only the final second of a UTC leap day after the
    # numeric offset is removed (RFC3339 sections 5.7 and Appendix D).
    try:
        utc_second_59 = day_start + timedelta(hours=hour, minutes=minute, seconds=59) - offset
    except OverflowError:
        return False
    return utc_second_59.time() == time(23, 59, 59) and utc_second_59.date() in LEAP_SECOND_UTC_DATES


def is_task_result(body):
    if not isinstance(body, dict) or not REQUIRED <= body.keys() or body.keys() - (REQUIRED | {"error_class"}):
        return False
    if body["v"] != "1" or body["type"] != "task.result":
        return False
    if any(not isinstance(body[k], str) or not re.search(r"^[A-Za-z0-9_\-.:]+$", body[k])
           for k in ("task_id", "session_id")):
        return False
    if not isinstance(body["status"], str) or body["status"] not in STATUSES:
        return False
    if not all(isinstance(body[k], str) for k in ("summary", "agent_id")) or not _date_time(body["created_at"]):
        return False
    error = body.get("error_class")  # OPTIONAL and nullable in the frozen schema.
    if error is not None and (not isinstance(error, str) or error not in ERROR_CODES):
        return False
    links = body["artifact_links"]
    return bool(isinstance(links, list) and all(
        isinstance(link, dict) and set(link) == {"title", "url"}
        and all(isinstance(link[k], str) and len(link[k]) >= 1 for k in ("title", "url"))
        for link in links))
