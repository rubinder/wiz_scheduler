"""The mock external booking provider (#259 step 9): its own inventory,
injected latency and failures, idempotency keys, and the agent's one retry
on Transient."""

import asyncio
import random
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from backend.reservations import availability
from backend.reservations.agent import recording, tools
from backend.reservations.agent.core import RETRY_TEXT, AgentCore, TurnInput, handoff_text
from backend.reservations.mock_external_models import (
    MockExternalBooking,
    MockExternalIdempotency,
)
from backend.reservations.providers import (
    HoldExpired,
    InvalidTransition,
    NotFound,
    PartyTooLarge,
    ProviderContext,
    SlotUnavailable,
    Transient,
    provider_named,
)
from backend.reservations.providers import mock_external
from backend.reservations.providers.mock_external import MockExternalProvider
from backend.reservations.models import Reservation
from backend.reservations.rules import MockExternalSettings, get_rules, save_rules
from tests.conftest import COMPANY_ID, LOCATION_ID, get_test_session_factory
from tests.reservations.agent.fakes import ScriptedLlm, last_tool_result, reply, text, tool_use

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NY = ZoneInfo("America/New_York")
PHONE = "+12125550100"


_KNOBS: dict[str, MockExternalSettings] = {}


def _knobs(monkeypatch, **kw) -> None:
    """The mock's settings for the contexts `_ctx` builds (rules.mock_external)."""
    monkeypatch.setitem(_KNOBS, "settings", MockExternalSettings(**kw))


async def _ctx(db, now=None, conv_id="conv0001", seq=1, attempt=0) -> ProviderContext:
    rules, _ = await get_rules(db, COMPANY_ID, LOCATION_ID)
    if "settings" in _KNOBS:
        rules = rules.model_copy(update={"mock_external": _KNOBS["settings"]})
    return ProviderContext(COMPANY_ID, LOCATION_ID, "America/New_York", rules,
                           now or datetime.now(timezone.utc), conversation_id=conv_id,
                           seq=seq, attempt=attempt)


def _at(ctx: ProviderContext, hh: int, mm: int = 0) -> datetime:
    day = ctx.now.astimezone(NY).date() + timedelta(days=7)
    return datetime.combine(day, time(hh, mm), tzinfo=NY)


async def _hold(db, ctx, start, party=2, name="Ada"):
    return await provider_named("mock_external").hold(
        db, ctx, party, start, name=name, phone=PHONE, channel="web",
        conversation_id=ctx.conversation_id or "conv0001")


async def _count(db, model=MockExternalBooking) -> int:
    return (await db.execute(select(func.count()).select_from(model))).scalar_one()


async def test_find_hold_book_modify_cancel_lookup(db_session, seeded_floor):
    p = provider_named("mock_external")
    assert isinstance(p, MockExternalProvider)
    ctx = await _ctx(db_session)
    start = _at(ctx, 19)
    day_start = datetime.combine(start.date(), time(0), tzinfo=NY)
    slots = await p.find_slots(db_session, ctx, 2, day_start, day_start + timedelta(days=1))
    # Every 15 minutes from 17:00 to 21:00, the dinner period.
    assert [s.start.astimezone(NY).strftime("%H:%M") for s in slots][:3] == \
        ["17:00", "17:15", "17:30"]
    assert len(slots) == 17 and slots[-1].start.astimezone(NY).time() == time(21)
    assert slots[0].end - slots[0].start == timedelta(minutes=90)

    held = await _hold(db_session, ctx, start)
    assert held.external and held.status == "held" and held.code is None
    assert held.held_until is not None
    booked = await p.book(db_session, ctx, held.ref)
    assert booked.status == "booked" and booked.held_until is None
    assert booked.code and len(booked.code) == 6
    assert set(booked.code) <= set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")

    found = await p.lookup(db_session, ctx, code=booked.code.lower(), phone=PHONE)
    assert found == booked
    assert await p.lookup(db_session, ctx, ref=held.ref) == booked
    assert await p.lookup(db_session, ctx, code=booked.code, phone="+12125550199") is None

    moved = await p.modify(db_session, ctx, held.ref, party_size=4, start=_at(ctx, 20))
    assert moved.party_size == 4 and moved.start == _at(ctx, 20)
    assert moved.end - moved.start == timedelta(minutes=105)
    assert moved.code == booked.code

    cancelled = await p.cancel(db_session, ctx, held.ref)
    assert cancelled.status == "cancelled"
    with pytest.raises(InvalidTransition):
        await p.cancel(db_session, ctx, held.ref)
    with pytest.raises(NotFound):
        await p.book(db_session, ctx, "nope0000")
    with pytest.raises(PartyTooLarge):
        await _hold(db_session, ctx, start, party=ctx.rules.max_self_book_party + 1)
    with pytest.raises(SlotUnavailable):
        await _hold(db_session, ctx, _at(ctx, 16))  # outside the dinner period


