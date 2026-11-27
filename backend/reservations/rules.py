"""Reservation rules: the per-location JSON document and its helpers."""

from datetime import date, datetime, time, timezone
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel, Field, TypeAdapter, ValidationError, ValidationInfo, field_validator,
    model_validator,
)
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.reservations.flows import Flows
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


class VoiceKnobs(BaseModel):
    """Phone-channel tuning. API-only (no rules-editor UI); the defaults keep
    today's prompt and confirm_prompt byte-identical."""
    # 0 adds no sentence-count instruction to the voice prompt.
    voice_reply_max_sentences: int = Field(default=0, ge=0, le=5)
    voice_read_back: bool = False
    voice_confirm_style: Literal["question", "statement"] = "question"


class MockExternalSettings(BaseModel):
    """Per-location knobs for the mock external booking provider (#259 step
    9, providers/mock_external.py): its capacity, and the latency and failure
    rate it simulates. Admin-only, like `provider`."""

    max_covers_per_slot: int = Field(default=40, ge=1, le=500)
    latency_ms: int = Field(default=0, ge=0, le=3000)
    failure_rate: float = Field(default=0.0, ge=0.0, le=0.5)


ProviderName = Literal["wizscheduler", "mock_external"]


# Validation context for rules read back from storage: checks added after
# rules were stored (the greeting disclosure) apply on write only, so
# existing rules keep reading.
STORED = {"stored": True}

DISCLOSURE_MESSAGE = ("the greeting must tell diners the host is automated: "
                      "include the word 'automated', or turn the disclosure off")


def discloses_automation(greeting: str) -> bool:
    """The greeting tells the diner the host is automated: it says
    "automated", case-insensitive. "Assistant" alone does not (a person can
    be an assistant), so it no longer passes."""
    return "automated" in greeting.lower()


def missing_disclosure(rules: "RulesSchema") -> bool:
    """A custom greeting that hides the automated host while the location
    discloses it."""
    g = rules.persona.greeting
    return bool(rules.disclose_automated and g and not discloses_automation(g))


class Persona(BaseModel):
    voice_id: str = "Joanna"
    # Empty: the default greeting (agent/gate.greeting). While the location
    # discloses the automated host (RulesSchema.disclose_automated), a custom
    # greeting must say so; a stored one that does not (saved before the rule,
    # or while the disclosure was off) reads, but is never rendered: the
    # default is.
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
    voice: VoiceKnobs = Field(default_factory=VoiceKnobs)
    staff_transfer_number: str | None = Field(
        default=None, pattern=r"^\+[1-9]\d{6,14}$"
    )
    # Cost (USD: model, and voice call cost) the AI host may spend at this
    # location per local day; once reached, new and ongoing chats are closed
    # with a call-us reply.
    daily_spend_cap_usd: float = Field(default=20.0, ge=1)
    # The booking provider new AI host conversations book through (#259 step
    # 9). A conversation keeps the one it started with. Admin-only, as are
    # the mock's knobs.
    provider: ProviderName = "wizscheduler"
    mock_external: MockExternalSettings = Field(default_factory=MockExternalSettings)
    # The greeting (web chat and phone) tells diners the host is an automated
    # assistant. On by default; an admin may turn it off, and the restaurant
    # is then responsible for any disclosure the law requires (terms 12.F).
    # Admin-only. Whether on or off, the host never claims to be a person.
    disclose_automated: bool = True
    # Custom conversation flows per channel (decision 102); null keeps the
    # default flow. Validated by the rules route (flows.flow_problems).
    flows: Flows = Field(default_factory=Flows)
    # Regular managers an admin allowed to edit `flows` at this location.
    # Admin-only.
    flow_editor_ids: list[str] = []

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

    @model_validator(mode="after")
    def _greeting_discloses(self, info: ValidationInfo) -> "RulesSchema":
        # Checked here only when the write states disclose_automated; a write
        # that omits it (a regular manager's editor) keeps the stored value,
        # and the rules route checks the merged rules (missing_disclosure).
        if ((info.context or {}).get("stored")
                or "disclose_automated" not in self.model_fields_set):
            return self
        if missing_disclosure(self):
            raise ValueError(DISCLOSURE_MESSAGE)
        return self

    def custom_greeting(self) -> str:
        """The custom greeting, or "" (use the default) when it is unset, or
        when the location discloses the automated host and it does not."""
        g = self.persona.greeting
        return g if (not self.disclose_automated or discloses_automation(g)) else ""

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
    return RulesSchema.model_validate(row.rules, context=STORED), row.version


