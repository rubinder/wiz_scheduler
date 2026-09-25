"""Deriving payable hours from approved shifts and the arrivals that gate them.

The pure functions come first and take primitives rather than ORM rows, so
their tests never go through a SQLite round-trip (which strips tzinfo) and
therefore test the rule rather than the driver.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import and_, case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from backend.models import (
    Employee,
    EmployeeCheckIn,
    Location,
    Shift,
    ShiftSchedule,
    TimeEntry,
    User,
)
from backend.models.employee_check_in import CHECK_IN_DUPLICATE, CHECK_IN_MATCHED
from backend.models.time_entry import (
    TIME_ENTRY_CHECKED_IN,
    TIME_ENTRY_MANAGER_ATTESTED,
)

logger = logging.getLogger(__name__)

# Locations sit at most +/-14h from UTC, so a range widened by a day either
# side is guaranteed to contain every shift whose LOCAL pay_date falls inside
# it. The exact membership test is done in Python, where the timezone is known.
_RANGE_SLACK = timedelta(days=1)


def paid_minutes(start: datetime, end: datetime) -> int:
    """Scheduled length in minutes, read off the WALL-CLOCK FACES.

    tzinfo is dropped deliberately and `end <= start` is treated as crossing
    midnight by adding 24 hours — the same convention as
    _shift_duration_hours in backend/scheduling/local_scheduler.py. A
    22:00 -> 06:00 shift is 480 minutes whether or not the generator wrote the
    end date as the next day, and whether or not a DST transition falls inside
    it. Scheduled hours are a wall-clock contract between an employer and an
    employee, not an elapsed-instant measurement, and a payroll file reporting
    7 hours for a "22:00-06:00" shift because the clocks went forward is the
    kind of surprise that generates a support ticket per location per year.

    Differs from _shift_duration_hours in one place: EQUAL FACES RAISE. No
    shift template produces a 24-hour shift, the schedule update handlers
    already reject start_time == end_time with a 422, and silently paying
    someone for a full day because two timestamps matched is the worst
    available failure mode. Callers treat this as a malformed shift.
    """
    start_face = start.hour * 60 + start.minute
    end_face = end.hour * 60 + end.minute
    if end_face == start_face:
        raise ValueError(
            f"shift start and end share a wall-clock face ({start_face // 60:02d}:"
            f"{start_face % 60:02d}); refusing to pay it as 24 hours"
        )
    if end_face < start_face:
        end_face += 24 * 60
    return end_face - start_face


def pay_date_for(timezone_name: str, start: datetime) -> date:
    """The location-local calendar date the shift STARTED.

    The whole shift's hours land here; no shift is ever split across two pay
    dates. Derived from the location's timezone, never from Shift.date (which
    is LLM-supplied and can drift) and never from the UTC instant.

    A 22:00 Sunday -> 06:00 Monday shift is paid entirely on Sunday. This
    agrees with EmployeeCheckIn.local_date (the local date of the scan, which
    happens at the start of the shift) and with the one day a human would name
    if you asked them when that shift was.
    """
    return start.astimezone(ZoneInfo(timezone_name)).date()


@dataclass(frozen=True)
class DeriveResult:
    created: int
    existing: int
    exception_count: int


@dataclass(frozen=True)
class ApproveResult:
    approved: int
    already_approved: int


@dataclass(frozen=True)
class TimeEntryRow:
    id: str
    shift_id: str
    employee_id: str
    employee_name: str
    location_id: str
    location_name: str
    role_id: str
    role_name: str
    pay_date: date
    start_time: datetime
    end_time: datetime
    paid_minutes: int
    source: str
    checked_in_at: datetime | None
    lateness_minutes: int | None
    attested_by_name: str | None
    attested_at: datetime | None
    attestation_reason: str | None
    approved_at: datetime | None
    exported_at: datetime | None


@dataclass(frozen=True)
class ExceptionRow:
    shift_id: str
    employee_id: str
    employee_name: str
    location_id: str
    location_name: str
    role_id: str
    role_name: str
    pay_date: date
    start_time: datetime
    end_time: datetime
    paid_minutes: int


@dataclass(frozen=True)
class _Candidate:
    shift: Shift
    location: Location
    employee_name: str
    pay_date: date
    paid_minutes: int


def _as_utc(dt: datetime) -> datetime:
    """Normalize a (possibly naive) datetime from SQLite to UTC-aware.

    DateTime(timezone=True) is honored by Postgres but ignored by SQLite,
    which strips tzinfo on round-trip. We always store UTC, so re-attaching it
    when missing is correct. Same pattern as _as_utc in
    backend/services/check_in.py — and the same warning applies: never build a
    shift fixture from an offset-bearing ISO string under SQLite.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def _candidate_shifts(
    db: AsyncSession,
    company_id: str,
    range_start: date,
    range_end: date,
    location_id: str | None,
) -> list[_Candidate]:
    """Approved, already-started shifts whose derived pay_date is in range.

    Filtered on start_time rather than Shift.date: Shift.date is LLM-supplied
    and can drift, while start_time is what both the check-in matcher and the
    pay date are derived from.

    A shift whose wall-clock faces are equal is skipped with a warning rather
    than raising: it is malformed, and the paid_minutes > 0 check constraint
    must never be the thing a manager discovers.
    """
    now = datetime.now(timezone.utc)
    window_start = datetime.combine(
        range_start, time.min, tzinfo=timezone.utc
    ) - _RANGE_SLACK
    window_end = datetime.combine(
        range_end, time.max, tzinfo=timezone.utc
    ) + _RANGE_SLACK

    query = (
        select(Shift, Location, Employee.full_name)
        .join(ShiftSchedule, ShiftSchedule.id == Shift.shift_schedule_id)
        .join(Location, Location.id == Shift.location_id)
        .join(Employee, Employee.id == Shift.employee_id)
        .where(
            Shift.company_id == company_id,
            ShiftSchedule.status == "approved",
            Shift.start_time >= window_start,
            Shift.start_time <= window_end,
        )
    )
    if location_id:
        query = query.where(Shift.location_id == location_id)

    candidates: list[_Candidate] = []
    for shift, location, employee_name in (await db.execute(query)).all():
        start = _as_utc(shift.start_time)
        if start >= now:
            # Not worked yet: neither an entry nor an exception.
            continue
        pay_date = pay_date_for(location.timezone, start)
        if not (range_start <= pay_date <= range_end):
            continue
        try:
            # paid_minutes is contracted on LOCAL wall-clock faces (see its
            # docstring), but Shift.start_time/end_time are UTC instants —
            # the same true-instant-vs-wall-clock distinction _shift_local_face
            # (backend/scheduling/graph.py) exists to handle. Reading the
            # faces straight off the UTC-stored values would compute the UTC
            # face, which drifts from the scheduled local length by the
            # zone's offset and, worse, by one hour across a DST transition
            # night (e.g. 22:00->06:00 America/New_York reads as 420 or 540
            # minutes instead of 480). Converting into the location's own
            # zone first recovers the intended wall-clock faces.
            zone = ZoneInfo(location.timezone)
            minutes = paid_minutes(
                _as_utc(shift.start_time).astimezone(zone),
                _as_utc(shift.end_time).astimezone(zone),
            )
        except ValueError:
            logger.warning(
                "payroll.malformed_shift shift_id=%s company_id=%s start=%s end=%s",
                shift.id, company_id, shift.start_time, shift.end_time,
            )
            continue
        candidates.append(_Candidate(
            shift=shift, location=location, employee_name=employee_name,
            pay_date=pay_date, paid_minutes=minutes,
        ))
    return candidates