async def test_capacity_is_covers_per_15_minute_step(db_session, seeded_floor, monkeypatch):
    _knobs(monkeypatch, max_covers_per_slot=4)
    p = provider_named("mock_external")
    ctx = await _ctx(db_session)
    await _hold(db_session, ctx, _at(ctx, 19))
    await _hold(db_session, ctx, _at(ctx, 19), name="Bo")
    for blocked in (_at(ctx, 19), _at(ctx, 19, 15), _at(ctx, 17, 45)):
        with pytest.raises(SlotUnavailable):
            await _hold(db_session, ctx, blocked, name="Cy")
    # 17:30-19:00 and 20:30 onwards don't overlap 19:00-20:30.
    w = _at(ctx, 17)
    starts = {s.start.astimezone(NY).strftime("%H:%M")
              for s in await p.find_slots(db_session, ctx, 2, w, w + timedelta(hours=5))}
    assert {"17:30", "20:30"} <= starts
    assert not starts & {"17:45", "19:00", "20:15"}


async def test_an_expired_hold_frees_its_covers(db_session, seeded_floor, monkeypatch):
    _knobs(monkeypatch, max_covers_per_slot=2)
    p = provider_named("mock_external")
    ctx = await _ctx(db_session)
    held = await _hold(db_session, ctx, _at(ctx, 19))
    with pytest.raises(SlotUnavailable):
        await _hold(db_session, ctx, _at(ctx, 19), name="Bo")
    later = await _ctx(db_session, now=ctx.now + timedelta(
        minutes=ctx.rules.hold_timeout_minutes, seconds=1))
    assert (await p.lookup(db_session, later, ref=held.ref)).status == "expired"
    with pytest.raises(HoldExpired):
        await p.book(db_session, later, held.ref)
    assert (await _hold(db_session, later, _at(later, 19), name="Bo")).status == "held"


async def test_idempotent_replays_return_the_first_result(db_session, seeded_floor):
    p = provider_named("mock_external")
    ctx = await _ctx(db_session)
    held = await _hold(db_session, ctx, _at(ctx, 19))

    first = await p.book(db_session, ctx, held.ref, idempotency_key="c:book:1")
    assert await p.book(db_session, ctx, held.ref, idempotency_key="c:book:1") == first

    moved = await p.modify(db_session, ctx, held.ref, party_size=3, start=None,
                           idempotency_key="c:modify:2")
    again = await p.modify(db_session, ctx, held.ref, party_size=3, start=None,
                           idempotency_key="c:modify:2")
    assert again == moved and again.party_size == 3

    gone = await p.cancel(db_session, ctx, held.ref, idempotency_key="c:cancel:3")
    assert await p.cancel(db_session, ctx, held.ref, idempotency_key="c:cancel:3") == gone
    # A replay is the stored first result even though the booking changed since.
    assert await p.book(db_session, ctx, held.ref, idempotency_key="c:book:1") == first
    assert first.status == "booked"
    assert await _count(db_session) == 1
    assert await _count(db_session, MockExternalIdempotency) == 3

    other = await _hold(db_session, ctx, _at(ctx, 20), name="Bo")
    with pytest.raises(InvalidTransition):  # a key replayed for another booking
        await p.book(db_session, ctx, other.ref, idempotency_key="c:book:1")


