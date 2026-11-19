"""One day of the AI host analytics job (#259 step 10): extract, land, check,
then replace that day's agent_daily_metrics.

Order matters. The lake is landed before the checks run, so on a quality
failure the day's partitions are already written (and replaced on the
rerun); only the metrics are withheld: the day's existing
agent_daily_metrics rows are left as they were, nothing new is written,
and the operator is alerted. The caller exits 1.

Idempotent: rerunning a day replaces its partitions (lake.write_partition)
and deletes and reinserts its metrics rows in one transaction.
"""

import logging
from datetime import date
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.analytics import extract, lake, metrics, quality
from backend.analytics.models import AgentDailyMetric
from backend.analytics.schemas import TABLES
from backend.database import cross_tenant_session
from backend.models.location import Location
from backend.reservations.models import ReservationRules
from backend.reservations.rules import RulesSchema
from backend.services.operator_alerts import send_analytics_quality_alert

logger = logging.getLogger(__name__)


def _locations(extracted: dict[str, list[dict[str, Any]]]) -> list[str]:
    return sorted({r["location_id"] for rows in extracted.values() for r in rows
                   if r.get("location_id")})


async def load_rules(owner: AsyncSession,
                     location_ids: list[str]) -> dict[str, metrics.LocationRules]:
    """Each location's rules (defaults when it has none or they no longer
    validate) and time zone."""
    if not location_ids:
        return {}
    tzs = dict((await owner.execute(
        select(Location.id, Location.timezone).where(Location.id.in_(location_ids)))).all())
    stored = dict((await owner.execute(
        select(ReservationRules.location_id, ReservationRules.rules)
        .where(ReservationRules.location_id.in_(location_ids)))).all())
    out: dict[str, metrics.LocationRules] = {}
    for loc in location_ids:
        try:
            rules = RulesSchema.model_validate(stored[loc]) if loc in stored else RulesSchema()
        except ValidationError:
            logger.warning("analytics: invalid rules at location %s, periods unknown", loc)
            rules = RulesSchema()
        try:
            tz = ZoneInfo(tzs.get(loc) or "UTC")
        except (KeyError, ValueError):
            tz = ZoneInfo("UTC")
        out[loc] = metrics.LocationRules(rules=rules, tz=tz)
    return out


async def run(day: date, sink: lake.Sink, db: AsyncSession, *,
              runs_dir: Path | None = extract.RUNS_DIR) -> dict[str, Any]:
    """Process UTC `day`. Returns a summary with no PII: `ok`, the landed
    row counts, the quality failures and the metrics rows written."""
    extracted = await extract.day(db, day, runs_dir=runs_dir)
    landed = {t: lake.write_partition(t, day, extracted[t], schema, sink=sink)
              for t, schema in TABLES.items() if t in extracted}
    failures = await quality.check(day, extracted, landed, db)
    summary: dict[str, Any] = {"day": day.isoformat(), "landed": landed,
                               "skipped": extract.skipped(extracted),
                               "failures": failures, "metrics_rows": 0, "ok": not failures}
    if failures:
        logger.error("analytics %s: %d quality failure(s), lake landed, metrics not written",
                     day, len(failures))
        send_analytics_quality_alert(day.isoformat(), failures)
        return summary

    async with cross_tenant_session(db) as owner:
        try:
            rules = await load_rules(owner, _locations(extracted))
            rows = metrics.compute(extracted, rules, day=day)
            await owner.execute(delete(AgentDailyMetric).where(AgentDailyMetric.day == day))
            owner.add_all([AgentDailyMetric(**r) for r in rows])
            await owner.commit()
        except BaseException:
            await owner.rollback()
            raise
    summary["metrics_rows"] = len(rows)
    logger.info("analytics %s: landed %s, %d metrics row(s)", day,
                ", ".join(f"{t}={n}" for t, n in landed.items()), len(rows))
    return summary
