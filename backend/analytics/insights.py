"""The in-app Insights dashboard's reads (#259 step 10): one location's
agent_daily_metrics rows over an inclusive UTC date range. Rows are returned
raw, without gap-filling; ratios and 7-day comparisons are the page's job,
computed from the summed counts."""

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.analytics.models import AgentDailyMetric

DEFAULT_DAYS = 30
MAX_DAYS = 180


class InvalidRange(ValueError):
    code = "invalid_range"


def insights_range(start: date | None, end: date | None, today: date) -> tuple[date, date]:
    """The inclusive range to read. `end` defaults to yesterday (the newest
    day the nightly job can have written) and `start` to 30 days ending
    there. Raises InvalidRange when start > end or the span is over 180
    days."""
    end = end or today - timedelta(days=1)
    start = start or end - timedelta(days=DEFAULT_DAYS - 1)
    if start > end:
        raise InvalidRange("from must not be after to")
    if (end - start).days + 1 > MAX_DAYS:
        raise InvalidRange(f"The range can be at most {MAX_DAYS} days")
    return start, end


async def daily_metrics(
    db: AsyncSession, company_id: str, location_id: str, start: date, end: date
) -> list[AgentDailyMetric]:
    """The location's rows from `start` to `end` inclusive, oldest first."""
    return list((await db.execute(
        select(AgentDailyMetric).where(
            AgentDailyMetric.company_id == company_id,
            AgentDailyMetric.location_id == location_id,
            AgentDailyMetric.day >= start,
            AgentDailyMetric.day <= end,
        ).order_by(AgentDailyMetric.day)
    )).scalars())
