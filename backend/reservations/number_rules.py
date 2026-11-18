"""Managers' number lists (#259 step 7): allow and block entries for caller
numbers, "Mark as spam" from a conversation, and the screening counts.

The router authorizes (LocationScope; company-wide entries are admin-only to
change) and calls these. Whether an entry applies to a call is
`screening.decide`; whether it is active is `screening.rule_active`, the one
definition both use.

Expiry. A manager picks a local date ("expires on"); the entry stops
applying at the end of that day, stored as `expires_at` = 23:59:59.999 that
day in the zone of the entry's location. A company-wide entry has no
location, so it uses the zone of the company's first active location by
name (then id), or UTC when there is none. `expires_on` read back is the
same date in the same zone.

Uniqueness. One active entry per scope (a location, or company-wide) and
number, whatever its action: an allow and a block for the same number at
the same scope would contradict each other. "Active" moves with the clock,
so no index can hold it; create and edit take a transaction-scoped advisory
lock per company and number, and the check runs under it. Entries that
never expire also have a partial unique index (uq_number_rules_permanent)
as the backstop.

A location block under an active allow (that location's or company-wide)
would never apply, since an allow wins (screening.decide): creating one, by
POST or Mark as spam, is NumberAllowed (409 number_allowed, with the scope
of the allow).

Number rules are diner data: diner_data exports and erases them. Deleting
one is a hard delete (a rule is not history; screened_calls keep only the
hashed caller).
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Location, User
from backend.reservations.agent.models import AgentConversation
from backend.reservations.booking import ReservationError
from backend.reservations.screening import (
    BLOCK, active_rule_clause, rule_active, spam_markable, valid_e164,
)
from backend.reservations.screening_models import NumberRule, ScreenedCall
from backend.services.archive import active
from backend.services.location_scope import LocationScope

STATS_DAYS = 7
_END_OF_DAY = time(23, 59, 59, 999000)


class DuplicateRule(ReservationError):
    code = "duplicate_rule"


class InvalidNumber(ReservationError):
    code = "invalid_phone"


class ExpiryInPast(ReservationError):
    code = "expiry_in_past"


class NoCallerNumber(ReservationError):
    code = "no_caller_number"


class NumberAllowed(ReservationError):
    """A location block (a POST or Mark as spam) under an active allow for
    the number, at that location (scope "here") or company-wide (scope
    "all_locations"): the allow would win, so the block would do nothing.
    Remove the allow first."""
    code = "number_allowed"

    def __init__(self, scope: str) -> None:
        super().__init__(f"this number is on an allow list ({scope}); remove it first")
        self.scope = scope

    @property
    def extra(self) -> dict[str, str]:
        return {"scope": self.scope}


@dataclass(frozen=True)
class RuleView:
    rule: NumberRule
    expires_on: date | None
    created_by_name: str | None
    active: bool


async def rule_zone(db: AsyncSession, company_id: str, location_id: str | None) -> ZoneInfo:
    """The zone an entry's expiry date is read in (see the module doc)."""
    q = select(Location.timezone).where(Location.company_id == company_id)
    if location_id is not None:
        q = q.where(Location.id == location_id)
    else:
        # A company-wide entry has no location of its own. Its "end of day"
        # uses the company's first active location by name (then id): stable
        # across reads and, for the usual single-zone company, the zone the
        # manager means. With no active location, UTC. Ruled by the
        # controller (#259 step 7, task 4); a per-company zone would replace it.
        q = q.where(active(Location)).order_by(Location.name, Location.id).limit(1)
    tz = (await db.execute(q)).scalar_one_or_none()
    return ZoneInfo(tz) if tz else ZoneInfo("UTC")


def end_of_day(day: date, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, _END_OF_DAY, tzinfo=zone).astimezone(timezone.utc)


async def views(db: AsyncSession, company_id: str, rules: list[NumberRule],
                 now: datetime) -> list[RuleView]:
    user_ids = sorted({r.created_by_user_id for r in rules if r.created_by_user_id})
    names: dict[str, str] = {}
    if user_ids:
        names = dict((await db.execute(
            select(User.id, User.full_name).where(
                User.id.in_(user_ids), User.company_id == company_id)
        )).all())
    zones: dict[str | None, ZoneInfo] = {}
    out = []
    for r in rules:
        expires_on = None
        if r.expires_at is not None:
            if r.location_id not in zones:
                zones[r.location_id] = await rule_zone(db, company_id, r.location_id)
            expires_on = r.expires_at.astimezone(zones[r.location_id]).date()
        out.append(RuleView(r, expires_on, names.get(r.created_by_user_id or ""),
                            rule_active(r, now)))
    return out


