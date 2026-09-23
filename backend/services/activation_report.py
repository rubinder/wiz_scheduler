"""Weekly activation-funnel cohort report.

Companion to services/activation.py, which writes the milestone rows this
reads. For each of the last *weeks* weekly signup cohorts (grouped by
`ownership_groups.created_at`, most recent first), reports how many groups
reached each funnel milestone, what percentage of the cohort that is, and
the median hours from signup to reaching it.

REPORTS ONLY. Nothing here acts on the funnel or changes product behavior,
the same posture as services/abuse_report.py.
"""
from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.activation_event import ACTIVATION_EVENTS, ActivationEvent
from backend.models.ownership_group import OwnershipGroup

logger = logging.getLogger(__name__)


class MilestoneStats(TypedDict):
    count: int
    pct: float
    median_hours: float | None


class CohortReport(TypedDict):
    week_start: str
    week_end: str
    signups: int
    milestones: dict[str, MilestoneStats]


def _hours_between(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds() / 3600.0


async def build_activation_report(
    db: AsyncSession, *, weeks: int = 8
) -> dict[str, Any]:
    """Build the weekly-cohort activation funnel report.

    *weeks* is how many most-recent weekly signup cohorts to include. Week
    boundaries are 7-day buckets counted back from "now" (always UTC — see
    CLAUDE.md), not calendar weeks, so the report is stable regardless of
    what day it runs.
    """
    now = datetime.now(timezone.utc)

    cohorts: list[CohortReport] = []

    for i in range(weeks):
        week_end = now - timedelta(days=7 * i)
        week_start = week_end - timedelta(days=7)

        groups = (await db.execute(
            select(OwnershipGroup).where(
                OwnershipGroup.created_at >= week_start,
                OwnershipGroup.created_at < week_end,
            )
        )).scalars().all()

        signups = len(groups)
        milestones: dict[str, MilestoneStats] = {}

        if signups == 0:
            for event in ACTIVATION_EVENTS:
                milestones[event] = MilestoneStats(
                    count=0, pct=0.0, median_hours=None
                )
            cohorts.append(CohortReport(
                week_start=week_start.isoformat(),
                week_end=week_end.isoformat(),
                signups=0,
                milestones=milestones,
            ))
            continue

        og_ids = [g.id for g in groups]
        rows = (await db.execute(
            select(ActivationEvent).where(
                ActivationEvent.ownership_group_id.in_(og_ids)
            )
        )).scalars().all()

        # event -> {og_id: occurred_at}
        by_event: dict[str, dict[str, datetime]] = {e: {} for e in ACTIVATION_EVENTS}
        for row in rows:
            by_event[row.event][row.ownership_group_id] = row.occurred_at

        # Origin for "hours from signup": the recorded signup event when
        # present, else the group's own created_at (covers a group whose
        # signup row predates this feature and was never backfilled).
        origin: dict[str, datetime] = dict(by_event.get("signup", {}))
        for g in groups:
            origin.setdefault(g.id, g.created_at)

        for event in ACTIVATION_EVENTS:
            reached = by_event.get(event, {})
            count = len(reached)
            hours: list[float] = []
            for og_id, occurred_at in reached.items():
                start = origin.get(og_id)
                if start is None:
                    continue
                hours.append(_hours_between(start, occurred_at))

            milestones[event] = MilestoneStats(
                count=count,
                pct=round((count / signups) * 100, 1) if signups else 0.0,
                median_hours=(
                    round(statistics.median(hours), 1) if hours else None
                ),
            )

        cohorts.append(CohortReport(
            week_start=week_start.isoformat(),
            week_end=week_end.isoformat(),
            signups=signups,
            milestones=milestones,
        ))

    report = {
        "generated_at": now.isoformat(),
        "weeks": weeks,
        "cohorts": cohorts,
        "note": (
            "Reports only. Nothing acts on this data or changes product "
            "behavior. Cohorts are 7-day buckets of ownership_groups."
            "created_at counted back from now, most recent first."
        ),
    }

    logger.info(
        "activation_report.generated weeks=%d cohorts=%d",
        weeks, len(cohorts),
    )
    return report
