"""Golden test: the employee-timezone feature leaves NULL-timezone schedules unchanged.

Every employee starts with `timezone = NULL`, and the spec (§5, §6) promises
that schedules generated before and after the feature are identical. This
pins that: it builds the demo seed (backend/seed.py -- its roster, roles,
location split, both shift templates, and its 09:00-17:00 availability) for
one fixed week, runs the LOCAL scheduler pipeline with a fixed RNG seed for
several strategies, and compares every result field for field against
tests/fixtures/employee_timezone_golden.json.

The snapshot was produced by running this same harness against the pre-branch
code (d64c032a, the merge-base before this feature) with
`EMPLOYEE_TZ_GOLDEN_WRITE=<path>` set. The week is 2026-04-06: before this
branch the local scheduler stamped every shift with the offset of a fixed date
(2026-03-30, -04:00 for America/New_York). That was a real bug, fixed here, so
a winter week would legitimately differ. In a week where New York is on
daylight time the old fixed offset equals the real one, so any difference in
this week is a regression, not the offset fix.

Seed availability is anchored to "today" in seed.py; here it is materialized
for the fixed week instead, with the same window and the same wall-clock
tagged-UTC representation (`_seed_availability`).
"""

import json
import os
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Company,
    Employee,
    EmployeeAvailability,
    EmployeeRole,
    Location,
    Region,
    Role,
    ShiftTemplate,
)
from backend.models.ownership_group import OwnershipGroup
from backend.scheduling.graph import run_scheduling_pipeline
from backend.seed import (
    AVAILABILITY_END_HOUR,
    AVAILABILITY_START_HOUR,
    COMPANY_ID,
    EMPLOYEE_IDS,
    LOCATION_ID,
    LOCATION_ID_UPTOWN,
    OWNERSHIP_GROUP_ID,
    REGION_ID,
    ROLE_FLOOR_ID,
    ROLE_LEAD_ID,
    SHIFT_TEMPLATE_ID,
    SHIFT_TEMPLATE_ID_UPTOWN,
    _availability_id,
    _employee_location_ids,
    _role_assignments,
    _weekly_schedule,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

WEEK_START = date(2026, 4, 6)
RNG_SEED = 20261014
STRATEGIES = ["random", "rotation", "max_hours"]
SNAPSHOT = Path(__file__).parent / "fixtures" / "employee_timezone_golden.json"


async def _seed_demo(db: AsyncSession) -> None:
    """The demo tenant from backend/seed.py, availability for the fixed week."""
    db.add(OwnershipGroup(id=OWNERSHIP_GROUP_ID, name="Acme Corp Group"))
    await db.flush()
    db.add(Company(id=COMPANY_ID, ownership_group_id=OWNERSHIP_GROUP_ID,
                   name="Acme Corp", slug="acme-corp"))
    await db.flush()
    db.add_all([
        Role(id=ROLE_FLOOR_ID, company_id=COMPANY_ID, name="Floor Associate",
             description="General floor duties"),
        Role(id=ROLE_LEAD_ID, company_id=COMPANY_ID, name="Team Lead",
             description="Shift supervision"),
        Region(id=REGION_ID, company_id=COMPANY_ID, name="East Coast"),
    ])
    await db.flush()
    db.add_all([
        Location(id=LOCATION_ID, company_id=COMPANY_ID, region_id=REGION_ID,
                 name="Downtown Store", timezone="America/New_York"),
        Location(id=LOCATION_ID_UPTOWN, company_id=COMPANY_ID, region_id=REGION_ID,
                 name="Uptown Store", timezone="America/New_York"),
    ])
    await db.flush()
    for i, eid in enumerate(EMPLOYEE_IDS):
        db.add(Employee(id=eid, company_id=COMPANY_ID, full_name=f"Employee {eid}",
                        email=f"{eid}@example.com",
                        location_ids=_employee_location_ids(i)))
    await db.flush()
    for emp_idx, role_id, skill in _role_assignments():
        db.add(EmployeeRole(id=f"er{emp_idx:03d}{role_id[-2:]}", company_id=COMPANY_ID,
                            employee_id=EMPLOYEE_IDS[emp_idx], role_id=role_id,
                            skill_level=skill))
    db.add_all([
        ShiftTemplate(id=SHIFT_TEMPLATE_ID, company_id=COMPANY_ID,
                      location_id=LOCATION_ID, name="Weekday Standard",
                      weekly_schedule=_weekly_schedule()),
        ShiftTemplate(id=SHIFT_TEMPLATE_ID_UPTOWN, company_id=COMPANY_ID,
                      location_id=LOCATION_ID_UPTOWN, name="Weekday Light",
                      weekly_schedule=_weekly_schedule(floor_headcount=1)),
    ])
    # The week plus a day either side, as _seed_availability writes them.
    for emp_idx, eid in enumerate(EMPLOYEE_IDS):
        for offset in range(-1, 8):
            day = WEEK_START + timedelta(days=offset)
            db.add(EmployeeAvailability(
                id=_availability_id(emp_idx, day), company_id=COMPANY_ID,
                employee_id=eid, year=day.year, month=day.month, day=day.day,
                start_time=datetime(day.year, day.month, day.day,
                                    AVAILABILITY_START_HOUR, 0, tzinfo=timezone.utc),
                end_time=datetime(day.year, day.month, day.day,
                                  AVAILABILITY_END_HOUR, 0, tzinfo=timezone.utc),
            ))
    await db.commit()


def _normalize(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Results in a stable order, shifts sorted, as plain JSON values."""
    out = []
    for r in sorted(results, key=lambda r: str(r.get("location_id"))):
        shifts = sorted(
            r.get("shifts", []),
            key=lambda s: (s.get("date", ""), s.get("start_time", ""),
                           s.get("role_name", ""), s.get("employee_id") or ""),
        )
        out.append({
            "location_id": r.get("location_id"),
            "status": r.get("status"),
            "errors": r.get("errors", []),
            "shifts": shifts,
        })
    return json.loads(json.dumps(out, sort_keys=True, default=str))


async def _run_all(db: AsyncSession) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for strategy in STRATEGIES:
        random.seed(RNG_SEED)
        results = [r async for r in run_scheduling_pipeline(
            COMPANY_ID, WEEK_START.isoformat(), db, use_local=True, strategy=strategy,
        )]
        output[strategy] = _normalize(results)
    return output


async def test_null_timezone_schedules_match_the_pre_feature_snapshot(
    db_session: AsyncSession,
):
    await _seed_demo(db_session)
    actual = await _run_all(db_session)

    # Every strategy must actually schedule people, or the comparison is empty.
    for strategy, results in actual.items():
        assert any(s.get("employee_id") for r in results for s in r["shifts"]), strategy

    write_to = os.environ.get("EMPLOYEE_TZ_GOLDEN_WRITE")
    if write_to:
        Path(write_to).write_text(json.dumps(actual, indent=1, sort_keys=True) + "\n")
        return

    expected = json.loads(SNAPSHOT.read_text())
    assert actual == expected
