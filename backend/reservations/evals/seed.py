"""Seed one eval scenario into its own company, region and location.

Everything goes through the real services (floor.save_floor,
rules.save_rules, booking.book_staff / hold / confirm / cancel) at the
scenario's fixed "now", so a scenario that seeds is one the product could
have produced. The only shortcut is `_set_fixed_code` below.
"""

from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Company, Location, Region
from backend.reservations import booking
from backend.reservations.booking import DinerContact
from backend.reservations.evals.scenarios_io import ExistingReservation, Scenario
from backend.reservations.floor import save_floor
from backend.reservations.models import (
    CANCELLED,
    PENDING,
    SOURCE_WEB_AGENT,
    Reservation,
)
from backend.reservations.rules import save_rules
from backend.utils.id_gen import generate_short_id


@dataclass(frozen=True)
class SeededScenario:
    company_id: str
    location_id: str
    slug: str


async def _set_fixed_code(db: AsyncSession, res: Reservation, code: str) -> None:
    """Eval seeding only: replace the random code booking assigned with the
    scenario's fixed one, so the diner simulator can quote it. Touches only
    `confirmation_code` (the per-location unique index still applies);
    nothing in the product sets a code this way."""
    await db.execute(
        update(Reservation)
        .where(Reservation.id == res.id, Reservation.company_id == res.company_id)
        .values(confirmation_code=code))
    res.confirmation_code = code


async def _seed_existing(db: AsyncSession, scenario: Scenario, seeded: SeededScenario,
                         item: ExistingReservation) -> Reservation:
    start = item.start_local.replace(tzinfo=ZoneInfo(scenario.location.timezone))
    diner = DinerContact(item.diner.name, item.diner.phone)
    now = scenario.now
    if item.status == PENDING:
        # A submitted large-party request, as the web agent leaves it.
        res = await booking.hold(
            db, seeded.company_id, seeded.location_id, item.party_size, start,
            source=SOURCE_WEB_AGENT, now=now, diner=diner)
        if res.status != PENDING:
            raise ValueError(f"{scenario.id}: existing party of {item.party_size} "
                             "is not above max_self_book_party, so cannot be pending")
        res = await booking.confirm(db, seeded.company_id, res.id, now=now)
    else:
        res = await booking.book_staff(db, seeded.company_id, seeded.location_id,
                                       item.party_size, start, diner, now=now)
        if item.status == CANCELLED:
            res = await booking.cancel(db, seeded.company_id, res.id, now=now)
    if item.confirmation_code is not None:
        await _set_fixed_code(db, res, item.confirmation_code)
    return res


async def seed_scenario(db: AsyncSession, scenario: Scenario,
                        voice_knobs: dict[str, Any] | None = None) -> SeededScenario:
    """Create the scenario's company/region/location (unique slug), floor,
    rules and existing reservations, and commit."""
    company_id, region_id, location_id = (
        generate_short_id(), generate_short_id(), generate_short_id())
    slug = f"eval-{scenario.id.replace('_', '-')}-{generate_short_id()}"
    db.add(Company(id=company_id, name=f"Eval {scenario.title}", slug=slug))
    await db.flush()
    db.add(Region(id=region_id, company_id=company_id, name="Eval"))
    await db.flush()
    db.add(Location(id=location_id, company_id=company_id, region_id=region_id,
                    name=scenario.location.name, timezone=scenario.location.timezone))
    await db.flush()
    seeded = SeededScenario(company_id=company_id, location_id=location_id, slug=slug)

    await save_floor(db, company_id, location_id, scenario.floor_in(), now=scenario.now)
    await save_rules(db, company_id, location_id, scenario.rules_schema(voice_knobs))
    for item in scenario.existing:
        await _seed_existing(db, scenario, seeded, item)
    await db.commit()
    return seeded