def _failing_conv(action: str, rate: float, seq: int = 1) -> str:
    """A conversation id whose first draw for `action` fails and second passes."""
    for i in range(1000):
        cid = f"conv{i:04d}"
        rng = random.Random(f"{cid}:{seq}:{action}")
        if rng.random() < rate and rng.random() >= rate:
            return cid
    raise AssertionError("no seed found")


async def test_an_injected_failure_writes_nothing_and_is_reproducible(
        db_session, seeded_floor, monkeypatch):
    _knobs(monkeypatch, failure_rate=0.5)
    cid = _failing_conv("hold", 0.5)
    ctx = await _ctx(db_session, conv_id=cid)
    for _ in range(2):  # the same turn draws the same failure
        with pytest.raises(Transient):
            await _hold(db_session, ctx, _at(ctx, 19))
    assert await _count(db_session) == 0
    # The retry (attempt 1) takes the next draw, which passes.
    retry = await _ctx(db_session, conv_id=cid, attempt=1)
    held = await _hold(db_session, retry, _at(ctx, 19))

    _knobs(monkeypatch, failure_rate=0.5)
    bid = _failing_conv("book", 0.5)
    bctx = await _ctx(db_session, conv_id=bid)
    with pytest.raises(Transient):
        await provider_named("mock_external").book(db_session, bctx, held.ref,
                                                   idempotency_key=f"{bid}:book:1")
    assert await _count(db_session, MockExternalIdempotency) == 0
    row = await db_session.get(MockExternalBooking, held.ref)
    assert row.status == "held" and row.code is None


async def test_latency_sleeps_before_every_call(db_session, seeded_floor, monkeypatch):
    _knobs(monkeypatch, latency_ms=1500)
    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)

    monkeypatch.setattr(mock_external, "_sleep", fake_sleep)
    ctx = await _ctx(db_session)
    held = await _hold(db_session, ctx, _at(ctx, 19))
    await provider_named("mock_external").lookup(db_session, ctx, ref=held.ref)
    assert slept == [1.5, 1.5]


async def test_mock_rows_do_not_change_wizscheduler_find_slots(db_session, seeded_floor):
    ctx = await _ctx(db_session)
    w = _at(ctx, 17)
    before = await availability.find_slots(db_session, COMPANY_ID, LOCATION_ID, 2, w,
                                           w + timedelta(hours=5), now=ctx.now)
    for name in ("A", "B", "C", "D", "E", "F"):
        held = await _hold(db_session, ctx, _at(ctx, 19), party=6, name=name)
        await provider_named("mock_external").book(db_session, ctx, held.ref)
    after = await availability.find_slots(db_session, COMPANY_ID, LOCATION_ID, 2, w,
                                          w + timedelta(hours=5), now=ctx.now)
    assert after == before


async def test_two_holds_for_the_last_covers_cannot_both_succeed(seeded_floor, monkeypatch):
    _knobs(monkeypatch, max_covers_per_slot=2)
    factory = get_test_session_factory()

    async def attempt(name: str):
        async with factory() as db:
            ctx = await _ctx(db)
            try:
                await _hold(db, ctx, _at(ctx, 19), name=name)
                await db.commit()
                return "held"
            except SlotUnavailable:
                await db.rollback()
                return "full"

    results = await asyncio.gather(attempt("A"), attempt("B"))
    assert sorted(results) == ["full", "held"]


# -- the agent: one retry on Transient, then staff --------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _mock_conv(db, web_conv):
    web_conv.provider = "mock_external"
    await db.flush()
    return web_conv