async def view(db: AsyncSession, rule: NumberRule, now: datetime) -> RuleView:
    return (await views(db, rule.company_id, [rule], now))[0]


async def list_rules(db: AsyncSession, company_id: str, scope: LocationScope,
                     location_id: str | None, now: datetime) -> list[RuleView]:
    """One location's entries plus the company-wide ones, or (no location)
    every entry in scope plus the company-wide ones. Newest first."""
    where = NumberRule.location_id == location_id if location_id is not None \
        else scope.clause(NumberRule.location_id)
    rules = list((await db.execute(
        select(NumberRule)
        .where(NumberRule.company_id == company_id,
               or_(NumberRule.location_id.is_(None), where))
        .order_by(NumberRule.created_at.desc(), NumberRule.id)
    )).scalars().all())
    return await views(db, company_id, rules, now)


async def get_rule(db: AsyncSession, company_id: str, rule_id: str) -> NumberRule | None:
    return (await db.execute(select(NumberRule).where(
        NumberRule.id == rule_id, NumberRule.company_id == company_id)
    )).scalar_one_or_none()


async def _lock_number(db: AsyncSession, company_id: str, e164: str) -> None:
    await db.execute(select(func.pg_advisory_xact_lock(
        func.hashtextextended(f"number_rules:{company_id}:{e164}", 0))))


async def _active_at_scope(db: AsyncSession, company_id: str, location_id: str | None,
                           e164: str, now: datetime,
                           exclude_id: str | None = None) -> NumberRule | None:
    scope_match = NumberRule.location_id.is_(None) if location_id is None \
        else NumberRule.location_id == location_id
    q = select(NumberRule).where(
        NumberRule.company_id == company_id, NumberRule.e164 == e164, scope_match,
        active_rule_clause(now))
    if exclude_id is not None:
        q = q.where(NumberRule.id != exclude_id)
    return (await db.execute(q.order_by(NumberRule.created_at, NumberRule.id).limit(1))
            ).scalar_one_or_none()


async def _allowed_over(db: AsyncSession, company_id: str, location_id: str,
                        e164: str, now: datetime) -> str | None:
    """"here" or "all_locations" when an active allow for the number covers
    the location (a location allow first), else None."""
    scopes = set((await db.execute(
        select(NumberRule.location_id).where(
            NumberRule.company_id == company_id, NumberRule.e164 == e164,
            NumberRule.action == "allow", active_rule_clause(now),
            or_(NumberRule.location_id == location_id, NumberRule.location_id.is_(None)))
    )).scalars().all())
    if location_id in scopes:
        return "here"
    if None in scopes:
        return "all_locations"
    return None


async def _flush_unique(db: AsyncSession) -> None:
    """Flush; the permanent-entry unique index (uq_number_rules_permanent)
    is a DuplicateRule."""
    try:
        await db.flush()
    except IntegrityError as e:
        if "uq_number_rules_permanent" in str(e.orig):
            raise DuplicateRule("an active entry for this number already exists here") from e
        raise


async def _expires_at(db: AsyncSession, company_id: str, location_id: str | None,
                      expires_on: date | None, now: datetime) -> datetime | None:
    if expires_on is None:
        return None
    at = end_of_day(expires_on, await rule_zone(db, company_id, location_id))
    if at <= now:
        raise ExpiryInPast("the expiry date has already passed")
    return at


async def create_rule(db: AsyncSession, company_id: str, *, e164: str, action: str,
                      location_id: str | None, expires_on: date | None,
                      note: str | None, user_id: str | None,
                      now: datetime) -> NumberRule:
    """Flushes; the caller commits (which releases the lock)."""
    if not valid_e164(e164):
        raise InvalidNumber("number must be E.164, e.g. +12125550100")
    expires_at = await _expires_at(db, company_id, location_id, expires_on, now)
    await _lock_number(db, company_id, e164)
    if action == BLOCK and location_id is not None:
        allowed = await _allowed_over(db, company_id, location_id, e164, now)
        if allowed:
            raise NumberAllowed(allowed)
    if await _active_at_scope(db, company_id, location_id, e164, now):
        raise DuplicateRule("an active entry for this number already exists here")
    rule = NumberRule(company_id=company_id, location_id=location_id, e164=e164,
                      action=action, expires_at=expires_at, note=note or None,
                      created_by_user_id=user_id, created_at=now)
    db.add(rule)
    await _flush_unique(db)
    return rule


