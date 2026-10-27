"""Which locations a scheduling run touches (#180).

The graph picks the locations to schedule from RECURRING templates: those
matching `template_ids`, or every recurring template when none are given.
The generate route needs the same answer up front, to know which locations
to lock, so both call `recurring_templates_by_location` and the two cannot
drift apart.
"""
from __future__ import annotations

from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import ShiftTemplate


async def recurring_templates_by_location(
    db: AsyncSession, company_id: str, template_ids: List[str] | None,
) -> Dict[str, List[str]]:
    """location_id -> the recurring template ids the run will use there.

    `template_ids` falsy means "every recurring template of the company".
    Ids that are not recurring templates of this company simply match
    nothing. The graph schedules every active location when the result is
    empty (see `_load_initial_state`).
    """
    query = select(ShiftTemplate.id, ShiftTemplate.location_id).where(
        ShiftTemplate.company_id == company_id,
        ShiftTemplate.specific_date.is_(None),
    )
    if template_ids:
        query = query.where(ShiftTemplate.id.in_(template_ids))
    location_to_template_ids: Dict[str, List[str]] = {}
    for template_id, location_id in (await db.execute(query)).all():
        location_to_template_ids.setdefault(str(location_id), []).append(str(template_id))
    return location_to_template_ids


async def generation_lock_seed(
    db: AsyncSession, company_id: str, template_ids: List[str] | None,
) -> set[str] | None:
    """The lock seed for a generate run: the locations it will schedule.

    None means "the whole company", which is what a run with no template
    filter is (acceptance criterion 3 of #180), and also what a filter
    matching no recurring template becomes: the graph then falls back to
    every active location. A restricted manager never reaches this with
    None: the route has already replaced "no filter" with their in-scope
    template ids.
    """
    if not template_ids:
        return None
    by_location = await recurring_templates_by_location(db, company_id, template_ids)
    return set(by_location) or None
