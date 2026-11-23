"""Request and response bodies for the manager reservations API."""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from backend.analytics.models import AgentDailyMetric
from backend.reservations.agent.models import AgentConversation, AgentEvent
from backend.reservations.models import Reservation
from backend.reservations.providers.base import ProviderBooking
from backend.reservations.flows import Flows
from backend.reservations.rules import RulesSchema
from backend.reservations.screening import spam_markable


class RulesOut(BaseModel):
    rules: RulesSchema
    version: int


class FlowsIn(BaseModel):
    """PUT .../flows: only the flows, and for an admin the flow editors
    (omitted: kept). Every other rules field stays as stored."""
    flows: Flows
    flow_editor_ids: list[str] | None = None


class SlotOut(BaseModel):
    start: datetime
    end: datetime
    table_ids: list[str]


class DinerIn(BaseModel):
    name: str = Field(min_length=1)
    phone: str | None = None  # staff bookings may omit it (decision 17)
    email: str | None = None


class BookingIn(BaseModel):
    party_size: int = Field(ge=1)
    start: AwareDatetime
    diner: DinerIn
    notes: str | None = None


class ReservationPatch(BaseModel):
    party_size: int | None = Field(default=None, ge=1)
    start: AwareDatetime | None = None
    notes: str | None = None


class WalkInIn(BaseModel):
    party_size: int = Field(ge=1)
    name: str | None = None
    phone: str | None = None
    notes: str | None = None


class StatusIn(BaseModel):
    status: Literal["seated", "completed", "no_show", "cancelled"]


class ReservationOut(BaseModel):
    id: str
    location_id: str
    party_size: int
    start: datetime
    end: datetime
    status: str
    tables: list[str]
    diner_name: str | None
    diner_phone: str | None
    diner_email: str | None
    notes: str | None
    source: str
    confirmation_code: str | None
    held_until: datetime | None
    # An external provider's booking (#259 step 9): read-only for staff, no
    # tables (that system has none of ours). WizScheduler rows are not.
    external: bool = False
    provider: str = "wizscheduler"

    @classmethod
    def external_booking(cls, b: ProviderBooking, location_id: str,
                         provider: str) -> "ReservationOut":
        """An external provider's booking, as the host view and diner data
        export show it. Only the AI host books there, hence `agent`."""
        return cls(
            id=b.ref, location_id=location_id, party_size=b.party_size,
            start=b.start, end=b.end, status=b.status, tables=[],
            diner_name=b.diner_name, diner_phone=b.diner_phone, diner_email=None,
            notes=None, source="agent", confirmation_code=b.code,
            held_until=b.held_until, external=True, provider=provider,
        )

    @classmethod
    def of(cls, res: Reservation, tables: list[str],
           status: str | None = None) -> "ReservationOut":
        """`status` overrides the stored one (a lapsed hold reads `expired`)."""
        return cls(
            id=res.id, location_id=res.location_id, party_size=res.party_size,
            start=res.start_at, end=res.end_at, status=status or res.status,
            tables=tables,
            diner_name=res.diner_name, diner_phone=res.diner_phone,
            diner_email=res.diner_email, notes=res.notes, source=res.source,
            confirmation_code=res.confirmation_code, held_until=res.held_until,
        )


class ConversationOut(BaseModel):
    """One agent conversation in the manager list. Never carries source_key
    or public_token. cost_usd is a decimal string (6 places)."""
    id: str
    channel: str
    started_at: datetime
    ended_at: datetime | None
    outcome: str | None
    turn_count: int
    cost_usd: str
    reservation_id: str | None
    provider: str

    @classmethod
    def of(cls, c: AgentConversation) -> "ConversationOut":
        return cls(
            id=c.id, channel=c.channel, started_at=c.started_at,
            ended_at=c.ended_at, outcome=c.outcome, turn_count=c.turn_count,
            cost_usd=str(c.cost_usd), reservation_id=c.reservation_id,
            provider=c.provider,
        )


# Model reasoning blocks never leave the server.
_HIDDEN_BLOCKS = frozenset({"thinking", "redacted_thinking"})


def public_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """An event payload as managers see it: without `raw` (the JSON dump of
    `content`) and with thinking blocks dropped from `content`."""
    out = {k: v for k, v in (payload or {}).items() if k != "raw"}
    content = out.get("content")
    if isinstance(content, list):
        out["content"] = [
            b for b in content
            if not (isinstance(b, dict) and b.get("type") in _HIDDEN_BLOCKS)
        ]
    return out


class AgentEventOut(BaseModel):
    seq: int
    type: str
    payload: dict[str, Any]
    latency_ms: dict[str, Any]
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: str
    created_at: datetime

    @classmethod
    def of(cls, e: AgentEvent) -> "AgentEventOut":
        return cls(
            seq=e.seq, type=e.type, payload=public_payload(e.payload),
            latency_ms=e.latency_ms,
            input_tokens=e.input_tokens, output_tokens=e.output_tokens,
            cache_read_tokens=e.cache_read_tokens,
            cache_write_tokens=e.cache_write_tokens,
            cost_usd=str(e.cost_usd), created_at=e.created_at,
        )


class ConversationDetailOut(ConversationOut):
    location_id: str
    status: str
    # A phone call with a caller number: "Mark as spam" applies. The number
    # itself stays out of the detail.
    spam_markable: bool
    events: list[AgentEventOut]

    @classmethod
    def with_events(cls, c: AgentConversation,
                    events: list[AgentEvent]) -> "ConversationDetailOut":
        return cls(
            **ConversationOut.of(c).model_dump(), location_id=c.location_id,
            status=c.status, spam_markable=spam_markable(c),
            events=[AgentEventOut.of(e) for e in events],
        )