async def _earliest_check_ins(
    db: AsyncSession, shift_ids: list[str]
) -> dict[str, EmployeeCheckIn]:
    """The gating scan per shift: the EARLIEST matched-or-duplicate arrival.

    We do not re-derive the match. _match_shift already ran at scan time and
    persisted its answer on EmployeeCheckIn.shift_id; re-deriving would be a
    second implementation that can disagree with the first, and would silently
    produce a different answer for any shift edited after the scan.
    """
    rows = (await db.execute(
        select(EmployeeCheckIn)
        .where(
            EmployeeCheckIn.shift_id.in_(shift_ids),
            EmployeeCheckIn.status.in_([CHECK_IN_MATCHED, CHECK_IN_DUPLICATE]),
        )
        .order_by(EmployeeCheckIn.checked_in_at.asc())
    )).scalars().all()
    # shift_id.in_(shift_ids) already guarantees a non-None shift_id on every
    # row, so setdefault alone (ordered earliest-first) picks the winner.
    earliest: dict[str, EmployeeCheckIn] = {}
    for row in rows:
        earliest.setdefault(row.shift_id, row)
    return earliest


async def derive_time_entries(
    db: AsyncSession,
    company_id: str,
    range_start: date,
    range_end: date,
    location_id: str | None = None,
    _retry: bool = True,
) -> DeriveResult:
    """Create the TimeEntry rows for a range. Idempotent.

    Loads the candidate shifts and their locations once, maps each to its
    check-in, and inserts in one commit. An IntegrityError on
    uq_time_entries_shift means a concurrent derive got there first, so the
    call is retried once — which re-reads the now-present shift ids and
    therefore excludes them. A second failure re-queries which shift ids
    actually persisted rather than assuming the rolled-back "created" rows
    landed, so a DeriveResult never reports a row as existing that isn't
    actually in the database.
    """
    candidates = await _candidate_shifts(
        db, company_id, range_start, range_end, location_id
    )
    if not candidates:
        return DeriveResult(created=0, existing=0, exception_count=0)

    shift_ids = [c.shift.id for c in candidates]
    existing_ids = set((await db.execute(
        select(TimeEntry.shift_id).where(TimeEntry.shift_id.in_(shift_ids))
    )).scalars().all())
    check_ins = await _earliest_check_ins(db, shift_ids)

    created = 0
    existing = 0
    exception_count = 0
    for c in candidates:
        if c.shift.id in existing_ids:
            existing += 1
            continue
        check_in = check_ins.get(c.shift.id)
        if check_in is None:
            exception_count += 1
            continue
        db.add(TimeEntry(
            company_id=company_id,
            location_id=c.shift.location_id,
            employee_id=c.shift.employee_id,
            shift_id=c.shift.id,
            role_id=c.shift.role_id,
            role_name=c.shift.role_name,
            pay_date=c.pay_date,
            start_time=c.shift.start_time,
            end_time=c.shift.end_time,
            paid_minutes=c.paid_minutes,
            source=TIME_ENTRY_CHECKED_IN,
            check_in_id=check_in.id,
            checked_in_at=check_in.checked_in_at,
            lateness_minutes=check_in.minutes_from_start,
        ))
        created += 1

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if _retry:
            return await derive_time_entries(
                db, company_id, range_start, range_end, location_id,
                _retry=False,
            )
        # Persistent conflict: the whole commit rolled back, so none of this
        # call's attempted inserts persisted. Re-query rather than assume
        # `created` landed, so a row that failed twice is never reported as
        # existing.
        existing_ids = set((await db.execute(
            select(TimeEntry.shift_id).where(TimeEntry.shift_id.in_(shift_ids))
        )).scalars().all())
        logger.warning(
            "payroll.derive_conflict company_id=%s range=%s..%s",
            company_id, range_start, range_end,
        )
        return DeriveResult(
            created=0, existing=len(existing_ids),
            exception_count=exception_count,
        )

    return DeriveResult(
        created=created, existing=existing, exception_count=exception_count
    )


