"""Activation funnel milestones, recorded once per ownership group.

    signup -> first_location -> first_employee -> first_generation -> upgraded

Companion to services/signup_signals.py (which observes signup-time signals)
and services/abuse_report.py (which reports on them): this module marks a
group's progress through the funnel so free-to-paid activation can actually
be measured instead of guessed at. See backend/scripts/
run_activation_report.py for the read side. Nothing in this repo may read
activation_events to gate or change product behavior — it is report-only,
the same posture the other two modules commit to.

record_milestone must never fail the request that calls it, and must never
touch the caller's own session: it opens its OWN short-lived AsyncSession
bound to the same engine, does one dialect-aware `INSERT ... ON CONFLICT DO
NOTHING` keyed on the unique (ownership_group_id, event) pair, commits, and
closes. Every failure — including "already recorded" — is caught inside
that private session and logged; nothing propagates out. This is
deliberate: an earlier version reused the caller's session and relied on
catching IntegrityError from a SAVEPOINT, but a flush failure (even one
contained by a SAVEPOINT) leaves the *Session* itself needing an explicit
rollback() before its next statement, and rollback() on an AsyncSession
expires every attribute of every object already loaded in it — including
the caller's. Every one of the five call sites keeps using its own session
and its own ORM objects immediately after calling this, so a private
session is the only way to guarantee they are never disturbed.

Call sites should invoke this AFTER their own primary write has committed
successfully, so a milestone is only ever recorded for a row that durably
exists.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.activation_event import ACTIVATION_EVENTS, ActivationEvent

logger = logging.getLogger(__name__)


async def record_milestone(
    db: AsyncSession,
    ownership_group_id: str | None,
    event: str,
    *,
    user_id: str | None = None,
) -> None:
    """Record *event* for *ownership_group_id*, once.

    Safe to call every time the triggering action happens: the first call
    for a (group, event) pair inserts a row, every later one is a no-op.
    Runs on its own private session — never flushes, commits, rolls back,
    or expires anything on *db*. Never raises.
    """
    if not ownership_group_id:
        # No ownership group (seed/dev data, or a demo tenant) — nothing to
        # attribute this to. Not an error.
        return
    if event not in ACTIVATION_EVENTS:
        logger.error("activation.unknown_event event=%s", event)
        return

    try:
        engine = db.bind
        dialect_name = getattr(getattr(engine, "dialect", None), "name", "")
        if dialect_name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as _insert
        else:
            # Covers the sqlite test database, and anything else that isn't
            # postgres, the same way: on_conflict_do_nothing() is supported
            # identically by both dialects' Insert constructs.
            from sqlalchemy.dialects.sqlite import insert as _insert

        stmt = (
            _insert(ActivationEvent)
            .values(
                ownership_group_id=ownership_group_id,
                event=event,
                user_id=user_id,
                occurred_at=datetime.now(timezone.utc),
            )
            .on_conflict_do_nothing(
                index_elements=["ownership_group_id", "event"],
            )
        )

        async with AsyncSession(bind=engine, expire_on_commit=False) as session:
            await session.execute(stmt)
            await session.commit()
    except Exception:
        logger.exception(
            "activation.record_failed group=%s event=%s",
            ownership_group_id, event,
        )