async def get_agent_rules(
    db: AsyncSession, company_id: str, location_id: str
) -> tuple[RulesSchema, int]:
    """get_rules for the AI host's channels (decision 103): a location without
    its own staff_transfer_number uses the company's
    default_staff_transfer_number, so one operator can cover many locations.
    Only while voice is on (VOICE_PROVISIONING_ENABLED): with voice off the
    web chat reads the location's own number, as before. Never saved back:
    the rules editor reads get_rules."""
    from backend.config import settings
    from backend.reservations.voice_capacity_models import VoiceCompanySettings

    rules, version = await get_rules(db, company_id, location_id)
    if not rules.staff_transfer_number and settings.VOICE_PROVISIONING_ENABLED:
        default = (await db.execute(
            select(VoiceCompanySettings.default_staff_transfer_number)
            .where(VoiceCompanySettings.company_id == company_id))).scalar_one_or_none()
        if default:
            rules = rules.model_copy(update={"staff_transfer_number": default})
    return rules, version


class FlowsConflict(Exception):
    """The flows changed since the copy being saved was loaded (409)."""


_FLOW_FIELDS = ("flows", "flow_editor_ids")


async def lock_rules(db: AsyncSession, location_id: str) -> None:
    """The location's rules-write lock, held to the end of the transaction.
    Taken before reading what a save keeps or compares, so two saves run one
    after the other. An advisory lock, so bookings (which lock the location
    row) never wait on it."""
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                     {"k": f"reservation-rules:{location_id}"})


async def flows_version(db: AsyncSession, company_id: str, location_id: str) -> int:
    """The flows' own version (0 before the first save)."""
    row = await _row(db, company_id, location_id)
    return row.flows_version if row is not None else 0


async def check_flows_version(
    db: AsyncSession, company_id: str, location_id: str, loaded: int
) -> None:
    """FlowsConflict unless the stored flows are still the copy loaded at
    `loaded`. Call under lock_rules."""
    if await flows_version(db, company_id, location_id) != loaded:
        raise FlowsConflict()


async def save_rules(
    db: AsyncSession, company_id: str, location_id: str, rules: RulesSchema
) -> int:
    """Upsert the rules and bump the version, and the flows_version when the
    flows or their editors changed. Does not commit."""
    payload = rules.model_dump(mode="json")
    now = datetime.now(timezone.utc)
    stored = await stored_fields(db, company_id, location_id, _FLOW_FIELDS)
    flows_changed = any(stored[k] != getattr(rules, k) for k in _FLOW_FIELDS)
    row = await _row(db, company_id, location_id)
    if row is None:
        row = ReservationRules(
            company_id=company_id, location_id=location_id, rules=payload,
            version=1, flows_version=1 if flows_changed else 0, updated_at=now,
        )
        db.add(row)
    else:
        row.rules = payload
        row.version += 1
        if flows_changed:
            row.flows_version += 1
        row.updated_at = now
    await db.flush()
    return row.version


async def stored_fields(
    db: AsyncSession, company_id: str, location_id: str, names: tuple[str, ...]
) -> dict[str, Any]:
    """The stored value of each named top-level rules field, validated one
    field at a time: a field missing or invalid in storage reads as its
    default, and stored rules that no longer validate as a whole still yield
    their valid fields. Used where a save must keep, or compare against,
    what is stored (the voice knobs, the admin-only provider fields)."""
    row = await _row(db, company_id, location_id)
    raw: dict[str, Any] = row.rules if row is not None and isinstance(row.rules, dict) else {}
    out: dict[str, Any] = {}
    for name in names:
        info = RulesSchema.model_fields[name]
        try:
            out[name] = TypeAdapter(info.annotation).validate_python(
                raw[name], context=STORED)
        except (KeyError, ValidationError):
            out[name] = info.get_default(call_default_factory=True)
    return out