async def list_time_entries(
    db: AsyncSession,
    company_id: str,
    range_start: date,
    range_end: date,
    location_id: str | None = None,
    approved: bool | None = None,
) -> list[TimeEntryRow]:
    """Read-only. Writes nothing, derives nothing."""
    attester = aliased(User)
    query = (
        select(
            TimeEntry, Employee.full_name, Location.name, Location.timezone,
            attester.full_name,
        )
        .join(Employee, Employee.id == TimeEntry.employee_id)
        .join(Location, Location.id == TimeEntry.location_id)
        .outerjoin(attester, attester.id == TimeEntry.attested_by_user_id)
        .where(
            TimeEntry.company_id == company_id,
            TimeEntry.pay_date >= range_start,
            TimeEntry.pay_date <= range_end,
        )
        .order_by(TimeEntry.pay_date, Employee.full_name)
    )
    if location_id:
        query = query.where(TimeEntry.location_id == location_id)
    if approved is True:
        query = query.where(TimeEntry.approved_at.isnot(None))
    elif approved is False:
        query = query.where(TimeEntry.approved_at.is_(None))

    return [
        TimeEntryRow(
            id=entry.id,
            shift_id=entry.shift_id,
            employee_id=entry.employee_id,
            employee_name=employee_name,
            location_id=entry.location_id,
            location_name=location_name,
            role_id=entry.role_id,
            role_name=entry.role_name,
            pay_date=entry.pay_date,
            start_time=entry.start_time,
            end_time=entry.end_time,
            paid_minutes=entry.paid_minutes,
            source=entry.source,
            # checked_in_at is a true instant (backend/services/check_in.py:
            # `datetime.now(timezone.utc)`), unlike start_time/end_time, which
            # the payroll schema serializes exactly as stored. Converting
            # into the location's own zone recovers the wall-clock face the
            # page renders with utils/shiftTime.ts — the same fix
            # _candidate_shifts applies to paid_minutes above.
            checked_in_at=(
                _as_utc(entry.checked_in_at).astimezone(ZoneInfo(location_tz))
                if entry.checked_in_at is not None else None
            ),
            lateness_minutes=entry.lateness_minutes,
            attested_by_name=attested_by_name,
            attested_at=entry.attested_at,
            attestation_reason=entry.attestation_reason,
            approved_at=entry.approved_at,
            exported_at=entry.exported_at,
        )
        for entry, employee_name, location_name, location_tz, attested_by_name
        in (await db.execute(query)).all()
    ]


