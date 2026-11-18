from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from backend.reservations import booking
from backend.reservations.agent import recording, tools
from backend.reservations.agent.models import AgentEvent, ReservationLookupFailure
from backend.reservations.agent.tools import TOOLS, execute_tool
from backend.reservations.booking import DinerContact
from backend.reservations.models import BOOKED, HELD, PENDING, Reservation
from tests.conftest import COMPANY_ID, LOCATION_ID

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NY = ZoneInfo("America/New_York")
PHONE = "+12125550100"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _day(now: datetime) -> str:
    return (now.astimezone(NY).date() + timedelta(days=7)).isoformat()


def _local(day: str, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(datetime.fromisoformat(day).date(), time(hh, mm), tzinfo=NY)


async def _find(db, conv, now, party=2, earliest=None, latest=None):
    return await execute_tool(db, conv, "find_slots", {
        "party_size": party, "date": _day(now), "earliest": earliest, "latest": latest,
    }, now=now)


async def _hold(db, conv, now, start_iso, party=2, phone=PHONE):
    return await execute_tool(db, conv, "hold_slot", {
        "start_iso": start_iso, "party_size": party, "name": "Ada", "phone": phone,
    }, now=now)


async def _booked(db, now, party=2, hh=19) -> Reservation:
    res = await booking.hold(db, COMPANY_ID, LOCATION_ID, party, _local(_day(now), hh),
                             source="web_agent", now=now,
                             diner=DinerContact(name="Ada", phone=PHONE))
    return await booking.confirm(db, COMPANY_ID, res.id, now=now)


async def test_tool_schemas_are_strict():
    names = {t["name"] for t in TOOLS}
    assert names == {"find_slots", "hold_slot", "lookup_booking", "modify_booking",
                     "cancel_booking", "handoff_to_staff", "restaurant_info"}
    for t in TOOLS:
        assert t["strict"] is True
        schema = t["input_schema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert sorted(schema["required"]) == sorted(schema["properties"])


async def test_find_slots_spreads_options_and_records_offers(db_session, web_conv):
    now = _now()
    out = await _find(db_session, web_conv, now)
    assert not out.is_error and out.expect is None
    opts = out.result["options"]
    assert len(opts) == 6
    # Dinner 17:00-21:00 every 15 min: spread across the window, not the first six.
    assert opts[0]["local_time"] == "17:00" and opts[-1]["local_time"] == "21:00"
    assert all(o["local_date"] == _day(now) for o in opts)
    offered = web_conv.offered_starts
    assert [o["start_iso"] for o in offered] == [o["start_iso"] for o in opts]
    assert all(o["party_size"] == 2 for o in offered)
    # A repeat search does not duplicate offers.
    await _find(db_session, web_conv, now)
    assert len(web_conv.offered_starts) == 6
    await db_session.refresh(web_conv)
    assert len(web_conv.offered_starts) == 6


async def test_find_slots_window_is_local_time(db_session, web_conv):
    now = _now()
    out = await _find(db_session, web_conv, now, earliest="19:00", latest="19:30")
    assert [o["local_time"] for o in out.result["options"]] == ["19:00", "19:15", "19:30"]


async def test_find_slots_rejects_bad_input(db_session, web_conv):
    now = _now()
    out = await execute_tool(db_session, web_conv, "find_slots", {
        "party_size": 2, "date": "next friday", "earliest": None, "latest": None,
    }, now=now)
    assert out.is_error and out.result == {"error": "invalid_input"}
    out = await _find(db_session, web_conv, now, party=99)
    assert out.is_error and out.result == {"error": "party_too_large"}
    assert web_conv.offered_starts == []


async def test_hold_of_a_time_never_offered_is_rejected(db_session, web_conv):
    now = _now()
    out = await _hold(db_session, web_conv, now, _local(_day(now), 19).isoformat())
    assert out.is_error and out.result == {"error": "not_offered"}
    assert web_conv.held_reservation_id is None
    assert (await db_session.execute(select(Reservation))).scalars().all() == []


async def test_hold_needs_the_party_size_that_was_offered(db_session, web_conv):
    now = _now()
    opts = (await _find(db_session, web_conv, now)).result["options"]
    out = await _hold(db_session, web_conv, now, opts[0]["start_iso"], party=4)
    assert out.result == {"error": "not_offered"}


async def test_hold_of_an_offered_time_holds_with_diner(db_session, web_conv):
    now = _now()
    opts = (await _find(db_session, web_conv, now, earliest="19:00", latest="19:00")
            ).result["options"]
    # Same instant written in UTC still matches the offer.
    utc_iso = datetime.fromisoformat(opts[0]["start_iso"]).astimezone(timezone.utc).isoformat()
    out = await _hold(db_session, web_conv, now, utc_iso)
    assert not out.is_error, out.result
    assert out.expect == "confirm_hold"
    assert out.result["status"] == "held"
    assert out.result["local_time"] == "19:00" and out.result["party_size"] == 2
    assert out.result["expires_in_minutes"] == 5
    res = await db_session.get(Reservation, out.result["hold_id"])
    assert (res.status, res.diner_name, res.diner_phone, res.source, res.conversation_id) == (
        HELD, "Ada", PHONE, "web_agent", web_conv.id)
    assert web_conv.held_reservation_id == res.id


async def test_large_party_hold_is_pending(db_session, web_conv):
    now = _now()
    # Above max_self_book_party (8) goes to approval: rules allow 20 but the
    # floor seats at most 6, so check status mapping through a party of 6 at
    # a lowered self-book limit.
    from backend.reservations.rules import get_rules, save_rules
    rules, _ = await get_rules(db_session, COMPANY_ID, LOCATION_ID)
    await save_rules(db_session, COMPANY_ID, LOCATION_ID,
                     rules.model_copy(update={"max_self_book_party": 4}))
    opts = (await _find(db_session, web_conv, now, party=6)).result["options"]
    out = await _hold(db_session, web_conv, now, opts[0]["start_iso"], party=6)
    assert out.result["status"] == "pending_approval", out.result
    assert out.expect == "confirm_hold"
    res = await db_session.get(Reservation, out.result["hold_id"])
    assert res.status == PENDING


async def test_hold_with_bad_phone_is_invalid_phone(db_session, web_conv):
    now = _now()
    opts = (await _find(db_session, web_conv, now)).result["options"]
    out = await _hold(db_session, web_conv, now, opts[0]["start_iso"], phone="212-555-0100")
    assert out.is_error and out.result == {"error": "invalid_phone"}
    assert web_conv.held_reservation_id is None


async def test_failed_hold_leaves_conversation_unchanged_and_session_usable(
        db_session, web_conv):
    now = _now()
    opts = (await _find(db_session, web_conv, now, party=3, earliest="19:00",
                        latest="19:00")).result["options"]
    # Party 3 fits only T2; take it first so the hold loses the race.
    await _booked(db_session, now, party=3, hh=19)
    out = await _hold(db_session, web_conv, now, opts[0]["start_iso"], party=3)
    assert out.is_error and out.result == {"error": "slot_unavailable"}
    assert web_conv.held_reservation_id is None
    # The session still works after the failed write.
    assert (await _find(db_session, web_conv, now)).is_error is False


async def test_lookup_wrong_phone_is_not_found_and_not_verified(db_session, web_conv):
    now = _now()
    res = await _booked(db_session, now)
    out = await execute_tool(db_session, web_conv, "lookup_booking", {
        "confirmation_code": res.confirmation_code, "phone": "+12125550199"}, now=now)
    assert out.result == {"found": False}
    assert web_conv.verified_reservation_id is None
    wrong_code = await execute_tool(db_session, web_conv, "lookup_booking", {
        "confirmation_code": "ZZZZZZ", "phone": PHONE}, now=now)
    assert wrong_code.result == out.result
    assert wrong_code.is_error == out.is_error


async def test_lookup_right_code_and_phone_verifies(db_session, web_conv):
    now = _now()
    res = await _booked(db_session, now)
    out = await execute_tool(db_session, web_conv, "lookup_booking", {
        "confirmation_code": res.confirmation_code.lower(), "phone": PHONE}, now=now)
    assert out.result["found"] is True
    assert out.result["local_time"] == "19:00" and out.result["status"] == BOOKED
    assert web_conv.verified_reservation_id == res.id


async def test_modify_and_cancel_need_verification(db_session, web_conv):
    now = _now()
    await _booked(db_session, now)
    out = await execute_tool(db_session, web_conv, "modify_booking",
                             {"party_size": 3, "start_iso": None}, now=now)
    assert out.is_error and out.result == {"error": "not_verified"}
    out = await execute_tool(db_session, web_conv, "cancel_booking", {}, now=now)
    assert out.is_error and out.result == {"error": "not_verified"}


async def _verify(db, conv, now, res):
    await execute_tool(db, conv, "lookup_booking", {
        "confirmation_code": res.confirmation_code, "phone": PHONE}, now=now)
    assert conv.verified_reservation_id == res.id


async def test_modify_time_must_be_offered(db_session, web_conv):
    now = _now()
    res = await _booked(db_session, now)
    await _verify(db_session, web_conv, now, res)
    target = _local(_day(now), 20).isoformat()
    out = await execute_tool(db_session, web_conv, "modify_booking",
                             {"party_size": None, "start_iso": target}, now=now)
    assert out.result == {"error": "not_offered"}
    await _find(db_session, web_conv, now, earliest="20:00", latest="20:00")
    out = await execute_tool(db_session, web_conv, "modify_booking",
                             {"party_size": None, "start_iso": target}, now=now)
    assert not out.is_error, out.result
    assert out.expect == "confirm_modify"
    assert out.result["proposed"]["local_time"] == "20:00"
    assert out.result["current"]["local_time"] == "19:00"
    # Staged, not written: the confirm_modify action applies it.
    assert web_conv.pending_modify == {"start_iso": target, "party_size": None}
    await db_session.refresh(res)
    assert res.start_at == _local(_day(now), 19)


async def test_modify_rejects_unavailable_or_too_large(db_session, web_conv):
    now = _now()
    res = await _booked(db_session, now)
    await _verify(db_session, web_conv, now, res)
    out = await execute_tool(db_session, web_conv, "modify_booking",
                             {"party_size": 9, "start_iso": None}, now=now)
    assert out.is_error and out.result == {"error": "party_too_large"}
    # res holds T1, so a party of 6 takes T3; a party of 2 then takes T2.
    # Growing res to 6 would need T3 or T1+T2: neither is free.
    for party in (6, 2):
        await _booked(db_session, now, party=party, hh=19)
    out = await execute_tool(db_session, web_conv, "modify_booking",
                             {"party_size": 6, "start_iso": None}, now=now)
    assert out.is_error and out.result == {"error": "slot_unavailable"}
    assert web_conv.pending_modify is None


async def test_a_new_lookup_clears_staged_changes(db_session, web_conv):
    now = _now()
    res = await _booked(db_session, now)
    await _verify(db_session, web_conv, now, res)
    await execute_tool(db_session, web_conv, "cancel_booking", {}, now=now)
    assert web_conv.pending_cancel_id == res.id
    await execute_tool(db_session, web_conv, "lookup_booking", {
        "confirmation_code": "NOPE00", "phone": PHONE}, now=now)
    assert web_conv.pending_cancel_id is None and web_conv.pending_modify is None


async def test_cancel_asks_for_confirmation_and_does_not_cancel(db_session, web_conv):
    now = _now()
    res = await _booked(db_session, now)
    await _verify(db_session, web_conv, now, res)
    out = await execute_tool(db_session, web_conv, "cancel_booking", {}, now=now)
    assert not out.is_error and out.expect == "confirm_cancel"
    assert web_conv.pending_cancel_id == res.id
    await db_session.refresh(res)
    assert res.status == BOOKED


async def test_handoff_records_an_event(db_session, web_conv):
    now = _now()
    out = await execute_tool(db_session, web_conv, "handoff_to_staff",
                             {"reason": "allergy question"}, now=now)
    assert not out.is_error and out.expect == "handoff"
    assert out.result["handed_off"] is True and out.result["phone"] is None
    events = (await db_session.execute(select(AgentEvent).where(
        AgentEvent.conversation_id == web_conv.id))).scalars().all()
    assert [(e.type, e.payload) for e in events] == [
        ("handoff", {"reason": "allergy question"})]


async def test_restaurant_info_lists_today_periods(db_session, web_conv):
    now = _now()
    out = await execute_tool(db_session, web_conv, "restaurant_info", {}, now=now)
    assert out.result["today"] == now.astimezone(NY).date().isoformat()
    assert out.result["service_periods"] == [
        {"name": "dinner", "first_seating": "17:00", "last_seating": "21:00"}]
    assert out.result["facts"] == []


async def test_unknown_tool_and_unexpected_errors_never_raise(db_session, web_conv,
                                                             monkeypatch):
    now = _now()
    out = await execute_tool(db_session, web_conv, "launch_rockets", {}, now=now)
    assert out.is_error and out.result == {"error": "unknown_tool"}

    async def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(tools, "find_slots", boom)
    out = await _find(db_session, web_conv, now)
    assert out.is_error and out.result == {"error": "internal"}


async def test_a_second_hold_releases_the_first(db_session, web_conv):
    now = _now()
    opts = (await _find(db_session, web_conv, now)).result["options"]
    first = (await _hold(db_session, web_conv, now, opts[0]["start_iso"])).result["hold_id"]
    second = (await _hold(db_session, web_conv, now, opts[1]["start_iso"])).result["hold_id"]
    assert web_conv.held_reservation_id == second
    assert (await db_session.get(Reservation, first)).status == "cancelled"
    assert (await db_session.get(Reservation, second)).status == HELD


async def _self_book_limit(db, limit: int) -> None:
    from backend.reservations.rules import get_rules, save_rules
    rules, _ = await get_rules(db, COMPANY_ID, LOCATION_ID)
    await save_rules(db, COMPANY_ID, LOCATION_ID,
                     rules.model_copy(update={"max_self_book_party": limit}))


async def test_a_submitted_pending_request_survives_a_new_hold(db_session, web_conv):
    now = _now()
    await _self_book_limit(db_session, 4)
    opts = (await _find(db_session, web_conv, now, party=6)).result["options"]
    pending_id = (await _hold(db_session, web_conv, now, opts[0]["start_iso"],
                              party=6)).result["hold_id"]
    await booking.confirm(db_session, COMPANY_ID, pending_id, now=now)
    small = (await _find(db_session, web_conv, now)).result["options"]
    out = await _hold(db_session, web_conv, now, small[-1]["start_iso"])
    assert not out.is_error, out.result
    pending = await db_session.get(Reservation, pending_id)
    assert pending.status == PENDING and pending.confirmation_code


async def test_re_hold_can_reuse_the_tables_of_the_released_hold(db_session, web_conv):
    now = _now()
    # Party 3 fits only T2, so the second hold needs the first one's table.
    opts = (await _find(db_session, web_conv, now, party=3, earliest="19:00",
                        latest="19:15")).result["options"]
    first = (await _hold(db_session, web_conv, now, opts[0]["start_iso"], party=3)
             ).result["hold_id"]
    out = await _hold(db_session, web_conv, now, opts[1]["start_iso"], party=3)
    assert not out.is_error, out.result
    assert (await db_session.get(Reservation, first)).status == "cancelled"
    assert web_conv.held_reservation_id == out.result["hold_id"]


async def test_failed_re_hold_restores_the_previous_hold(db_session, web_conv):
    now = _now()
    late = (await _find(db_session, web_conv, now, party=3, earliest="19:00",
                        latest="19:00")).result["options"]
    first = (await _hold(db_session, web_conv, now, late[0]["start_iso"], party=3)
             ).result["hold_id"]
    early = (await _find(db_session, web_conv, now, party=3, earliest="17:00",
                         latest="17:00")).result["options"]
    await _booked(db_session, now, party=3, hh=17)  # T2 at 17:00 goes elsewhere
    out = await _hold(db_session, web_conv, now, early[0]["start_iso"], party=3)
    assert out.result == {"error": "slot_unavailable"}
    assert web_conv.held_reservation_id == first
    old = await db_session.get(Reservation, first)
    await db_session.refresh(old)
    assert old.status == HELD


async def test_failed_lookup_clears_verification(db_session, web_conv):
    now = _now()
    res = await _booked(db_session, now)
    await _verify(db_session, web_conv, now, res)
    out = await execute_tool(db_session, web_conv, "lookup_booking", {
        "confirmation_code": "ZZZZZZ", "phone": PHONE}, now=now)
    assert out.result == {"found": False}
    assert web_conv.verified_reservation_id is None


async def test_lookups_stop_after_three_failures(db_session, web_conv, monkeypatch):
    now = _now()
    res = await _booked(db_session, now)
    for _ in range(3):
        out = await execute_tool(db_session, web_conv, "lookup_booking", {
            "confirmation_code": "ZZZZZZ", "phone": PHONE}, now=now)
        assert out.result == {"found": False}
    calls = []

    async def counting(*a, **k):
        calls.append(a)
        return None

    monkeypatch.setattr(booking, "lookup", counting)
    out = await execute_tool(db_session, web_conv, "lookup_booking", {
        "confirmation_code": res.confirmation_code, "phone": PHONE}, now=now)
    assert out.is_error and out.result == {"error": "too_many_attempts"}
    assert calls == [] and web_conv.verified_reservation_id is None
    await db_session.refresh(web_conv)
    assert web_conv.failed_lookups == 3


async def _wrong_lookups(db, n: int, now, phone=PHONE, source=None) -> None:
    """n wrong lookups for phone, each from a fresh conversation."""
    for _ in range(n):
        conv = await recording.start_conversation(db, COMPANY_ID, LOCATION_ID, "web",
                                                  source_key=source)
        out = await execute_tool(db, conv, "lookup_booking", {
            "confirmation_code": "ZZZZZZ", "phone": phone}, now=now)
        assert out.result == {"found": False}
    await db.commit()


async def test_lookup_lockout_is_per_viewer_until_the_phone_wide_handoff(db_session,
                                                                        seeded_floor):
    now = _now()
    res = await _booked(db_session, now)
    args = {"confirmation_code": res.confirmation_code, "phone": PHONE}
    await _wrong_lookups(db_session, 5, now, source="198.51.100.1")
    rows = (await db_session.execute(select(ReservationLookupFailure))).scalars().all()
    assert {r.source_key for r in rows} == {"198.51.100.1"}
    same = await recording.start_conversation(db_session, COMPANY_ID, LOCATION_ID, "web",
                                              source_key="198.51.100.1")
    out = await execute_tool(db_session, same, "lookup_booking", args, now=now)
    assert out.result == {"error": "too_many_attempts"}
    # Another diner looking up the same phone is not locked out by them...
    other = await recording.start_conversation(db_session, COMPANY_ID, LOCATION_ID,
                                               "web", source_key="198.51.100.2")
    out = await execute_tool(db_session, other, "lookup_booking", args, now=now)
    assert out.result["found"] is True
    # ...until the phone-wide figure, from many viewers: hand off to staff.
    for i in range(15):
        await _wrong_lookups(db_session, 1, now, source=f"203.0.113.{i}")
    late = await recording.start_conversation(db_session, COMPANY_ID, LOCATION_ID, "web",
                                              source_key="198.51.100.3")
    out = await execute_tool(db_session, late, "lookup_booking", args, now=now)
    assert out.is_error and out.result == {"error": "too_many_attempts", "handoff": True}
    assert late.verified_reservation_id is None


async def test_lookup_lockout_spans_conversations(db_session, web_conv, monkeypatch):
    now = _now()
    res = await _booked(db_session, now)
    await _wrong_lookups(db_session, 5, now)
    rows = (await db_session.execute(select(ReservationLookupFailure))).scalars().all()
    assert len(rows) == 5 and {r.phone for r in rows} == {PHONE}
    assert {(r.company_id, r.location_id) for r in rows} == {(COMPANY_ID, LOCATION_ID)}
    calls = []

    async def counting(*a, **k):
        calls.append(a)
        return None

    monkeypatch.setattr(booking, "lookup", counting)
    # A new conversation, a right code: refused without querying.
    out = await execute_tool(db_session, web_conv, "lookup_booking", {
        "confirmation_code": res.confirmation_code, "phone": f" {PHONE} "}, now=now)
    assert out.is_error and out.result == {"error": "too_many_attempts"}
    assert calls == [] and web_conv.verified_reservation_id is None


async def test_lookup_lockout_is_per_phone_and_expires(db_session, web_conv):
    now = _now()
    res = await _booked(db_session, now)
    await _wrong_lookups(db_session, 4, now)
    await _wrong_lookups(db_session, 3, now, phone="+12125550199")
    # Four failures for this phone: still allowed.
    out = await execute_tool(db_session, web_conv, "lookup_booking", {
        "confirmation_code": res.confirmation_code, "phone": PHONE}, now=now)
    assert out.result["found"] is True

    await _wrong_lookups(db_session, 1, now)
    conv = await recording.start_conversation(db_session, COMPANY_ID, LOCATION_ID, "web")
    args = {"confirmation_code": res.confirmation_code, "phone": PHONE}
    out = await execute_tool(db_session, conv, "lookup_booking", args, now=now)
    assert out.result == {"error": "too_many_attempts"}
    later = now + timedelta(minutes=16)
    out = await execute_tool(db_session, conv, "lookup_booking", args, now=later)
    assert out.result["found"] is True


async def test_retention_purges_day_old_lookup_failures(db_session, web_conv):
    from backend.services.data_retention import run_data_retention

    now = _now()
    for age in (timedelta(days=2), timedelta(hours=1)):
        db_session.add(ReservationLookupFailure(
            company_id=COMPANY_ID, location_id=LOCATION_ID, phone=PHONE,
            created_at=now - age))
    await db_session.commit()
    summary = await run_data_retention(db_session)
    await db_session.commit()
    assert summary["old_lookup_failures_deleted"] == 1
    left = (await db_session.execute(select(ReservationLookupFailure))).scalars().all()
    assert len(left) == 1


async def test_retention_purges_conversations_past_the_reservations_window(
        db_session, web_conv):
    from sqlalchemy import func

    from backend.reservations.agent.models import AgentConversation
    from backend.services.data_retention import run_data_retention

    old = await recording.start_conversation(db_session, COMPANY_ID, LOCATION_ID, "web")
    old.started_at = _now() - timedelta(days=366)
    await recording.record_event(db_session, old, "error", {"error": "x"})
    await recording.record_event(db_session, web_conv, "error", {"error": "y"})
    await db_session.commit()
    summary = await run_data_retention(db_session)
    await db_session.commit()
    assert summary["old_agent_conversations_deleted"] == 1
    ids = set((await db_session.execute(select(AgentConversation.id))).scalars())
    assert ids == {web_conv.id}
    events = (await db_session.execute(
        select(AgentEvent.conversation_id, func.count()).group_by(
            AgentEvent.conversation_id))).all()
    assert events == [(web_conv.id, 1)]


async def test_offered_starts_keep_the_most_recent_30(db_session, web_conv):
    now = _now()
    base = now.astimezone(NY).date() + timedelta(days=7)
    batches = []
    for i in range(6):
        out = await execute_tool(db_session, web_conv, "find_slots", {
            "party_size": 2, "date": (base + timedelta(days=i)).isoformat(),
            "earliest": None, "latest": None}, now=now)
        batches.append([o["start_iso"] for o in out.result["options"]])
    offered = [o["start_iso"] for o in web_conv.offered_starts]
    assert len(offered) == 30
    assert offered == [s for b in batches[1:] for s in b]
    # The dropped offer can no longer be held.
    out = await _hold(db_session, web_conv, now, batches[0][0])
    assert out.result == {"error": "not_offered"}


@pytest.mark.parametrize("bad", ["19:00:00", "19:00+01:00", "7pm", "24:00", "9:00"])
async def test_find_slots_accepts_only_hh_mm(db_session, web_conv, bad):
    out = await _find(db_session, web_conv, _now(), earliest=bad)
    assert out.is_error and out.result == {"error": "invalid_time"}


async def test_staging_a_cancel_drops_a_staged_modify_and_vice_versa(db_session, web_conv):
    """Only the request the diner was last shown can be confirmed."""
    now = _now()
    res = await _booked(db_session, now)
    await _verify(db_session, web_conv, now, res)
    out = await execute_tool(db_session, web_conv, "modify_booking",
                             {"party_size": 3, "start_iso": None}, now=now)
    assert not out.is_error, out.result
    assert web_conv.pending_modify is not None
    await execute_tool(db_session, web_conv, "cancel_booking", {}, now=now)
    assert web_conv.pending_cancel_id == res.id and web_conv.pending_modify is None
    out = await execute_tool(db_session, web_conv, "modify_booking",
                             {"party_size": 3, "start_iso": None}, now=now)
    assert not out.is_error, out.result
    assert web_conv.pending_modify == {"start_iso": None, "party_size": 3}
    assert web_conv.pending_cancel_id is None
