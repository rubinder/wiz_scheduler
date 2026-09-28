"""One-off, local-only script to backfill realistic-looking check-ins and
payroll time entries for the seeded demo company, so the marketing site's
check-in-report and payroll feature screenshots show real data instead of an
empty state. Never run against a deployed database."""

import asyncio
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from backend.database import async_session_factory
from backend.models import Employee, Location, Shift, ShiftSchedule, User
from backend.models.employee_check_in import CHECK_IN_MATCHED, EmployeeCheckIn
from backend.services.time_entries import (
    approve_entries,
    attest_shift,
    derive_time_entries,
)

COMPANY_ID = "comp0001"
LOCATION_ID = "locn0001"
MANAGER_USER_ID = "user0001"


async def main() -> None:
    async with async_session_factory() as db:
        location = (await db.execute(
            select(Location).where(Location.id == LOCATION_ID)
        )).scalar_one()

        shifts = list((await db.execute(
            select(Shift)
            .join(ShiftSchedule, ShiftSchedule.id == Shift.shift_schedule_id)
            .where(
                ShiftSchedule.status == "approved",
                ShiftSchedule.week_start_date == date(2026, 8, 31),
                Shift.location_id == LOCATION_ID,
            )
            .order_by(Shift.start_time)
        )).scalars().all())
        print(f"found {len(shifts)} approved shifts for {LOCATION_ID}")

        # Split: first 9 get a check-in (checked_in time entries), next 4 get
        # a manager attestation instead (manager_attested time entries), the
        # rest stay untouched so the payroll page still shows an exception.
        checked_in_shifts = shifts[:9]
        attested_shifts = shifts[9:13]

        counters: dict[date, int] = {}
        for i, shift in enumerate(checked_in_shifts):
            local_date = shift.start_time.astimezone(
                timezone.utc
            ).date()  # location is UTC-friendly enough for seed purposes
            counter = counters.get(local_date, 0)
            counters[local_date] = counter + 1
            # Small, varied lateness: mostly on time, a couple minutes early
            # or late, so the report doesn't look synthetic.
            lateness = [-3, 0, 0, 2, -1, 5, 0, -2, 1][i % 9]
            checked_in_at = shift.start_time + timedelta(minutes=lateness)
            db.add(EmployeeCheckIn(
                company_id=COMPANY_ID,
                location_id=LOCATION_ID,
                employee_id=shift.employee_id,
                shift_id=shift.id,
                checked_in_at=checked_in_at,
                local_date=local_date,
                counter=counter,
                status=CHECK_IN_MATCHED,
                minutes_from_start=lateness,
            ))
        await db.commit()
        print(f"created {len(checked_in_shifts)} check-ins")

        result = await derive_time_entries(
            db, COMPANY_ID, date(2026, 8, 31), date(2026, 9, 7), LOCATION_ID,
        )
        print(f"derived time entries: {result}")

        manager = (await db.execute(
            select(User).where(User.id == MANAGER_USER_ID)
        )).scalar_one()
        for shift in attested_shifts:
            try:
                entry = await attest_shift(
                    db, COMPANY_ID, shift.id, manager,
                    "Phone died mid-shift; confirming from the schedule.",
                )
                print(f"attested {shift.id} -> {entry.id}")
            except Exception as exc:  # noqa: BLE001 - best-effort seed script
                print(f"attest failed for {shift.id}: {exc}")

        # Approve everything through 2026-09-02 so the payroll page shows a
        # mix of approved and still-pending entries.
        approve_result = await approve_entries(
            db, COMPANY_ID, date(2026, 8, 31), date(2026, 9, 2), manager,
            LOCATION_ID,
        )
        print(f"approved: {approve_result}")


if __name__ == "__main__":
    asyncio.run(main())