class DinerPhoneIn(BaseModel):
    phone: str


class DinerCodeIn(BaseModel):
    """A confirmation code, any case, surrounding whitespace allowed."""
    code: str = Field(min_length=1, max_length=32)


RuleAction = Literal["allow", "block"]


class NumberRuleIn(BaseModel):
    """A new allow or block entry. location_id null = every location
    (admin only). expires_on is a local date; the entry stops applying at
    the end of it (reservations/number_rules.py)."""
    model_config = ConfigDict(extra="forbid")
    e164: str
    action: RuleAction
    location_id: str | None = None
    expires_on: date | None = None
    note: str | None = Field(default=None, max_length=200)


class NumberRulePatch(BaseModel):
    """Action, expiry and note only: the number and the scope never change
    (delete and recreate). An omitted field is left as it is; expires_on
    null means never; action may be omitted but not null."""
    model_config = ConfigDict(extra="forbid")
    action: RuleAction | None = None
    expires_on: date | None = None
    note: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _action_not_null(self) -> "NumberRulePatch":
        if "action" in self.model_fields_set and self.action is None:
            raise ValueError("action cannot be null")
        return self


class NumberRuleOut(BaseModel):
    id: str
    e164: str
    action: str
    location_id: str | None
    expires_at: datetime | None
    # expires_at as the local date the manager picked (YYYY-MM-DD).
    expires_on: date | None
    note: str | None
    created_by_name: str | None
    created_at: datetime
    active: bool

    @classmethod
    def of(cls, v: Any) -> "NumberRuleOut":
        """`v` is a number_rules.RuleView."""
        r = v.rule
        return cls(id=r.id, e164=r.e164, action=r.action, location_id=r.location_id,
                   expires_at=r.expires_at, expires_on=v.expires_on, note=r.note,
                   created_by_name=v.created_by_name, created_at=r.created_at,
                   active=v.active)


class MarkSpamOut(NumberRuleOut):
    """The block entry; created false when it was already there (200)."""
    created: bool


class ScreeningStatsOut(BaseModel):
    """Screened calls over the last 7 days."""
    days: int
    blocked_list: int
    blocked_ftc: int
    answered: int


class InsightsDayOut(BaseModel):
    """One agent_daily_metrics row (one UTC day). Money as decimal strings."""
    day: date
    conversations: int
    resolved: int
    booked: int
    handed_off: int
    abandoned: int
    failed: int
    booking_intent: int
    handoff_reasons: dict[str, int]
    llm_p50_ms: int | None
    llm_p95_ms: int | None
    turn_p50_ms: int | None
    turn_p95_ms: int | None
    cost_usd: str
    billed_usd: str
    screened: int
    blocked: int
    covers_by_period: dict[str, int]

    @classmethod
    def of(cls, m: AgentDailyMetric) -> "InsightsDayOut":
        return cls(
            day=m.day, conversations=m.conversations, resolved=m.resolved,
            booked=m.booked, handed_off=m.handed_off, abandoned=m.abandoned,
            failed=m.failed, booking_intent=m.booking_intent,
            handoff_reasons=m.handoff_reasons, llm_p50_ms=m.llm_p50_ms,
            llm_p95_ms=m.llm_p95_ms, turn_p50_ms=m.turn_p50_ms,
            turn_p95_ms=m.turn_p95_ms, cost_usd=str(m.cost_usd),
            billed_usd=str(m.billed_usd), screened=m.screened, blocked=m.blocked,
            covers_by_period=m.covers_by_period)


class InsightsOut(BaseModel):
    """The AI host's daily metrics for one location, `from` to `to`
    inclusive (UTC dates). Days with no row are absent, not zero."""
    model_config = ConfigDict(populate_by_name=True)

    location_id: str
    from_: date = Field(alias="from")
    to: date
    days: list[InsightsDayOut]


class DinerDataOut(BaseModel):
    """One diner's data at one location (reservations/diner_data.py),
    including that number's entries here and company-wide (location_id
    null). `reservations` includes the diner's mock external bookings here,
    labelled `external: true`."""
    reservations: list[ReservationOut]
    conversations: list[ConversationDetailOut]
    number_rules: list[NumberRuleOut]


class DinerCodeEraseOut(BaseModel):
    """Erasure by confirmation code: one reservation and its conversations
    (reservations/diner_data.erase_by_code). The number's other data stays."""
    reservations_scrubbed: int
    external_bookings_deleted: int
    conversations_deleted: int
    conversations_closed_in_flight: int
    conversations_still_in_flight: int


class DinerEraseOut(BaseModel):
    reservations_scrubbed: int
    # Bookings the AI host made in the mock external system here (deleted).
    external_bookings_deleted: int
    conversations_deleted: int
    # Mid-turn conversations closed rather than deleted; erase again later.
    conversations_closed_in_flight: int
    # Locked by a running turn: neither deleted nor closed; erase again later.
    conversations_still_in_flight: int
    lookup_failures_deleted: int
    # This location's entries for the number.
    number_rules_deleted: int
    # Company-wide entries for the number, never erased here: an admin
    # removes them from the number lists page.
    company_rules_remaining: int


class ReservationsConfigOut(BaseModel):
    enabled: bool
    # Whether the rules editor may offer the mock external provider.
    mock_provider_allowed: bool = False
    # Mirrors agent/models.py OUTCOMES; the conversations filter reads it.
    conversation_outcomes: list[str]


class VoiceNumberOut(BaseModel):
    """The location's AI host phone number. status "none" when it has none."""
    e164: str | None
    status: Literal["none", "requested", "active", "releasing", "released"]
    release_after: AwareDatetime | None = None
