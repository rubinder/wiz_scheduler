"""Critical fix: the week-schedule response must emit a genuine
location-local face, not the raw timestamptz instant.

`edit_approved_shifts` (backend/routers/schedules.py) writes an edit's
incoming start_time/end_time straight into the (offset-significant)
timestamptz column -- an instant. `collect_edit_warnings`
(backend/services/edit_warnings.py) passes that same payload field through
`_wall_clock`, which strips the offset and treats it as a face. Those two
readings only agree if the client posts the location's own offset, and the
client can only do that if the GET response it copied the time from was
already a genuine location face.

Before this fix, `_shift_to_response` serialized `Shift.start_time` raw. On
Postgres a shift written "09:00-04:00" is normalised on storage and reads
back "13:00+00:00" -- so the response (and everything the client echoes back
from it) was shifted by the location's UTC offset. SQLite's DateTime(
timezone=True) columns preserve the original local digits on read instead of
normalising to UTC, so a real round-trip through the test database cannot
reproduce this. This test builds a Postgres-shaped Shift directly in Python
-- a `Shift` object holding a UTC-normalised instant, exactly what asyncpg
hands back for a timestamptz column -- without touching the database at all,
so SQLite's leniency has no opportunity to mask the bug.
"""

from datetime import date, datetime, timezone

from backend.models import Location, Shift
from backend.routers.schedules import _shift_to_response
from backend.scheduling.graph import _shift_local_face
from backend.scheduling.nodes import _wall_clock
from tests.conftest import COMPANY_ID, EMPLOYEE1_ID, ROLE_FLOOR_ID, _id

NY_LOCATION = Location(
    id=_id(),
    company_id=COMPANY_ID,
    region_id=_id(),
    name="NY Test Location",
    timezone="America/New_York",
)


def _postgres_shaped_shift(start_utc: datetime, end_utc: datetime) -> Shift:
    """A `Shift` as Postgres would hand it back after commit: start_time/
    end_time are true instants, tzinfo=UTC, already normalised -- exactly
    what a timestamptz column returns against a real Postgres driver, and
    exactly what SQLite (the test database) does not reproduce on its own.
    """
    return Shift(
        id=_id(),
        company_id=COMPANY_ID,
        shift_schedule_id=_id(),
        location_id=NY_LOCATION.id,
        employee_id=EMPLOYEE1_ID,
        role_id=ROLE_FLOOR_ID,
        role_name="Floor Associate",
        date=date(2026, 8, 31),
        start_time=start_utc,
        end_time=end_utc,
    )


def test_shift_to_response_emits_the_location_local_face_not_the_utc_instant():
    """A shift stored 09:00-04:00 (New York, August -> EDT) is normalised by
    Postgres to 13:00+00:00 on storage. The response must undo that and hand
    back 09:00-04:00 -- the face the client actually needs to hold and later
    return -- not the raw UTC instant.

    This assertion alone already fails on the pre-fix code: `_shift_to_response`
    did not accept a `location` argument at all.
    """
    shift = _postgres_shaped_shift(
        datetime(2026, 8, 31, 13, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 31, 17, 0, tzinfo=timezone.utc),
    )

    resp = _shift_to_response(shift, location=NY_LOCATION)

    assert resp.start_time.isoformat() == "2026-08-31T09:00:00-04:00"
    assert resp.end_time.isoformat() == "2026-08-31T13:00:00-04:00"


def test_response_face_and_the_warnings_fallback_face_agree():
    """The two ways `collect_edit_warnings` can arrive at a shift's span must
    land on the identical wall-clock face:

    - the "payload" path: an edit carries start_time/end_time verbatim, as
      copied by the client (EditShiftModal.tsx) from a prior GET response,
      and `_wall_clock` strips the (now-correct) offset from it;
    - the "fallback" path: the edit doesn't move the time, so
      `collect_edit_warnings` reads the committed `Shift` row back out of the
      database and calls `_shift_local_face` on it directly.

    Before this fix, the payload path read `_shift_to_response`'s raw
    UTC-normalised instant and disagreed with the fallback face by exactly
    the location's offset -- 4 hours for New York -- producing spurious
    `no_availability`/`already_booked` warnings, and on an actual time edit,
    silently storing the wrong instant.
    """
    shift = _postgres_shaped_shift(
        datetime(2026, 8, 31, 13, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 31, 21, 0, tzinfo=timezone.utc),
    )

    resp = _shift_to_response(shift, location=NY_LOCATION)
    payload_start = _wall_clock(resp.start_time.isoformat())
    payload_end = _wall_clock(resp.end_time.isoformat())

    fallback_start, fallback_end = _shift_local_face(shift, NY_LOCATION)

    assert (payload_start, payload_end) == (fallback_start, fallback_end)