_UNSET = object()


async def update_rule(db: AsyncSession, rule: NumberRule, *, action: str | None,
                      expires_on: "date | None | object" = _UNSET,
                      note: "str | None | object" = _UNSET,
                      now: datetime) -> NumberRule:
    """Action, expiry and note only; scope and number never change (delete
    and recreate). `_UNSET` leaves a field as it is, and so does an
    `expires_on` equal to the current one (an expired entry stays editable:
    its past date is not re-checked). Flushes."""
    if action is not None:
        rule.action = action
    if note is not _UNSET:
        rule.note = note or None  # type: ignore[assignment]
    if expires_on is not _UNSET and expires_on == (await view(db, rule, now)).expires_on:
        expires_on = _UNSET
    if expires_on is not _UNSET:
        new_at = await _expires_at(
            db, rule.company_id, rule.location_id, expires_on, now)  # type: ignore[arg-type]
        await _lock_number(db, rule.company_id, rule.e164)
        # Checked before the row changes, so autoflush cannot hit the
        # unique index first.
        with db.no_autoflush:
            clash = (new_at is None or new_at > now) and await _active_at_scope(
                db, rule.company_id, rule.location_id, rule.e164, now, exclude_id=rule.id)
        if clash:
            raise DuplicateRule("an active entry for this number already exists here")
        rule.expires_at = new_at
    await _flush_unique(db)
    return rule


async def delete_rule(db: AsyncSession, rule: NumberRule) -> None:
    await db.delete(rule)
    await db.flush()


def spam_note(conversation_id: str) -> str:
    return f"Marked as spam from conversation {conversation_id}"


async def mark_spam(db: AsyncSession, conv: AgentConversation, user_id: str | None,
                    now: datetime) -> tuple[NumberRule, bool]:
    """A permanent block at the conversation's location for its caller.
    Idempotent: an active block there is returned as it is (created False).
    An active allow there or company-wide is NumberAllowed. Flushes."""
    caller = conv.caller_number
    if not spam_markable(conv):
        raise NoCallerNumber("only a phone call with a caller number can be marked as spam")
    assert caller is not None
    await _lock_number(db, conv.company_id, caller)
    allowed = await _allowed_over(db, conv.company_id, conv.location_id, caller, now)
    if allowed:
        raise NumberAllowed(allowed)
    existing = await _active_at_scope(db, conv.company_id, conv.location_id, caller, now)
    if existing is not None:
        return existing, False
    rule = NumberRule(company_id=conv.company_id, location_id=conv.location_id,
                      e164=caller, action=BLOCK, expires_at=None,
                      note=spam_note(conv.id), created_by_user_id=user_id,
                      created_at=now)
    db.add(rule)
    await _flush_unique(db)
    return rule, True


@dataclass(frozen=True)
class ScreeningStats:
    blocked_list: int
    blocked_ftc: int
    answered: int


async def screening_stats(db: AsyncSession, company_id: str, scope: LocationScope,
                          location_id: str | None, now: datetime) -> ScreeningStats:
    """Screening decisions over the last STATS_DAYS days, at one location or
    every location in scope."""
    where = ScreenedCall.location_id == location_id if location_id is not None \
        else scope.clause(ScreenedCall.location_id)
    rows = (await db.execute(
        select(ScreenedCall.decision, ScreenedCall.reason, func.count())
        .where(ScreenedCall.company_id == company_id, where,
               ScreenedCall.created_at >= now - timedelta(days=STATS_DAYS))
        .group_by(ScreenedCall.decision, ScreenedCall.reason)
    )).all()
    counts = {(d, r): n for d, r, n in rows}
    return ScreeningStats(
        blocked_list=counts.get((BLOCK, "block_list"), 0),
        blocked_ftc=counts.get((BLOCK, "ftc"), 0),
        answered=sum(n for (d, _r), n in counts.items() if d != BLOCK),
    )
