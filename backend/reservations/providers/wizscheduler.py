"""The WizScheduler booking provider: a thin adapter over `availability.find_slots`
(still the single availability evaluator) and `booking.py`. Refs are
reservation ids. booking.* errors pass through unchanged: they are the
provider errors (see base.py)."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.reservations import availability, booking
from backend.reservations.models import SOURCE_VOICE_AGENT, SOURCE_WEB_AGENT, Reservation
from backend.reservations.providers.base import (
    ProviderBooking,
    ProviderContext,
    ProviderSlot,
)


def _booking(res: Reservation) -> ProviderBooking:
    return ProviderBooking(
        ref=res.id, code=res.confirmation_code, start=res.start_at, end=res.end_at,
        party_size=res.party_size, status=res.status, diner_name=res.diner_name,
        diner_phone=res.diner_phone, external=False, held_until=res.held_until)


class WizSchedulerProvider:
    name = "wizscheduler"

    async def find_slots(
        self, db: AsyncSession, ctx: ProviderContext, party_size: int,
        window_start: datetime, window_end: datetime, *,
        exclude_ref: str | None = None,
    ) -> list[ProviderSlot]:
        slots = await availability.find_slots(
            db, ctx.company_id, ctx.location_id, party_size, window_start, window_end,
            now=ctx.now, exclude_reservation_id=exclude_ref, apply_cutoff=True,
        )
        return [ProviderSlot(start=s.start, end=s.end, party_size=party_size, ref=None)
                for s in slots]

    async def hold(
        self, db: AsyncSession, ctx: ProviderContext, party_size: int,
        start: datetime, *, name: str, phone: str, channel: str,
        conversation_id: str,
    ) -> ProviderBooking:
        source = SOURCE_VOICE_AGENT if channel == "voice" else SOURCE_WEB_AGENT
        res = await booking.hold(
            db, ctx.company_id, ctx.location_id, party_size, start, source=source,
            conversation_id=conversation_id, now=ctx.now,
            diner=booking.DinerContact(name=name, phone=phone))
        return _booking(res)

    async def book(self, db: AsyncSession, ctx: ProviderContext,
                   ref: str) -> ProviderBooking:
        return _booking(await booking.confirm(db, ctx.company_id, ref, now=ctx.now))

    async def modify(
        self, db: AsyncSession, ctx: ProviderContext, ref: str, *,
        party_size: int | None, start: datetime | None,
    ) -> ProviderBooking:
        return _booking(await booking.modify(
            db, ctx.company_id, ref, party_size=party_size, start=start,
            now=ctx.now, by_staff=False))

    async def cancel(self, db: AsyncSession, ctx: ProviderContext,
                     ref: str) -> ProviderBooking:
        return _booking(await booking.cancel(db, ctx.company_id, ref, now=ctx.now))

    async def lookup(
        self, db: AsyncSession, ctx: ProviderContext, *,
        code: str | None = None, phone: str | None = None, ref: str | None = None,
    ) -> ProviderBooking | None:
        if ref is not None:
            res = (await db.execute(
                select(Reservation).where(Reservation.id == ref,
                                          Reservation.company_id == ctx.company_id,
                                          Reservation.location_id == ctx.location_id)
                .execution_options(populate_existing=True)
            )).scalar_one_or_none()
        elif code is not None and phone is not None:
            res = await booking.lookup(db, ctx.company_id, ctx.location_id, code, phone)
        else:
            raise ValueError("lookup needs a ref, or a code and a phone")
        return _booking(res) if res is not None else None
