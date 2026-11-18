"""Reservation rules: the per-location JSON document and its helpers."""

from datetime import date, datetime, time, timezone
from typing import Annotated

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.reservations.models import ReservationRules


class ServicePeriod(BaseModel):
    name: str
    weekdays: list[int] = []
    first_seating: time
    last_seating: time

    @field_validator("weekdays")
    @classmethod
    def _weekdays(cls, v: list[int]) -> list[int]:
        if len(set(v)) != len(v):
            raise ValueError("weekdays must be unique")
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("weekdays must be 0 (Monday) to 6 (Sunday)")
        return v

    @model_validator(mode="after")
    def _order(self) -> "ServicePeriod":
        if self.first_seating > self.last_seating:
            raise ValueError("a service period cannot cross midnight")
        return self


class DurationBand(BaseModel):
    max_party: int = Field(ge=1)
    minutes: int = Field(ge=30, le=360)
    churn_minutes: int = Field(ge=0, le=120)


class DateOverride(BaseModel):
    date: date
    periods: list[ServicePeriod] = []
    note: str = ""


class Persona(BaseModel):
    voice_id: str = "Joanna"
    greeting: str = ""
    facts: list[str] = []


def _default_durations() -> list[DurationBand]:
    return [
        DurationBand(max_party=2, minutes=90, churn_minutes=10),
        DurationBand(max_party=4, minutes=105, churn_minutes=15),
        DurationBand(max_party=6, minutes=120, churn_minutes=15),
        DurationBand(max_party=99, minutes=150, churn_minutes=20),
    ]


class RulesSchema(BaseModel):
    service_periods: list[ServicePeriod] = []
    slot_interval_minutes: int = Field(default=15, ge=5, le=60)
    durations: list[DurationBand] = Field(default_factory=_default_durations, min_length=1)
    max_concurrent_covers: int | None = Field(default=None, ge=1)
    max_covers_per_slot: int | None = Field(default=None, ge=1)
    date_overrides: list[DateOverride] = []
    max_self_book_party: int = Field(default=8, ge=1)
    max_party_size: int = Field(default=20, ge=1)
    booking_horizon_days: int = Field(default=60, ge=1, le=365)
    cutoff_minutes: int = Field(default=60, ge=0, le=1440)
    closures: list[date] = []
    section_cover_limits: dict[str, Annotated[int, Field(ge=1)]] = {}
    hold_timeout_minutes: int = Field(default=5, ge=1, le=30)
    pending_hold_hours: int = Field(default=24, ge=1, le=72)
    persona: Persona = Field(default_factory=Persona)
    staff_transfer_number: str | None = Field(
        default=None, pattern=r"^\+[1-9]\d{6,14}$"
    )
    # Model cost (USD) the AI host may spend at this location per local day;
    # once reached, new and ongoing chats are closed with a call-us reply.
    daily_spend_cap_usd: float = Field(default=20.0, ge=1)

    @field_validator("durations")
    @classmethod
    def _increasing(cls, v: list[DurationBand]) -> list[DurationBand]:
        sizes = [b.max_party for b in v]
        if any(b <= a for a, b in zip(sizes, sizes[1:])):
            raise ValueError("duration bands need strictly increasing max_party")
        return v

    @field_validator("date_overrides")
    @classmethod
    def _unique_dates(cls, v: list[DateOverride]) -> list[DateOverride]:
        dates = [o.date for o in v]
        if len(set(dates)) != len(dates):
            raise ValueError("date_overrides must have unique dates")
        return v

    @model_validator(mode="after")
    def _party_sizes(self) -> "RulesSchema":
        if self.max_party_size < self.max_self_book_party:
            raise ValueError("max_party_size must be >= max_self_book_party")
        return self

    def band_for(self, party_size: int) -> DurationBand:
        for band in self.durations:
            if band.max_party >= party_size:
                return band
        return self.durations[-1]

    def duration_for(self, party_size: int) -> int:
        return self.band_for(party_size).minutes

    def periods_for(self, d: date) -> list[ServicePeriod]:
        if d in self.closures:
            return []
        for o in self.date_overrides:
            if o.date == d:
                return list(o.periods)
        return [p for p in self.service_periods if d.weekday() in p.weekdays]


# NANP premium-rate area codes: a transfer there bills by the minute to us.
BLOCKED_TRANSFER_PREFIXES = ("+1900", "+1976")


def transfer_number_problem(number: str, own_number: str | None = None) -> str | None:
    """Why the AI host may not transfer calls to `number`, or None. The
    handoff is an outbound call from Amazon Connect billed per minute, so
    a staff number must be in an allowlisted country
    (settings.VOICE_TRANSFER_COUNTRY_CODES), not a premium-rate NANP area
    code, and not the location's own AI host number (a loop). Checked when
    the rules are saved and again before every transfer (a number saved
    before this check, or a setting narrowed since). Not a RulesSchema
    validator, so a stored number that fails it never stops get_rules."""
    if not any(number.startswith(code) for code in settings.VOICE_TRANSFER_COUNTRY_CODES):
        return "staff_transfer_number must be in an allowed country"
    if number.startswith(BLOCKED_TRANSFER_PREFIXES):
        return "staff_transfer_number cannot be a premium-rate number"
    if own_number and number == own_number:
        return "staff_transfer_number cannot be this location's AI host number"
    return None


async def _row(
    db: AsyncSession, company_id: str, location_id: str
) -> ReservationRules | None:
    return (
        await db.execute(
            select(ReservationRules).where(
                ReservationRules.company_id == company_id,
                ReservationRules.location_id == location_id,
            )
        )
    ).scalar_one_or_none()


async def get_rules(
    db: AsyncSession, company_id: str, location_id: str
) -> tuple[RulesSchema, int]:
    row = await _row(db, company_id, location_id)
    if row is None:
        return RulesSchema(), 0
    return RulesSchema.model_validate(row.rules), row.version


async def save_rules(
    db: AsyncSession, company_id: str, location_id: str, rules: RulesSchema
) -> int:
    """Upsert the rules and bump the version. Does not commit."""
    payload = rules.model_dump(mode="json")
    now = datetime.now(timezone.utc)
    row = await _row(db, company_id, location_id)
    if row is None:
        row = ReservationRules(
            company_id=company_id, location_id=location_id, rules=payload,
            version=1, updated_at=now,
        )
        db.add(row)
    else:
        row.rules = payload
        row.version += 1
        row.updated_at = now
    await db.flush()
    return row.version
