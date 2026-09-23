"""Activation funnel milestones, recorded once per ownership group.

    signup -> first_location -> first_employee -> first_generation -> upgraded

Companion to services/signup_signals.py (which observes signup-time signals)
and services/abuse_report.py (which reports on them): this module marks a
group's progress through the funnel so free-to-paid activation can actually
be measured instead of guessed at. See backend/scripts/
run_activation_report.py for the read side. Nothing in this repo may read
activation_events to gate or change product behavior — it is report-only,
the same posture the other two modules commit to.

record_milestone must never fail the request that calls it: every failure
is caught, logged, and swallowed. Idempotency comes from the unique
(ownership_group_id, event) constraint on activation_events — a repeat call
for a milestone already recorded is a no-op. The insert is wrapped in a
SAVEPOINT (the same pattern services/schedule_lock.py uses for its
UNIQUE(company_id) race), and an IntegrityError on conflict is caught; it
works identically against the Postgres runtime and the SQLite test
database, unlike a dialect-specific ON CONFLICT clause.

A flush failure — even one caught and contained by a SAVEPOINT — leaves the
SQLAlchemy Session itself in a "deactivated" state that raises
PendingRollbackError on the next operation until Session.rollback() is
called; that rollback is not optional; the SAVEPOINT only bounds what it
undoes at the database level. On an AsyncSession, rollback() also expires
every attribute of every object already loaded in the session, caller's
objects included. That's why every one of the five call sites (register,
create_location, create_employee, the generate stream, confirm-upgrade) is
written to call record_milestone LAST — after everything it needs from its
own ORM objects has already been read into plain values — so a rollback in
here can never break the response being built around it. Call sites must
also invoke this AFTER their own primary write has committed successfully:
record_milestone commits its own insert as an independent unit of work, so
calling it mid-transaction would prematurely commit whatever else is
pending on the same session.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
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
    Never raises.
    """
    if not ownership_group_id:
        # No ownership group (seed/dev data, or a demo tenant) — nothing to
        # attribute this to. Not an error.
        return
    if event not in ACTIVATION_EVENTS:
        logger.error("activation.unknown_event event=%s", event)
        return

    row = ActivationEvent(
        ownership_group_id=ownership_group_id,
        event=event,
        user_id=user_id,
        occurred_at=datetime.now(timezone.utc),
    )
    try:
        db.add(row)
        async with db.begin_nested():
            await db.flush()
        await db.commit()
    except IntegrityError:
        # Already recorded — the unique constraint is the arbiter. The
        # SAVEPOINT already rolled back the failed insert at the database
        # level, but the Session itself still needs an explicit rollback()
        # to clear the flush-failure state it now carries (see module
        # docstring) — every call site is written to tolerate the resulting
        # attribute expiration.
        try:
            await db.rollback()
        except Exception:
            pass
    except Exception:
        logger.exception(
            "activation.record_failed group=%s event=%s",
            ownership_group_id, event,
        )
        try:
            await db.rollback()
        except Exception:
            pass
