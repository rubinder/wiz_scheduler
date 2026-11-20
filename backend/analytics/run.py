"""One day of the AI host analytics job (#259 step 10): extract, land, check,
then replace that day's agent_daily_metrics.

Order matters. The lake is landed before the checks run, so on a quality
failure the day's partitions are already written (and replaced on the
rerun); only the metrics are withheld: the day's existing
agent_daily_metrics rows are left as they were, nothing new is written,
and the operator is alerted. The caller exits 1.

Idempotent: rerunning a day replaces its partitions (lake.write_partition)
and deletes and reinserts its metrics rows in one transaction.

Two runs of the same day never overlap: the whole run holds a session
advisory lock keyed on (hashtext('agent_daily_metrics'), day ordinal) on a
connection of its own, so the lake writes are serialised too, and the
metrics transaction also takes the transaction-level lock on that key
before its delete. A second run of the day waits for the first.
"""

import logging
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any, AsyncIterator

from pydantic import ValidationError
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from backend.analytics import extract, lake, metrics, quality
from backend.analytics.models import AgentDailyMetric
from backend.analytics.schemas import TABLES
from backend.tenant import tenant_scope_disabled
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
        out[loc] = metrics.LocationRules(rules=rules, tz=metrics.local_zone(tzs.get(loc)))
    return out


LOCK_KEY = "agent_daily_metrics"


@asynccontextmanager
async def day_lock(db: AsyncSession, day: date) -> AsyncIterator[AsyncConnection]:
    """Hold the day's session advisory lock on a dedicated connection, and
    yield it: the metrics are written on it, so the transaction-level lock
    on the same key (re-entrant within one session) does not wait on it."""
    params = {"key": LOCK_KEY, "day": day.toordinal()}
    async with db.bind.connect() as conn:
        await conn.execute(text("SELECT pg_advisory_lock(hashtext(:key), :day)"), params)
        await conn.commit()
        try:
            yield conn
        finally:
            # A failing unlock must not mask the body's own error. Invalidate
            # the connection instead of returning it to the pool: closing
            # the session releases its advisory lock.
            try:
                await conn.execute(text("SELECT pg_advisory_unlock(hashtext(:key), :day)"),
                                   params)
                await conn.commit()
            except Exception as exc:
                logger.error("analytics %s: advisory unlock failed (%s), connection invalidated",
                             day, type(exc).__name__)
                await conn.invalidate()


async def run(day: date, sink: lake.Sink, db: AsyncSession, *,
              runs_dir: Path | None = extract.RUNS_DIR) -> dict[str, Any]:
    """Process UTC `day` under its advisory lock. Returns a summary with no
    PII: `ok`, the landed row counts, the quality failures and the metrics
    rows written."""
    async with day_lock(db, day) as conn:
        return await _run(day, sink, db, conn, runs_dir=runs_dir)


async def _run(day: date, sink: lake.Sink, db: AsyncSession, conn: AsyncConnection, *,
               runs_dir: Path | None) -> dict[str, Any]:
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

    # The owner session (no RLS binding), on the locked connection.
    with tenant_scope_disabled():
        async with AsyncSession(bind=conn, expire_on_commit=False) as owner:
            summary["metrics_rows"] = await _write_metrics(owner, day, extracted)
    logger.info("analytics %s: landed %s, %d metrics row(s)", day,
                ", ".join(f"{t}={n}" for t, n in landed.items()), summary["metrics_rows"])
    return summary


async def _write_metrics(owner: AsyncSession, day: date,
                         extracted: dict[str, list[dict[str, Any]]]) -> int:
    """Delete and reinsert the day's metrics in one transaction, under the
    day's transaction-level advisory lock. Returns the rows written."""
    try:
        rules = await load_rules(owner, _locations(extracted))
        rows = metrics.compute(extracted, rules, day=day)
        await owner.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key), :day)"),
                            {"key": LOCK_KEY, "day": day.toordinal()})
        await owner.execute(delete(AgentDailyMetric).where(AgentDailyMetric.day == day))
        owner.add_all([AgentDailyMetric(**r) for r in rows])
        await owner.commit()
    except BaseException:
        await owner.rollback()
        raise
    return len(rows)
