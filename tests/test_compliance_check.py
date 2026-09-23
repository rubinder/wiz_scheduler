"""Tests for the public, unauthenticated compliance-check API.

POST /api/v1/public/compliance-check scores an uploaded schedule (JSON, no
persistence) against the clopening (minimum rest) and 14-day advance-notice
Fair Workweek rules for the marketing site's free "check your schedule"
lead magnet. See issue #116 and the controller's contract comment on it for
the exact request/response shape this is built against.
"""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

ENDPOINT = "/api/v1/public/compliance-check"


def _shift(employee: str, start: str, end: str) -> dict:
    return {"employee": employee, "start": start, "end": end}


async def test_clopening_across_day_boundary_flagged(client: AsyncClient):
    """Two shifts of the same employee on different calendar days with less
    than min_rest_hours between them are flagged as a clopening, reporting
    the measured rest_hours and naming the earlier shift `first`."""
    body = {
        "timezone": "America/New_York",
        "min_rest_hours": 11,
        "shifts": [
            _shift("A.B.", "2026-10-05 16:00", "2026-10-06 00:00"),
            _shift("A.B.", "2026-10-06 06:00", "2026-10-06 14:00"),
        ],
    }
    r = await client.post(ENDPOINT, json=body)
    assert r.status_code == 200, r.text
    data = r.json()

    assert data["totals"]["employees"] == 1
    assert data["totals"]["clopenings"] == 1
    assert data["totals"]["short_notice"] == 0

    employee = data["employees"][0]
    assert employee["employee"] == "A.B."
    assert len(employee["findings"]) == 1

    finding = employee["findings"][0]
    assert finding["kind"] == "clopening"
    assert finding["rest_hours"] == 6.0
    assert finding["first"]["start"] == "2026-10-05T16:00:00-04:00"
    assert finding["first"]["end"] == "2026-10-06T00:00:00-04:00"
    assert finding["second"]["start"] == "2026-10-06T06:00:00-04:00"
    assert finding["second"]["end"] == "2026-10-06T14:00:00-04:00"


