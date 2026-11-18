"""Phone numbers for the AI host's voice channel (#259 step 6).

One `VoiceNumber` per location, owned by the location's ownership group and
following its AI host subscription (`ensure_for_group`):

- subscription answering (ANSWERING_STATUSES): every active location gets a
  number. A new row starts `requested`; the number is claimed in Amazon
  Connect, routed to the AI host contact flow, and becomes `active`. A
  number in its release hold (`releasing`) is reactivated as it is, with no
  new claim. A location that was archived lets its number go (hold, then
  release).
- anything else (no subscription, ended, unpaid, paused): `active` numbers
  go to `releasing` with `release_after = now + VOICE_RELEASE_HOLD_DAYS`; a
  number never claimed is simply `released`.

`release_due` gives back the numbers whose hold is over. `run_daily` (the
daily AI host billing script) runs both for every group, which is also the
retry for a claim that failed: a failure is logged, the row stays
`requested`, and nothing raises into the webhook that triggered it.

Nothing calls Connect unless settings.VOICE_PROVISIONING_ENABLED is true;
with it off `ensure_for_group` and `release_due` do nothing at all.

Claims are idempotent per row: the Connect ClientToken is derived from the
row id, and a released row is replaced by a fresh row (fresh id, fresh
token) when the location needs a number again. Each Connect step commits on
its own, under the row's lock (FOR UPDATE SKIP LOCKED), so a concurrent run
skips a row being worked rather than claiming twice.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
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
           "run_daily", "location_for_number", "for_location"]

logger = logging.getLogger(__name__)

# A number in these states still routes calls to its location.
ROUTED_STATUSES = ("active", "releasing")
DEFAULT_COUNTRY = "US"


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def client_token(row: VoiceNumber) -> str:
    return f"wizscheduler-voice-{row.id}"


async def _locked(db: AsyncSession, row_id: str) -> VoiceNumber | None:
    return (await db.execute(
        select(VoiceNumber).where(VoiceNumber.id == row_id)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )).scalar_one_or_none()


async def _active_locations(db: AsyncSession, og_id: str) -> list[Location]:
    return list((await db.execute(
        select(Location)
        .join(Company, Company.id == Location.company_id)
        .where(Company.ownership_group_id == og_id, active(Location))
        .order_by(Location.id)
    )).scalars().all())


async def _rows(db: AsyncSession, og_id: str) -> list[VoiceNumber]:
    return list((await db.execute(
        select(VoiceNumber).where(VoiceNumber.ownership_group_id == og_id)
        .order_by(VoiceNumber.location_id)
        .execution_options(populate_existing=True)
    )).scalars().all())


async def _provision(db: AsyncSession, row_id: str) -> str:
    """Claim (if not yet) and route the row's number. Returns the row's
    status afterwards; a Connect failure is logged and leaves it
    `requested`."""
    row = await _locked(db, row_id)
    if row is None or row.status != "requested":
        await db.rollback()
        return row.status if row else "skipped"
    location_id = row.location_id
    try:
        if row.connect_phone_number_id is None:
            claimed = await connect_numbers.claim(DEFAULT_COUNTRY, client_token=client_token(row))
            if claimed == connect_numbers.DISABLED:
                await db.rollback()
                return "requested"
            row.e164, row.connect_phone_number_id = claimed
            await db.commit()  # keep the claimed number even if routing fails
            row = await _locked(db, row_id)
            if row is None or row.status != "requested":
                await db.rollback()
                return row.status if row else "skipped"
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


def _hold(row: VoiceNumber, now: datetime) -> None:
    if row.status == "active":
        row.status = "releasing"
        row.release_after = now + timedelta(days=settings.VOICE_RELEASE_HOLD_DAYS)
    elif row.status == "requested" and row.connect_phone_number_id is None:
        row.status = "released"
    elif row.status == "requested":
        # Claimed but never routed: still ours, so it gets the hold too.
        row.status = "releasing"
        row.release_after = now + timedelta(days=settings.VOICE_RELEASE_HOLD_DAYS)


async def ensure_for_group(db: AsyncSession, og_id: str, now: datetime | None = None) -> dict:
    """Bring the group's numbers in line with its AI host subscription (see
    the module docstring). Commits. Connect failures are logged, never
    raised; a database error propagates (callers in webhooks catch it)."""
    if not connect_numbers.enabled():
        return {"og": og_id, "disabled": True}
    now = _now(now)
    out: dict = {"og": og_id, "claimed": [], "reactivated": [], "held": [], "pending": []}
    with tenant_scope_disabled():
        sub: ReservationSubscription | None = await for_group(db, og_id)
        answering = sub is not None and sub.status in ANSWERING_STATUSES
        locations = await _active_locations(db, og_id) if answering else []
        wanted = {loc.id: loc for loc in locations}
        rows = {r.location_id: r for r in await _rows(db, og_id)}

        # Numbers no longer wanted (subscription ended, location archived).
        for loc_id, row in rows.items():
            if loc_id not in wanted and row.status in ("active", "requested"):
                _hold(row, now)
                out["held"].append(loc_id)
        # A number in its hold comes back as it is.
        for loc_id, row in rows.items():
            if loc_id in wanted and row.status == "releasing":
                row.status = "active"
                row.release_after = None
                out["reactivated"].append(loc_id)
        await db.commit()

        to_provision: list[str] = []
        for loc_id, loc in wanted.items():
            row = rows.get(loc_id)
            if row is not None and row.status in ("active", "releasing"):
                continue
            if row is None or row.status == "released":
                try:
                    if row is not None:
                        db.expunge(row)
                        await db.execute(delete(VoiceNumber).where(
                            VoiceNumber.id == row.id, VoiceNumber.status == "released"))
                    row = VoiceNumber(company_id=loc.company_id, ownership_group_id=og_id,
                                      location_id=loc_id, status="requested")
                    db.add(row)
                    await db.commit()
                except IntegrityError:  # a concurrent run made it
                    await db.rollback()
                    continue
            to_provision.append(row.id)

        for row_id in to_provision:
            status = await _provision(db, row_id)
            row = await db.get(VoiceNumber, row_id)
            loc_id = row.location_id if row else row_id
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
                if row.connect_phone_number_id:
                    if await connect_numbers.release(row.connect_phone_number_id) \
                            == connect_numbers.DISABLED:
                        await db.rollback()
                        continue
                row.status = "released"
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
    """The daily step: re-align every group that has a subscription or a
    number still held (retries failed claims, catches a missed webhook),
    then release the numbers whose hold is over. One group's failure never
    stops the others."""
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
    deleted (day 90): no hold, the group is gone. Best effort, like
    billing_reservations.cancel_for_deleted_group: logs and carries on, never
    raises, so it never blocks the deletion. Returns the phone number ids it
    released. Does not write; the caller deletes the rows."""
    released: list[str] = []
    try:
        with tenant_scope_disabled():
            ids = (await db.execute(
                select(VoiceNumber.connect_phone_number_id)
                .where(VoiceNumber.ownership_group_id == og_id,
                       VoiceNumber.status != "released",
                       VoiceNumber.connect_phone_number_id.is_not(None))
            )).scalars().all()
    except Exception:  # noqa: BLE001
        logger.exception("voice numbers of deleted og %s not read", og_id)
        return released
    for phone_number_id in ids:
        try:
            if await connect_numbers.release(phone_number_id) == connect_numbers.DISABLED:
                logger.error("voice number %s of deleted og %s not released: "
                             "provisioning is off", phone_number_id, og_id)
                continue
            released.append(phone_number_id)
        except Exception:  # noqa: BLE001
            logger.exception("voice number %s of deleted og %s not released",
                             phone_number_id, og_id)
    return released
