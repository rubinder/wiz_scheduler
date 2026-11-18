"""Shared reservations fixtures."""

from dataclasses import dataclass
from datetime import time

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Location
from backend.reservations.models import DiningTable, FloorSection, TableCombination
from backend.reservations.rules import RulesSchema, ServicePeriod, save_rules
from tests.conftest import COMPANY_ID, LOCATION_ID


@dataclass(frozen=True)
class SeededFloor:
    location: Location
    section_id: str
    t1: str
    t2: str
    t3: str
    combo: str


@pytest_asyncio.fixture
async def seeded_floor(db_session: AsyncSession, seed_location: Location) -> SeededFloor:
    """America/New_York location: T1 (2-2), T2 (2-4) combinable, T3 (4-6),
    combination T1+T2 (5-6), one daily dinner period 17:00-21:00. Committed,
    so other sessions see it."""
    section = FloorSection(company_id=COMPANY_ID, location_id=LOCATION_ID, name="Main")
    db_session.add(section)
    await db_session.flush()

    def table(label: str, lo: int, hi: int, combinable: bool = False) -> DiningTable:
        return DiningTable(company_id=COMPANY_ID, location_id=LOCATION_ID,
                           section_id=section.id, label=label, min_seats=lo,
                           max_seats=hi, combinable=combinable)

    t1, t2, t3 = table("T1", 2, 2, True), table("T2", 2, 4, True), table("T3", 4, 6)
    db_session.add_all([t1, t2, t3])
    await db_session.flush()
    combo = TableCombination(company_id=COMPANY_ID, location_id=LOCATION_ID,
                             name="T1+T2", table_ids=[t1.id, t2.id],
                             min_seats=5, max_seats=6)
    db_session.add(combo)
    await save_rules(db_session, COMPANY_ID, LOCATION_ID, RulesSchema(
        service_periods=[ServicePeriod(
            name="dinner", weekdays=list(range(7)),
            first_seating=time(17, 0), last_seating=time(21, 0))]))
    await db_session.commit()
    return SeededFloor(seed_location, section.id, t1.id, t2.id, t3.id, combo.id)


AGENT_OG_ID = "ogagent1"


async def subscribe(db: AsyncSession, status: str = "active", *,
                    period_start=None, period_end=None):
    """Put the test company in an ownership group with an AI host
    subscription of `status`. Committed."""
    from sqlalchemy import update

    from backend.models import Company, OwnershipGroup
    from backend.reservations.subscription import ReservationSubscription

    if await db.get(OwnershipGroup, AGENT_OG_ID) is None:
        db.add(OwnershipGroup(id=AGENT_OG_ID, name="Agent OG"))
        await db.flush()
    await db.execute(update(Company).where(Company.id == COMPANY_ID)
                     .values(ownership_group_id=AGENT_OG_ID))
    sub = ReservationSubscription(
        ownership_group_id=AGENT_OG_ID, status=status,
        current_period_start=period_start, current_period_end=period_end)
    db.add(sub)
    await db.commit()
    return sub