async def list_exceptions(
    db: AsyncSession,
    company_id: str,
    range_start: date,
    range_end: date,
    location_id: str | None = None,
) -> list[ExceptionRow]:
    """Approved, started, in-range shifts with no check-in and no entry.

    Attesting a shift removes it from this list on the next load, because it
    then has an entry.
    """
    candidates = await _candidate_shifts(
        db, company_id, range_start, range_end, location_id
    )
    if not candidates:
        return []

    shift_ids = [c.shift.id for c in candidates]
    entried = set((await db.execute(
        select(TimeEntry.shift_id).where(TimeEntry.shift_id.in_(shift_ids))
    )).scalars().all())
    check_ins = await _earliest_check_ins(db, shift_ids)

    rows = [
        ExceptionRow(
            shift_id=c.shift.id,
            employee_id=c.shift.employee_id,
            employee_name=c.employee_name,
            location_id=c.shift.location_id,
            location_name=c.location.name,
            role_id=c.shift.role_id,
            role_name=c.shift.role_name,
            pay_date=c.pay_date,
            start_time=c.shift.start_time,
            end_time=c.shift.end_time,
            paid_minutes=c.paid_minutes,
        )
        for c in candidates
        if c.shift.id not in entried and c.shift.id not in check_ins
    ]
    rows.sort(key=lambda r: (r.pay_date, r.employee_name))
    return rows


def _reject(code: str, message: str, http_status: int) -> HTTPException:
    """The detail shape CheckInRejected is translated into by
    backend/routers/check_ins.py, so every refusal in this feature reads the
    same way to the frontend."""
    return HTTPException(
        status_code=http_status, detail={"code": code, "message": message}
    )