async def test_same_day_split_shift_not_flagged(client: AsyncClient):
    """Two shifts starting on the same calendar day are a split shift,
    exempt from the cross-day rest rule even with a short gap."""
    body = {
        "timezone": "America/New_York",
        "min_rest_hours": 11,
        "shifts": [
            _shift("C.D.", "2026-10-05 06:00", "2026-10-05 10:00"),
            _shift("C.D.", "2026-10-05 14:00", "2026-10-05 22:00"),
        ],
    }
    r = await client.post(ENDPOINT, json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["totals"]["clopenings"] == 0
    assert data["employees"][0]["findings"] == []


async def test_rest_of_exactly_min_rest_hours_not_flagged(client: AsyncClient):
    """A rest gap of EXACTLY min_rest_hours across a day boundary is not a
    violation — the rule is strictly less-than."""
    body = {
        "timezone": "America/New_York",
        "min_rest_hours": 11,
        "shifts": [
            _shift("E.F.", "2026-10-05 16:00", "2026-10-06 00:00"),
            # Gap: 00:00 -> 11:00 == 11 hours exactly.
            _shift("E.F.", "2026-10-06 11:00", "2026-10-06 19:00"),
        ],
    }
    r = await client.post(ENDPOINT, json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["totals"]["clopenings"] == 0
    assert data["employees"][0]["findings"] == []


async def test_clopening_between_non_adjacent_shifts_is_still_flagged(client: AsyncClient):
    """Clopening detection must check every pair of an employee's shifts,
    not just neighbours in start order. A (16:00 Oct5 -> 02:00 Oct6) and
    B (17:00-18:00 Oct5) are exempt as a same-day split shift; B and C
    (08:00-16:00 Oct6) clear the bar at 14h rest; but A and C are only
    6.0h apart across the day boundary — a real clopening an adjacent-only
    scan would miss entirely."""
    body = {
        "timezone": "America/New_York",
        "min_rest_hours": 11,
        "shifts": [
            _shift("A", "2026-10-05 16:00", "2026-10-06 02:00"),
            _shift("A", "2026-10-05 17:00", "2026-10-05 18:00"),
            _shift("A", "2026-10-06 08:00", "2026-10-06 16:00"),
        ],
    }
    r = await client.post(ENDPOINT, json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["totals"]["clopenings"] == 1
    findings = data["employees"][0]["findings"]
    assert len(findings) == 1
    finding = findings[0]
    assert finding["kind"] == "clopening"
    assert finding["rest_hours"] == 6.0
    assert finding["first"]["start"] == "2026-10-05T16:00:00-04:00"
    assert finding["first"]["end"] == "2026-10-06T02:00:00-04:00"
    assert finding["second"]["start"] == "2026-10-06T08:00:00-04:00"
    assert finding["second"]["end"] == "2026-10-06T16:00:00-04:00"


async def test_short_notice_flagged_when_published_at_given(client: AsyncClient):
    body = {
        "timezone": "America/New_York",
        "notice_days": 14,
        "published_at": "2026-09-28T00:00:00",
        "shifts": [
            # Exactly 7 days after publish -> short notice.
            _shift("G.H.", "2026-10-05 00:00", "2026-10-05 08:00"),
        ],
    }
    r = await client.post(ENDPOINT, json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["totals"]["short_notice"] == 1
    findings = data["employees"][0]["findings"]
    assert len(findings) == 1
    assert findings[0]["kind"] == "short_notice"
    assert findings[0]["notice_days"] == 7.0
    assert findings[0]["shift"]["start"] == "2026-10-05T00:00:00-04:00"


async def test_short_notice_not_evaluated_without_published_at(client: AsyncClient):
    """Same short-notice shift, but published_at is omitted -> no finding."""
    body = {
        "timezone": "America/New_York",
        "notice_days": 14,
        "shifts": [
            _shift("G.H.", "2026-10-05 00:00", "2026-10-05 08:00"),
        ],
    }
    r = await client.post(ENDPOINT, json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["totals"]["short_notice"] == 0
    assert data["employees"][0]["findings"] == []


async def test_naive_times_interpreted_in_given_timezone_window_format(client: AsyncClient):
    """The response echoes windows as aware ISO-8601 strings carrying the
    request timezone's offset (America/New_York is -04:00 in October)."""
    body = {
        "timezone": "America/New_York",
        "min_rest_hours": 11,
        "shifts": [
            _shift("K.L.", "2026-10-05 16:00", "2026-10-06 00:00"),
            _shift("K.L.", "2026-10-06 05:00", "2026-10-06 13:00"),
        ],
    }
    r = await client.post(ENDPOINT, json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    finding = data["employees"][0]["findings"][0]
    assert finding["first"]["start"].endswith("-04:00")
    assert finding["second"]["start"].endswith("-04:00")


async def test_aware_input_passes_through_and_is_normalised_to_request_tz(client: AsyncClient):
    """A trailing-Z aware ISO datetime (UTC) is used as-is for the
    underlying instant, but echoed back rendered in the request timezone —
    asserted directly off a clopening finding's windows."""
    body = {
        "timezone": "America/New_York",
        "min_rest_hours": 11,
        "shifts": [
            # 2026-11-10T21:00:00Z == 2026-11-10T16:00:00-05:00 (EST, post-DST)
            _shift("M.N.", "2026-11-10T21:00:00Z", "2026-11-11T05:00:00Z"),
            # 2026-11-11T13:00:00Z == 2026-11-11T08:00:00-05:00
            _shift("M.N.", "2026-11-11T13:00:00Z", "2026-11-11T21:00:00Z"),
        ],
    }
    r = await client.post(ENDPOINT, json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["totals"]["employees"] == 1
    finding = data["employees"][0]["findings"][0]
    assert finding["kind"] == "clopening"
    assert finding["first"]["start"] == "2026-11-10T16:00:00-05:00"
    assert finding["first"]["end"] == "2026-11-11T00:00:00-05:00"
    assert finding["second"]["start"] == "2026-11-11T08:00:00-05:00"
    assert finding["second"]["end"] == "2026-11-11T16:00:00-05:00"
    assert finding["rest_hours"] == 8.0


async def test_aware_input_rendered_in_request_timezone(client: AsyncClient):
    body = {
        "timezone": "America/New_York",
        "min_rest_hours": 11,
        "shifts": [
            _shift("O.P.", "2026-10-05T20:00:00+00:00", "2026-10-06T04:00:00+00:00"),
            _shift("O.P.", "2026-10-06T10:00:00+00:00", "2026-10-06T18:00:00+00:00"),
        ],
    }
    r = await client.post(ENDPOINT, json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    finding = data["employees"][0]["findings"][0]
    assert finding["kind"] == "clopening"
    # 20:00Z/04:00Z Oct5-6 -> 16:00/00:00 -04:00 Oct5-6.
    assert finding["first"]["start"] == "2026-10-05T16:00:00-04:00"
    assert finding["first"]["end"] == "2026-10-06T00:00:00-04:00"
    # 10:00Z Oct6 -> 06:00 -04:00 Oct6.
    assert finding["second"]["start"] == "2026-10-06T06:00:00-04:00"
    assert finding["rest_hours"] == 6.0


async def test_aware_input_same_local_day_exempt_even_across_utc_midnight(client: AsyncClient):
    """The same-day exemption must compare LOCAL calendar days, not the
    input's original offset. Both shifts below land on the same local
    (America/New_York) calendar day — Oct 5 — even though the second one
    is timestamped past UTC midnight into Oct 6, so no finding should be
    raised despite only a 10-hour gap."""
    body = {
        "timezone": "America/New_York",
        "min_rest_hours": 11,
        "shifts": [
            # 10:00Z-14:00Z Oct5 -> 06:00-10:00 -04:00 Oct5.
            _shift("R.S.", "2026-10-05T10:00:00Z", "2026-10-05T14:00:00Z"),
            # 00:00Z-03:00Z Oct6 -> 20:00-23:00 -04:00 Oct5 (still Oct5 locally).
            _shift("R.S.", "2026-10-06T00:00:00Z", "2026-10-06T03:00:00Z"),
        ],
    }
    r = await client.post(ENDPOINT, json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["totals"]["clopenings"] == 0
    assert data["employees"][0]["findings"] == []


async def test_aware_input_different_local_days_flagged_across_utc_midnight(client: AsyncClient):
    """Conversely, two shifts that share a UTC calendar day but fall on
    different LOCAL (America/New_York) calendar days must still be
    evaluated for clopening — and the measured rest_hours must reflect the
    real gap between the local instants."""
    body = {
        "timezone": "America/New_York",
        "min_rest_hours": 11,
        "shifts": [
            # 02:00Z-03:30Z Oct6 -> 22:00-23:30 -04:00 Oct5.
            _shift("T.U.", "2026-10-06T02:00:00Z", "2026-10-06T03:30:00Z"),
            # 09:00Z-17:00Z Oct6 -> 05:00-13:00 -04:00 Oct6.
            _shift("T.U.", "2026-10-06T09:00:00Z", "2026-10-06T17:00:00Z"),
        ],
    }
    r = await client.post(ENDPOINT, json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["totals"]["clopenings"] == 1
    finding = data["employees"][0]["findings"][0]
    assert finding["kind"] == "clopening"
    assert finding["rest_hours"] == 5.5
    assert finding["first"]["start"] == "2026-10-05T22:00:00-04:00"
    assert finding["first"]["end"] == "2026-10-05T23:30:00-04:00"
    assert finding["second"]["start"] == "2026-10-06T05:00:00-04:00"
    assert finding["second"]["end"] == "2026-10-06T13:00:00-04:00"


async def test_dst_spring_forward_measures_real_elapsed_hours(client: AsyncClient):
    """Naive times in America/New_York either side of the 2027-03-14
    spring-forward (clocks jump 02:00 -> 03:00, losing a wall-clock hour)
    must be compared as real elapsed time, not naive wall-clock
    arithmetic: 01:00 EST (2027-03-14) to 05:00 EDT (2027-03-14) looks like
    a 4-hour wall-clock gap but is really 3 real hours, since the 02:00-
    03:00 hour never happened. min_rest_hours=3.5 distinguishes the two:
    a correct (3.0h) computation flags it, an incorrect naive (4.0h) one
    would not."""
    body = {
        "timezone": "America/New_York",
        "min_rest_hours": 3.5,
        "shifts": [
            _shift("DST1", "2027-03-13 20:00", "2027-03-14 01:00"),
            _shift("DST1", "2027-03-14 05:00", "2027-03-14 13:00"),
        ],
    }
    r = await client.post(ENDPOINT, json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    findings = data["employees"][0]["findings"]
    assert len(findings) == 1
    assert findings[0]["kind"] == "clopening"
    assert findings[0]["rest_hours"] == 3.0


async def test_unknown_timezone_returns_422(client: AsyncClient):
    body = {
        "timezone": "Not/AZone",
        "shifts": [_shift("Q.R.", "2026-10-05 16:00", "2026-10-05 20:00")],
    }
    r = await client.post(ENDPOINT, json=body)
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], str)


async def test_bad_datetime_returns_422_naming_shift_index(client: AsyncClient):
    body = {
        "timezone": "America/New_York",
        "shifts": [
            _shift("S.T.", "2026-10-05 16:00", "2026-10-05 20:00"),
            _shift("S.T.", "not-a-date", "2026-10-06 20:00"),
        ],
    }
    r = await client.post(ENDPOINT, json=body)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert isinstance(detail, str)
    assert "1" in detail  # offending shift is index 1


async def test_oversized_payload_returns_413(client: AsyncClient):
    from backend.config import settings

    too_many = settings.PUBLIC_COMPLIANCE_CHECK_MAX_SHIFTS + 1
    body = {
        "timezone": "America/New_York",
        "shifts": [
            _shift(f"emp-{i}", "2026-10-05 16:00", "2026-10-05 20:00")
            for i in range(too_many)
        ],
    }
    r = await client.post(ENDPOINT, json=body)
    assert r.status_code == 413
    assert isinstance(r.json()["detail"], str)


async def test_rate_limit_returns_429(client: AsyncClient):
    from backend.config import settings
    from backend.services.rate_limit import compliance_check_limiter

    compliance_check_limiter.reset()
    limit = settings.PUBLIC_COMPLIANCE_CHECK_RATE_LIMIT_PER_10MIN

    body = {
        "timezone": "America/New_York",
        "shifts": [_shift("U.V.", "2026-10-05 16:00", "2026-10-05 20:00")],
    }
    for i in range(limit):
        r = await client.post(ENDPOINT, json=body)
        assert r.status_code == 200, f"attempt {i + 1}/{limit} should succeed"

    r = await client.post(ENDPOINT, json=body)
    assert r.status_code == 429
    compliance_check_limiter.reset()


async def test_totals_arithmetic_across_multiple_employees(client: AsyncClient):
    body = {
        "timezone": "America/New_York",
        "min_rest_hours": 11,
        "notice_days": 14,
        # Chosen so Emp1's shifts (Oct 5-6) clear the 14-day notice window
        # (>= 14 days out) and only trip the clopening rule, while Emp2's
        # shift (Sep 30, 9 days out) trips only the notice rule.
        "published_at": "2026-09-21T00:00:00",
        "shifts": [
            # Employee 1: one clopening pair, notice clears the threshold.
            _shift("Emp1", "2026-10-05 16:00", "2026-10-06 00:00"),
            _shift("Emp1", "2026-10-06 06:00", "2026-10-06 14:00"),
            # Employee 2: one short-notice shift (9 days out), no clopening.
            _shift("Emp2", "2026-09-30 00:00", "2026-09-30 08:00"),
            # Employee 3: clean shift, no findings.
            _shift("Emp3", "2026-11-01 09:00", "2026-11-01 17:00"),
        ],
    }
    r = await client.post(ENDPOINT, json=body)
    assert r.status_code == 200, r.text
    data = r.json()

    assert data["totals"]["employees"] == 3
    assert data["totals"]["clopenings"] == 1
    assert data["totals"]["short_notice"] == 1

    by_name = {e["employee"]: e for e in data["employees"]}
    assert len(by_name["Emp1"]["findings"]) == 1
    assert by_name["Emp1"]["findings"][0]["kind"] == "clopening"
    assert len(by_name["Emp2"]["findings"]) == 1
    assert by_name["Emp2"]["findings"][0]["kind"] == "short_notice"
    assert by_name["Emp3"]["findings"] == []


async def test_every_employee_listed_even_with_no_findings(client: AsyncClient):
    body = {
        "timezone": "America/New_York",
        "shifts": [
            _shift("Clean1", "2026-11-01 09:00", "2026-11-01 17:00"),
            _shift("Clean2", "2026-11-02 09:00", "2026-11-02 17:00"),
        ],
    }
    r = await client.post(ENDPOINT, json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["totals"]["employees"] == 2
    names = {e["employee"] for e in data["employees"]}
    assert names == {"Clean1", "Clean2"}
    for e in data["employees"]:
        assert e["findings"] == []
