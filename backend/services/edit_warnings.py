"""Warnings raised by an approved-schedule edit.

All three are advisory: the edit applies regardless. A manager frequently
knows what the availability table does not, and refusing outright would make
editing useless in exactly the situations it exists for. Refusals live in the
router -- a checked-into shift and a closed edit window -- because those
protect data rather than advise about it.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import EmployeeAvailability, Location, Shift
from backend.scheduling.graph import _shift_local_face
from backend.scheduling.nodes import _subtract_consumed, _wall_clock


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    """Strict overlap. Touching intervals do not overlap: a shift ending at
    13:00 does not conflict with one starting at 13:00."""
    return a_start < b_end and b_start < a_end


async def collect_edit_warnings(
    db: AsyncSession,
    company_id: str,
    edits: list[Any],
    schedule: Any,
) -> list[dict]:
    """Advisory warnings for a set of edits. Never raises.

    Must be called BEFORE the edits are applied to `Shift` rows: every check
    here reads committed shifts back out of the database, and an
    already-booked check run after the mutation would be comparing a shift
    against its own post-edit state.
    """
    warnings: list[dict] = []

    for edit in edits:
        # Fetch the shift being touched (if any) up front. It's needed for
        # the already_exported check either way, and as a fallback source
        # for the employee/timespan when the edit itself doesn't carry a new
        # one (e.g. a pure reassignment that only sets employee_id).
        shift: Shift | None = None
        location: Location | None = None
        if edit.shift_id:
            row = (await db.execute(
                select(Shift, Location)
                .join(Location, Shift.location_id == Location.id)
                .where(
                    Shift.id == edit.shift_id,
                    Shift.company_id == company_id,
                    Location.company_id == company_id,
                )
            )).first()
            if row is not None:
                shift, location = row

        if edit.deleted:
            # Removing a shift cannot make anyone unavailable or double-booked.
            if shift is not None and shift.exported_at is not None:
                warnings.append({
                    "code": "already_exported",
                    "shift_id": str(shift.id),
                    "employee_id": str(shift.employee_id),
                    "detail": "This shift was exported to 7shifts; the external schedule will now disagree.",
                })
            continue

        # The employee this edit assigns. If the edit doesn't change the
        # employee (e.g. only the role or time changed), fall back to the
        # shift's current occupant -- a time change can just as easily create
        # a conflict for the employee already on the shift.
        emp_id = edit.employee_id if edit.employee_id is not None else (
            shift.employee_id if shift is not None else None
        )

        span_start: datetime | None = None
        span_end: datetime | None = None
        if emp_id is not None:
            if edit.start_time is not None and edit.end_time is not None:
                # `edit.start_time`/`edit.end_time` are the incoming request
                # payload, not a value read back from a `Shift` row: they
                # arrive from the client already carrying the location's own
                # offset and have never passed through a timestamptz column,
                # so `_wall_clock` (a tag-strip) is correct here exactly as it
                # is for availability. Do NOT convert these with
                # `_shift_local_face`/`.astimezone()`.
                span_start = _wall_clock(edit.start_time.isoformat())
                span_end = _wall_clock(edit.end_time.isoformat())
            elif shift is not None and location is not None:
                # The edit didn't move the time, so the span that matters is
                # the shift's existing one. `Shift.start_time`/`end_time` are
                # committed `Shift` rows read back from the database, so they
                # carry the timestamptz normalisation `graph.py` had to
                # correct for: they are true instants, and Postgres stores
                # them normalised to UTC. A shift written "09:00-04:00" reads
                # back as "13:00+00:00" -- stripping the tag with
                # `_wall_clock` the way we do for the edit's own payload above
                # (or for availability) would silently read the wrong face.
                # We must convert the instant into the location's own zone to
                # recover the true face -- see `_shift_local_face`,
                # backend/scheduling/graph.py.
                face = _shift_local_face(shift, location)
                if face is not None:
                    span_start, span_end = face

        if emp_id is not None and span_start is not None and span_end is not None:
            # 1. no_availability -- the employee's windows, minus their other
            #    shifts, must cover the span. Same definition the pipeline
            #    uses.
            windows = (await db.execute(
                select(EmployeeAvailability).where(
                    EmployeeAvailability.company_id == company_id,
                    EmployeeAvailability.employee_id == emp_id,
                )
            )).scalars().all()

            # `others` are committed `Shift` rows read back from the database
            # (see the note above): join each shift to its Location and
            # convert the instant into that location's own zone to recover
            # the true face, rather than tag-stripping it with `_wall_clock`.
            other_rows = (await db.execute(
                select(Shift, Location)
                .join(Location, Shift.location_id == Location.id)
                .where(
                    Shift.company_id == company_id,
                    Location.company_id == company_id,
                    Shift.employee_id == emp_id,
                    Shift.id != (edit.shift_id or ""),
                )
            )).all()
            other_spans: list[tuple[datetime, datetime]] = []
            for s, loc in other_rows:
                face = _shift_local_face(s, loc)
                if face is None:
                    # Bad/missing location.timezone: degrade rather than
                    # raise, same convention as graph.py. This drops the
                    # shift from the overlap check entirely -- acceptable for
                    # an advisory warning.
                    continue
                other_spans.append(face)

            covered = False
            for w in windows:
                w_start = _wall_clock(w.start_time.isoformat())
                w_end = _wall_clock(w.end_time.isoformat())
                for free_start, free_end in _subtract_consumed(w_start, w_end, other_spans):
                    if free_start <= span_start and free_end >= span_end:
                        covered = True
                        break
                if covered:
                    break

            if not covered:
                warnings.append({
                    "code": "no_availability",
                    "shift_id": edit.shift_id,
                    "employee_id": str(emp_id),
                    "detail": "This employee has no availability covering these hours.",
                })

            # 2. already_booked -- overlap, not exact match.
            for other_start, other_end in other_spans:
                if _overlaps(span_start, span_end, other_start, other_end):
                    warnings.append({
                        "code": "already_booked",
                        "shift_id": edit.shift_id,
                        "employee_id": str(emp_id),
                        "detail": "This employee already works an overlapping shift.",
                    })
                    break

        # 3. already_exported
        if shift is not None and shift.exported_at is not None:
            warnings.append({
                "code": "already_exported",
                "shift_id": str(shift.id),
                "employee_id": str(emp_id) if emp_id is not None else str(shift.employee_id),
                "detail": "This shift was exported to 7shifts; the external schedule will now disagree.",
            })

    return warnings
