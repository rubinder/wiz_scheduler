"""Phone numbers for the AI host's voice channel (#259 step 6).

One `VoiceNumber` per location, owned by the location's ownership group and
following its AI host subscription (`ensure_for_group`):

- subscription answering (ANSWERING_STATUSES): every active location gets a
  number. A new row starts `requested`; the number is searched, claimed in
  Amazon Connect, routed to the AI host contact flow, and becomes `active`.
  A number in its release hold (`releasing`) is reactivated as it is, with
  no new claim. An archived location's number goes on hold.
- anything else (no subscription, ended, unpaid, paused): a claimed number
  goes to `releasing` with `release_after = now + VOICE_RELEASE_HOLD_DAYS`;
  a row that never got a phone number id is simply `released`. A row with a
  connect_phone_number_id is never moved straight to `released`: only
  `release_due` does that, after Connect has released it.

Who claims. The Stripe webhook only records `requested` rows and holds
(`claim=False`); checkout confirm and the daily step (`run_daily`) claim.
`run_daily` is also the retry: a Connect failure is logged and the row
stays `requested`; a row still `requested` after a day is reported as an
error (the daily script mails the operator).

No orphaned claims. A claim is three committed steps on the row, each under
the row's lock (FOR UPDATE SKIP LOCKED, so a concurrent run skips the row):
search a number and store its e164; claim that e164 with the row's
ClientToken and store the phone number id; route it and mark it `active`.
When claim_phone_number fails, connect_numbers.find_owned (ListPhoneNumbersV2)
tells a claim whose answer was lost (owned: store its id and route it) from
one that never happened (not owned: clear the e164, search again next run).
If that check fails too, the row keeps its e164 and the retry repeats the
same claim (same e164, same token), which Connect treats as idempotent. A
row with a searched e164 and no id is never treated as never claimed: a
hold sends it to `releasing`, and `release_due` (and group deletion)
resolve it through find_owned before giving it back. A released row has
its e164 cleared, so a number Connect offers again is never blocked by an
old row.

No lost updates. Holds, reactivations and the release itself are
compare-and-set: UPDATE ... WHERE id AND status = <seen> AND
connect_phone_number_id IS NOT DISTINCT FROM <seen>, re-read and retried on
a miss. A hold that meets a claim in flight waits for its row lock, then
sees the new phone number id and holds the number instead of dropping it.

Nothing calls Connect unless settings.VOICE_PROVISIONING_ENABLED is true;
with it off `ensure_for_group`, `release_due` and `run_daily` do nothing.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import cross_tenant_session
from backend.models.company import Company
from backend.models.location import Location
from backend.reservations.subscription import ANSWERING_STATUSES, for_group
from backend.reservations.subscription_models import ReservationSubscription
from backend.reservations.voice_number_models import VOICE_STATUSES, VoiceNumber
from backend.services import connect_numbers
from backend.services.archive import active
from backend.tenant import tenant_scope_disabled

__all__ = ["VOICE_STATUSES", "VoiceNumber", "ensure_for_group", "release_due",
           "run_daily", "location_for_number", "for_location",
           "release_for_deleted_group"]

logger = logging.getLogger(__name__)

# A number in these states still routes calls to its location.
ROUTED_STATUSES = ("active", "releasing")
DEFAULT_COUNTRY = "US"
STALE_REQUEST = timedelta(hours=24)
_CAS_TRIES = 3


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def _hold_until(now: datetime) -> datetime:
    return now + timedelta(days=settings.VOICE_RELEASE_HOLD_DAYS)


def client_token(row_id: str) -> str:
    return f"wizscheduler-voice-{row_id}"


async def _locked(db: AsyncSession, row_id: str) -> VoiceNumber | None:
    return (await db.execute(
        select(VoiceNumber).where(VoiceNumber.id == row_id)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )).scalar_one_or_none()


State = tuple[str, str | None, str | None]  # (status, phone number id, e164)


async def _state(db: AsyncSession, row_id: str) -> State | None:
    row = (await db.execute(
        select(VoiceNumber.status, VoiceNumber.connect_phone_number_id, VoiceNumber.e164)
        .where(VoiceNumber.id == row_id))).first()
    return (row[0], row[1], row[2]) if row else None


async def _cas(db: AsyncSession, row_id: str, seen: State, values: dict) -> bool:
    """Write `values` only if the row is still exactly `seen`."""
    status, pn, e164 = seen
    hit = (await db.execute(
        update(VoiceNumber)
        .where(VoiceNumber.id == row_id, VoiceNumber.status == status,
               VoiceNumber.connect_phone_number_id.is_not_distinct_from(pn),
               VoiceNumber.e164.is_not_distinct_from(e164))
        .values(**values)
        .returning(VoiceNumber.id)
        .execution_options(synchronize_session=False)
    )).scalar_one_or_none()
    return hit is not None


async def _hold(db: AsyncSession, row_id: str, now: datetime) -> str | None:
    """The location no longer wants its number: one that may be claimed (a
    phone number id, or a searched e164 whose claim may have gone through)
    goes on hold; one never searched is released. Commits. Returns the
    resulting status."""
    for _ in range(_CAS_TRIES):
        seen = await _state(db, row_id)
        if seen is None:
            await db.rollback()
            return None
        status, pn, e164 = seen
        if status in ("releasing", "released"):
            await db.rollback()
            return status
        if pn is None and e164 is None:
            target, values = "released", {"status": "released", "e164": None,
                                          "release_after": None}
        else:
            target, values = "releasing", {"status": "releasing",
                                           "release_after": _hold_until(now)}
        if await _cas(db, row_id, seen, values):
            await db.commit()
            return target
        await db.rollback()
    logger.warning("voice number %s: hold lost its race %d times; next run", row_id, _CAS_TRIES)
    return None


async def _reactivate(db: AsyncSession, row_id: str) -> str | None:
    """A number on hold comes back as it is. Commits. Returns the resulting
    status (`released` when the release won the race)."""
    for _ in range(_CAS_TRIES):
        seen = await _state(db, row_id)
        if seen is None:
            await db.rollback()
            return None
        if seen[0] != "releasing":
            await db.rollback()
            return seen[0]
        if await _cas(db, row_id, seen, {"status": "active", "release_after": None}):
            await db.commit()
            return "active"
        await db.rollback()
    return None


async def _active_locations(db: AsyncSession, og_id: str) -> list[Location]:
    return list((await db.execute(
        select(Location)
        .join(Company, Company.id == Location.company_id)
        .where(Company.ownership_group_id == og_id, active(Location))
        .order_by(Location.id)
    )).scalars().all())


async def _relock(db: AsyncSession, row_id: str) -> VoiceNumber | None:
    row = await _locked(db, row_id)
    if row is None or row.status != "requested":
        await db.rollback()
        return None
    return row


async def _provision(db: AsyncSession, row_id: str) -> str:
    """Search, claim and route the row's number, committing after each step
    (see the module docstring). Returns the row's status afterwards; a
    Connect failure is logged and leaves it `requested`."""
    row = await _relock(db, row_id)
    if row is None:
        state = await _state(db, row_id)
        await db.rollback()
        return state[0] if state else "skipped"
    location_id = row.location_id
    try:
        if row.e164 is None:
            found = await connect_numbers.search(DEFAULT_COUNTRY)
            if found == connect_numbers.DISABLED:
                await db.rollback()
                return "requested"
            row.e164 = found
            await db.commit()
            if (row := await _relock(db, row_id)) is None:
                return "skipped"
        if row.connect_phone_number_id is None:
            try:
                pn = await connect_numbers.claim_number(row.e164,
                                                        client_token=client_token(row_id))
            except Exception:  # noqa: BLE001 - did the claim go through?
                logger.exception("claim of %s for location %s failed; checking ownership",
                                 row.e164, location_id)
                pn = await connect_numbers.find_owned(row.e164)
                if pn is None:
                    # Not ours: search again next time.
                    row.e164 = None
                    await db.commit()
                    return "requested"
            if pn == connect_numbers.DISABLED:
                await db.rollback()
                return "requested"
            row.connect_phone_number_id = pn
            await db.commit()
            if (row := await _relock(db, row_id)) is None:
                return "skipped"
        if await connect_numbers.associate(row.connect_phone_number_id) == connect_numbers.DISABLED:
            await db.rollback()
            return "requested"
        row.status = "active"
        row.release_after = None
        await db.commit()
        return "active"
    except Exception:  # noqa: BLE001 - retried by the daily step
        logger.exception("voice number for location %s not provisioned; stays requested",
                         location_id)
        await db.rollback()
        return "requested"


async def _request(db: AsyncSession, og_id: str, company_id: str, location_id: str,
                   released_id: str | None) -> str | None:
    """A fresh `requested` row for the location (replacing its released
    row, if any). Commits. None when a concurrent run got there first."""
    try:
        if released_id is not None:
            gone = (await db.execute(
                delete(VoiceNumber)
                .where(VoiceNumber.id == released_id, VoiceNumber.status == "released")
                .returning(VoiceNumber.id)
                .execution_options(synchronize_session=False))).scalar_one_or_none()
            if gone is None:
                await db.rollback()
                return None
        row = VoiceNumber(company_id=company_id, ownership_group_id=og_id,
                          location_id=location_id, status="requested")
        db.add(row)
        await db.commit()
        return row.id
    except IntegrityError:
        await db.rollback()
        return None


async def ensure_for_group(db: AsyncSession, og_id: str, now: datetime | None = None,
                           *, claim: bool = True) -> dict:
    """Bring the group's numbers in line with its AI host subscription (see
    the module docstring). `claim=False` (the webhook) records `requested`
    rows and holds but calls Connect for nothing. Commits. Connect failures
    are logged, never raised; a database error propagates (callers in
    webhooks and checkout run this in their own session and catch it)."""
    if not connect_numbers.enabled():
        return {"og": og_id, "disabled": True}
    now = _now(now)
    out: dict = {"og": og_id, "claimed": [], "reactivated": [], "held": [], "pending": []}
    with tenant_scope_disabled():
        sub: ReservationSubscription | None = await for_group(db, og_id)
        answering = sub is not None and sub.status in ANSWERING_STATUSES
        # Plain values: every step below commits or rolls back.
        wanted = {loc.id: loc.company_id
                  for loc in (await _active_locations(db, og_id) if answering else [])}
        rows = {loc_id: (row_id, status) for row_id, loc_id, status in (await db.execute(
            select(VoiceNumber.id, VoiceNumber.location_id, VoiceNumber.status)
            .where(VoiceNumber.ownership_group_id == og_id)
            .order_by(VoiceNumber.location_id))).all()}
        await db.rollback()

        # Numbers no longer wanted (subscription ended, location archived).
        for loc_id, (row_id, status) in rows.items():
            if loc_id not in wanted and status in ("active", "requested"):
                if await _hold(db, row_id, now) is not None:
                    out["held"].append(loc_id)

        to_provision: list[tuple[str, str]] = []
        for loc_id, company_id in wanted.items():
            row_id, status = rows.get(loc_id, (None, None))
            if status == "releasing":
                status = await _reactivate(db, row_id)
                if status == "active":
                    out["reactivated"].append(loc_id)
            if status == "active" or (row_id is not None and status is None):
                continue
            if row_id is None or status == "released":
                row_id = await _request(db, og_id, company_id, loc_id, row_id)
                if row_id is None:
                    continue
            to_provision.append((loc_id, row_id))

        for loc_id, row_id in to_provision:
            status = await _provision(db, row_id) if claim else "requested"
            (out["claimed"] if status == "active" else out["pending"]).append(loc_id)
    return out


async def release_due(db: AsyncSession, now: datetime | None = None) -> dict:
    """Give back every number whose release hold is over (all groups).
    Commits per number; a Connect failure is logged and retried next run."""
    if not connect_numbers.enabled():
        return {"disabled": True}
    now = _now(now)
    out: dict = {"released": [], "errors": []}
    with tenant_scope_disabled():
        ids = (await db.execute(
            select(VoiceNumber.id).where(VoiceNumber.status == "releasing",
                                         VoiceNumber.release_after <= now)
            .order_by(VoiceNumber.id)
        )).scalars().all()
        for row_id in ids:
            row = await _locked(db, row_id)
            if row is None or row.status != "releasing" or row.release_after > now:
                await db.rollback()
                continue
            location_id = row.location_id
            try:
                pn = row.connect_phone_number_id
                if pn is None and row.e164:
                    # Searched; the claim may have gone through unseen.
                    pn = await connect_numbers.find_owned(row.e164)
                    if pn == connect_numbers.DISABLED:
                        await db.rollback()
                        continue
                if pn:
                    if await connect_numbers.release(pn) == connect_numbers.DISABLED:
                        await db.rollback()
                        continue
                row.status = "released"
                row.e164 = None  # Connect may offer it again, to anyone
                row.release_after = None
                await db.commit()
                out["released"].append(location_id)
            except Exception as e:  # noqa: BLE001 - retried next run
                logger.exception("voice number for location %s not released", location_id)
                await db.rollback()
                out["errors"].append({"location": location_id,
                                      "error": f"{type(e).__name__}: {str(e)[:200]}"})
    return out


async def run_daily(db: AsyncSession, now: datetime | None = None) -> dict:
    """The daily step: re-align (and claim for) every group that has a
    subscription or a number not yet on hold, release the numbers whose
    hold is over, and report rows still `requested` after a day. One
    group's failure never stops the others."""
    if not connect_numbers.enabled():
        return {"disabled": True}
    now = _now(now)
    out: dict = {"groups": [], "errors": []}
    with tenant_scope_disabled():
        og_ids = sorted(set((await db.execute(
            select(ReservationSubscription.ownership_group_id))).scalars().all())
            | set((await db.execute(
                select(VoiceNumber.ownership_group_id)
                .where(VoiceNumber.status.in_(("requested", "active"))))).scalars().all()))
        await db.rollback()
    for og_id in og_ids:
        try:
            out["groups"].append(await ensure_for_group(db, og_id, now))
        except Exception as e:  # noqa: BLE001 - one group never stops the run
            logger.exception("voice numbers daily run failed for og %s", og_id)
            await db.rollback()
            out["errors"].append({"og": og_id, "error": f"{type(e).__name__}: {str(e)[:200]}"})
    released = await release_due(db, now)
    out["released"] = released.get("released", [])
    out["errors"] += released.get("errors", [])
    with tenant_scope_disabled():
        stale = (await db.execute(
            select(VoiceNumber.ownership_group_id, VoiceNumber.location_id,
                   VoiceNumber.created_at)
            .where(VoiceNumber.status == "requested",
                   VoiceNumber.created_at < now - STALE_REQUEST)
            .order_by(VoiceNumber.location_id))).all()
        await db.rollback()
    out["errors"] += [{"og": og, "location": loc,
                       "error": f"voice number requested since {created.isoformat()}, not claimed"}
                      for og, loc, created in stale]
    return out