async def attest_shift(
    db: AsyncSession,
    company_id: str,
    shift_id: str,
    user: User,
    reason: str | None,
) -> TimeEntry:
    """Record that a manager confirmed an unscanned shift was worked.

    entry_exists is raised both by the check below and by catching the
    IntegrityError on uq_time_entries_shift, so a check-then-insert race
    resolves the same way whichever side loses — the pattern record_check_in
    uses for the counter constraint.
    """
    row = (await db.execute(
        select(Shift, Location, ShiftSchedule.status)
        .join(ShiftSchedule, ShiftSchedule.id == Shift.shift_schedule_id)
        .join(Location, Location.id == Shift.location_id)
        .where(Shift.id == shift_id, Shift.company_id == company_id)
    )).first()
    if row is None:
        raise _reject("shift_not_found", "That shift no longer exists.", 404)
    shift, location, schedule_status = row

    if schedule_status != "approved":
        raise _reject(
            "shift_not_approved",
            "Only shifts on an approved schedule can be confirmed.",
            status.HTTP_409_CONFLICT,
        )

    start = _as_utc(shift.start_time)
    if start >= datetime.now(timezone.utc):
        raise _reject(
            "shift_not_started",
            "That shift has not started yet.",
            status.HTTP_409_CONFLICT,
        )

    already = (await db.execute(
        select(TimeEntry.id).where(TimeEntry.shift_id == shift.id).limit(1)
    )).scalar_one_or_none()
    if already is not None:
        raise _reject(
            "entry_exists",
            "That shift already has payable hours recorded.",
            status.HTTP_409_CONFLICT,
        )

    try:
        # paid_minutes is contracted on LOCAL wall-clock faces (see its
        # docstring); converting into the location's own zone first is the
        # same fix _candidate_shifts applies above.
        zone = ZoneInfo(location.timezone)
        minutes = paid_minutes(
            _as_utc(shift.start_time).astimezone(zone),
            _as_utc(shift.end_time).astimezone(zone),
        )
    except ValueError:
        logger.warning(
            "payroll.malformed_shift shift_id=%s company_id=%s", shift.id,
            company_id,
        )
        raise _reject(
            "malformed_shift",
            "That shift's start and end times are the same; fix the shift "
            "before confirming it.",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    entry = TimeEntry(
        company_id=company_id,
        location_id=shift.location_id,
        employee_id=shift.employee_id,
        shift_id=shift.id,
        role_id=shift.role_id,
        role_name=shift.role_name,
        pay_date=pay_date_for(location.timezone, start),
        start_time=shift.start_time,
        end_time=shift.end_time,
        paid_minutes=minutes,
        source=TIME_ENTRY_MANAGER_ATTESTED,
        check_in_id=None,
        checked_in_at=None,
        lateness_minutes=None,
        attested_by_user_id=user.id,
        attested_at=datetime.now(timezone.utc),
        attestation_reason=reason,
    )
    db.add(entry)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise _reject(
            "entry_exists",
            "That shift already has payable hours recorded.",
            status.HTTP_409_CONFLICT,
        )
    await db.refresh(entry)
    return entry


async def approve_entries(
    db: AsyncSession,
    company_id: str,
    range_start: date,
    range_end: date,
    user: User,
    location_id: str | None = None,
    entry_ids: list[str] | None = None,
) -> ApproveResult:
    """Say yes to a set of payable hours. Nothing exports unapproved.

    Already-approved entries are counted and left untouched, not re-stamped:
    the approval timestamp is an audit fact about when a human said yes.
    """
    query = select(TimeEntry).where(
        TimeEntry.company_id == company_id,
        TimeEntry.pay_date >= range_start,
        TimeEntry.pay_date <= range_end,
    )
    if location_id:
        query = query.where(TimeEntry.location_id == location_id)
    if entry_ids:
        query = query.where(TimeEntry.id.in_(entry_ids))

    rows = list((await db.execute(query)).scalars().all())

    if entry_ids and len(rows) != len(set(entry_ids)):
        # An id that named another company's entry, or one outside the range,
        # simply did not come back. Refusing the whole call is what keeps a
        # partial approval from looking like a complete one. An EMPTY list is
        # treated as "everything in range", exactly like null — the spec makes
        # a non-empty list the trigger for subset mode.
        raise _reject(
            "invalid_entry_ids",
            "Some of those entries are not in this range or not yours. "
            "Nothing was approved.",
            status.HTTP_400_BAD_REQUEST,
        )

    now = datetime.now(timezone.utc)
    approved = 0
    already_approved = 0
    for row in rows:
        if row.approved_at is not None:
            already_approved += 1
            continue
        row.approved_at = now
        row.approved_by_user_id = user.id
        approved += 1

    await db.commit()
    return ApproveResult(approved=approved, already_approved=already_approved)


@dataclass(frozen=True)
class AttestationRate:
    """A plain dataclass local to this service; the router maps it to the
    AttestationRateRow schema, keeping schema imports out of services the way
    backend/routers/check_ins.py already does for the report rows."""

    location_id: str
    location_name: str
    entries: int
    attested: int
    rate: float


async def attestation_rates(
    db: AsyncSession, company_id: str, since: date
) -> list[AttestationRate]:
    """Share of payable hours confirmed by a manager instead of a check-in.

    REPORTING ONLY. Nothing reads `rate` to block an attestation, refuse an
    export, or cap anything.

    Outer-joined from Location so a location with zero entries reports 0.0
    rather than vanishing from the list — a missing row reads as "fine" when
    it actually means "no data".
    """
    attested_expr = func.sum(
        case((TimeEntry.source == TIME_ENTRY_MANAGER_ATTESTED, 1), else_=0)
    )
    rows = (await db.execute(
        select(Location.id, Location.name, func.count(TimeEntry.id), attested_expr)
        .outerjoin(
            TimeEntry,
            and_(
                TimeEntry.location_id == Location.id,
                TimeEntry.company_id == company_id,
                TimeEntry.pay_date >= since,
            ),
        )
        .where(Location.company_id == company_id)
        .group_by(Location.id, Location.name)
    )).all()

    rates = [
        AttestationRate(
            location_id=location_id,
            location_name=location_name,
            entries=entries,
            attested=int(attested or 0),
            rate=(int(attested or 0) / entries) if entries else 0.0,
        )
        for location_id, location_name, entries, attested in rows
    ]
    # Descending, so the locations worth looking at sort to the top.
    rates.sort(key=lambda r: r.rate, reverse=True)
    return rates