def _hold_script(now: datetime) -> list:
    day = (now.astimezone(NY).date() + timedelta(days=7)).isoformat()

    def hold(request):
        opts = last_tool_result(request)["options"]
        return reply(tool_use("tu_2", "hold_slot", {
            "start_iso": opts[2]["start_iso"], "party_size": 2, "name": "Ada",
            "phone": PHONE}), stop="tool_use")

    return [reply(tool_use("tu_1", "find_slots", {"party_size": 2, "date": day,
                                                  "earliest": None, "latest": None}),
                  stop="tool_use"),
            hold, reply(text("Holding it. Press Confirm."))]


def _flaky(monkeypatch, method: str, failures: int) -> list:
    """Make the mock's `method` raise Transient `failures` times; returns the
    (attempt, idempotency_key) of every call."""
    calls: list = []
    real = getattr(MockExternalProvider, method)

    async def wrapped(self, db, ctx, *a, **kw):
        calls.append((ctx.attempt, kw.get("idempotency_key")))
        if len(calls) <= failures:
            raise Transient()
        return await real(self, db, ctx, *a, **kw)

    monkeypatch.setattr(MockExternalProvider, method, wrapped)
    return calls


async def test_confirm_retries_once_with_the_same_key(db_session, web_conv, monkeypatch):
    conv = await _mock_conv(db_session, web_conv)
    now = _now()
    core = AgentCore(ScriptedLlm(*_hold_script(now)))
    out = await core.handle_turn(db_session, conv, TurnInput("Table for two", None), now=now)
    assert out.expect == "confirm_hold"
    calls = _flaky(monkeypatch, "book", 1)
    out = await core.handle_turn(db_session, conv, TurnInput(None, "confirm_hold"), now=now)
    assert out.reply_text.startswith(f"{RETRY_TEXT} You're booked")
    assert [a for a, _ in calls] == [0, 1]
    assert calls[0][1] == calls[1][1] == f"{conv.id}:book:{conv.turn_count}"
    assert conv.outcome == "booked"
    rows = (await db_session.execute(select(MockExternalBooking))).scalars().all()
    assert [r.status for r in rows] == ["booked"]


async def test_a_second_transient_hands_off(db_session, web_conv, monkeypatch):
    conv = await _mock_conv(db_session, web_conv)
    now = _now()
    core = AgentCore(ScriptedLlm(*_hold_script(now)))
    await core.handle_turn(db_session, conv, TurnInput("Table for two", None), now=now)
    _flaky(monkeypatch, "book", 2)
    out = await core.handle_turn(db_session, conv, TurnInput(None, "confirm_hold"), now=now)
    assert out.handoff is not None and out.handoff["reason"] == "provider_unavailable"
    assert out.reply_text == f"{RETRY_TEXT} {handoff_text(conv.channel, None)}"
    assert conv.outcome == "handed_off"
    row = (await db_session.execute(select(MockExternalBooking))).scalar_one()
    assert row.status == "cancelled"  # the hold is released


async def test_a_tool_retries_once_then_hands_off(db_session, web_conv, monkeypatch):
    conv = await _mock_conv(db_session, web_conv)
    now = _now()
    day = (now.astimezone(NY).date() + timedelta(days=7)).isoformat()
    inp = {"party_size": 2, "date": day, "earliest": None, "latest": None}

    calls = _flaky(monkeypatch, "find_slots", 1)
    out = await tools.execute_tool(db_session, conv, "find_slots", inp, now=now)
    assert not out.is_error and out.retried and out.result["options"]
    assert [a for a, _ in calls] == [0, 1]

    monkeypatch.undo()
    _flaky(monkeypatch, "find_slots", 2)
    out = await tools.execute_tool(db_session, conv, "find_slots", inp, now=now)
    assert out.is_error
    assert out.result == {"error": "provider_unavailable", "handoff": True}

    # In a model turn, that ends the turn with staff.
    core = AgentCore(ScriptedLlm(reply(tool_use("tu_1", "find_slots", inp),
                                       stop="tool_use")))
    _flaky(monkeypatch, "find_slots", 99)
    out = await core.handle_turn(db_session, conv, TurnInput("Table for two", None), now=now)
    assert out.handoff is not None and out.handoff["reason"] == "provider_unavailable"
    assert out.reply_text == f"{RETRY_TEXT} {handoff_text(conv.channel, None)}"