async def for_location(db: AsyncSession, company_id: str, location_id: str) -> VoiceNumber | None:
    return (await db.execute(
        select(VoiceNumber).where(VoiceNumber.company_id == company_id,
                                  VoiceNumber.location_id == location_id)
    )).scalar_one_or_none()


async def location_for_number(db: AsyncSession, e164: str) -> tuple[str, str] | None:
    """(company_id, location_id) the dialled number belongs to, or None. An
    exact match on a number still routed to its location (active, or in its
    release hold), read across tenants: the caller is a phone call, not a
    signed-in user."""
    if not e164:
        return None
    async with cross_tenant_session(db) as everyone:
        row = (await everyone.execute(
            select(VoiceNumber.company_id, VoiceNumber.location_id)
            .where(VoiceNumber.e164 == e164, VoiceNumber.status.in_(ROUTED_STATUSES))
        )).first()
    return (row[0], row[1]) if row else None


async def release_for_deleted_group(db: AsyncSession, og_id: str) -> list[str]:
    """Give back the group's claimed numbers at once, before its data is
    deleted (day 90): no hold, the group is gone. A row with only a searched
    e164 is resolved through connect_numbers.find_owned first. Best effort,
    like billing_reservations.cancel_for_deleted_group: never raises, so it
    never blocks the deletion. Anything it could not release or resolve
    (Connect failed, or provisioning is off) goes to the operator, since the
    rows are deleted with the group. Returns the phone number ids it
    released. Does not write; the caller deletes the rows."""
    from backend.services.operator_alerts import send_voice_numbers_unreleased_alert

    released: list[str] = []
    try:
        with tenant_scope_disabled():
            rows = (await db.execute(
                select(VoiceNumber.connect_phone_number_id, VoiceNumber.e164)
                .where(VoiceNumber.ownership_group_id == og_id,
                       VoiceNumber.status != "released",
                       (VoiceNumber.connect_phone_number_id.is_not(None))
                       | (VoiceNumber.e164.is_not(None)))
                .order_by(VoiceNumber.location_id)
            )).all()
    except Exception:  # noqa: BLE001
        logger.exception("voice numbers of deleted og %s not read", og_id)
        return released
    unresolved: list[str] = []
    reason = "Connect refused or failed"
    for pn, e164 in rows:
        try:
            if pn is None:
                pn = await connect_numbers.find_owned(e164)
                if pn is None:
                    continue  # never claimed: nothing to give back
            if pn == connect_numbers.DISABLED or \
                    await connect_numbers.release(pn) == connect_numbers.DISABLED:
                reason = "VOICE_PROVISIONING_ENABLED is off"
                unresolved.append(pn if pn and pn != connect_numbers.DISABLED else e164 or "?")
                continue
            released.append(pn)
        except Exception:  # noqa: BLE001
            logger.exception("voice number %s (%s) of deleted og %s not released",
                             pn, e164, og_id)
            unresolved.append(pn or e164 or "?")
    if unresolved:
        send_voice_numbers_unreleased_alert(og_id, unresolved, reason)
    return released