async def test_a_retried_tool_prefixes_the_reply(db_session, web_conv, monkeypatch):
    conv = await _mock_conv(db_session, web_conv)
    now = _now()
    _flaky(monkeypatch, "find_slots", 1)
    core = AgentCore(ScriptedLlm(*_hold_script(now)))
    out = await core.handle_turn(db_session, conv, TurnInput("Table for two", None), now=now)
    assert out.reply_text == f"{RETRY_TEXT} Holding it. Press Confirm."
    assert out.expect == "confirm_hold"


# -- selection: a conversation keeps its provider ----------------------------


async def _switch(db, provider: str) -> None:
    rules, _ = await get_rules(db, COMPANY_ID, LOCATION_ID)
    await save_rules(db, COMPANY_ID, LOCATION_ID,
                     rules.model_copy(update={"provider": provider}))
    await db.flush()


async def test_a_provider_switch_mid_conversation_leaves_the_hold_on_wizscheduler(
        db_session, web_conv):
    assert web_conv.provider == "wizscheduler"
    now = _now()
    core = AgentCore(ScriptedLlm(*_hold_script(now)))
    out = await core.handle_turn(db_session, web_conv, TurnInput("Table for two", None),
                                 now=now)
    assert out.expect == "confirm_hold"
    held = web_conv.held_reservation_id
    await _switch(db_session, "mock_external")
    out = await core.handle_turn(db_session, web_conv, TurnInput(None, "confirm_hold"),
                                 now=now)
    assert "You're booked" in out.reply_text
    assert web_conv.provider == "wizscheduler" and web_conv.reservation_id == held
    res = await db_session.get(Reservation, held)
    assert res.status == "booked"
    assert await _count(db_session) == 0  # nothing went to the mock
    # A conversation started after the switch books through the mock.
    later = await recording.start_conversation(db_session, COMPANY_ID, LOCATION_ID, "web")
    assert later.provider == "mock_external"


async def test_a_code_is_only_found_through_its_own_provider(db_session, web_conv):
    """A WizScheduler code looked up by a mock conversation (and the reverse)
    is not found, even with the right phone."""
    wiz = provider_named("wizscheduler")
    mock = provider_named("mock_external")
    ctx = await _ctx(db_session)
    wiz_held = await wiz.hold(db_session, ctx, 2, _at(ctx, 19), name="Ada", phone=PHONE,
                              channel="web", conversation_id=web_conv.id)
    wiz_code = (await wiz.book(db_session, ctx, wiz_held.ref)).code
    mock_held = await _hold(db_session, ctx, _at(ctx, 20))
    mock_code = (await mock.book(db_session, ctx, mock_held.ref)).code

    assert await mock.lookup(db_session, ctx, code=wiz_code, phone=PHONE) is None
    assert await wiz.lookup(db_session, ctx, code=mock_code, phone=PHONE) is None
    assert await mock.lookup(db_session, ctx, ref=wiz_held.ref) is None
    assert await wiz.lookup(db_session, ctx, ref=mock_held.ref) is None

    conv = await _mock_conv(db_session, web_conv)
    now = _now()
    out = await tools.execute_tool(db_session, conv, "lookup_booking",
                                   {"confirmation_code": wiz_code, "phone": PHONE}, now=now)
    assert out.result == {"found": False} and conv.verified_reservation_id is None
    out = await tools.execute_tool(db_session, conv, "lookup_booking",
                                   {"confirmation_code": mock_code, "phone": PHONE}, now=now)
    assert out.result["found"] is True and conv.verified_reservation_id == mock_held.ref
