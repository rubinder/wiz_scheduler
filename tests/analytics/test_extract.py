"""extract.day (#259 step 10): one UTC day, every company, redacted."""

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from backend.analytics import extract, lake, schemas
from backend.models import Location, OwnershipGroup
from backend.reservations.agent.models import AgentConversation, AgentEvent
from backend.reservations.mock_external_models import MockExternalBooking
from backend.reservations.models import Reservation
from backend.reservations.screening_models import ScreenedCall
from backend.reservations.subscription_models import AgentUsage
from backend.services.rate_limit import source_key_for
from tests.conftest import COMPANY_ID, LOCATION_ID

pytestmark = [pytest.mark.integration]

DAY = date(2026, 11, 18)
NAME = "Zelda Quixotic"
PHONE = "+14155550123"
EMAIL = "zelda.q@example.org"
CODE = "QX7K9P"
# Every way the seeded identifiers could show up.
NEEDLES = [NAME, "Zelda", "Quixotic", PHONE, "4155550123", "555-0123", EMAIL, "example.org", CODE]


def at(d: date, h: int, m: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, h, m, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def seeded(db_session: AsyncSession, seed_location: Location) -> dict[str, str]:
    db = db_session
    db.add(OwnershipGroup(id="ogalyt01", name="Group", ai_credits_usd=0.0))
    before, after = DAY - timedelta(days=1), DAY + timedelta(days=1)

    def conv(started: datetime, **kw) -> AgentConversation:
        c = AgentConversation(company_id=COMPANY_ID, location_id=LOCATION_ID, channel="voice",
                              caller_number=PHONE, source_key=source_key_for(PHONE),
                              started_at=started, created_at=started, **kw)
        db.add(c)
        return c

    today = conv(at(DAY, 15), outcome="booked", status="closed",
                 cost_usd=Decimal("0.031000"), ended_at=at(DAY, 15, 5))
    # Starts 23:50 the day before, its events run past midnight.
    spanning = conv(at(before, 23, 50))
    late = conv(at(DAY, 23, 55))  # spans into the next day
    conv(at(after, 0, 1))
    await db.flush()

    def ev(c: AgentConversation, seq: int, type_: str, payload: dict, ts: datetime,
           **kw) -> None:
        db.add(AgentEvent(company_id=COMPANY_ID, conversation_id=c.id, seq=seq, type=type_,
                          payload=payload, created_at=ts, **kw))

    ctx = {"type": "text", "text": "[Now: 2026-11-18 10:00, Wednesday]"}
    words = f"Hi I'm {NAME}, call {PHONE} or (415) 555-0123, mail {EMAIL}, code {CODE}"
    ev(today, 1, "user_turn", {"content": [ctx, {"type": "text", "text": words}],
                               "raw": json.dumps([ctx, words])}, at(DAY, 15, 0))
    ev(today, 2, "assistant_turn",
       {"content": [{"type": "text", "text": f"Booking for {PHONE}"},
                    {"type": "tool_use", "id": "tu1", "name": "hold_slot",
                     "input": {"name": NAME, "phone": PHONE}}],
        "raw": NAME, "stop_reason": "tool_use"},
       at(DAY, 15, 1), latency_ms={"llm": 812.4}, input_tokens=100, output_tokens=20,
       cost_usd=Decimal("0.001234"))
    ev(today, 3, "tool_call", {"tool_use_id": "tu1", "name": "hold_slot",
                               "input": {"start_iso": "2026-11-20T19:00:00-05:00",
                                         "party_size": 2, "name": NAME, "phone": PHONE}},
       at(DAY, 15, 1))
    result = {"confirmation_code": CODE, "name": NAME, "party_size": 2, "status": "booked"}
    ev(today, 4, "tool_result",
       {"content": [{"type": "tool_result", "tool_use_id": "tu1",
                     "content": json.dumps(result)}],
        "raw": json.dumps(result)}, at(DAY, 15, 2), latency_ms={"tools": 45})
    ev(today, 5, "action", {"action": "confirm_hold", "result": f"booked {CODE}",
                            "reply": f"Booked, {NAME}! Code {CODE}, we'll text {PHONE}."},
       at(DAY, 15, 3))
    ev(today, 6, "handoff", {"reason": f"caller {EMAIL} asked for a manager"}, at(DAY, 15, 4))
    ev(today, 7, "error", {"error": "llm_error", "detail": f"{NAME} {PHONE}"}, at(DAY, 15, 4))
    ev(today, 8, "handoff", {"reason": "lookup_lockout"}, at(DAY, 15, 4))
    ev(today, 9, "action", {"action": f"call {NAME}", "result": f"{NAME} said hi",
                            "reply": "ok"}, at(DAY, 15, 4))
    ev(today, 10, "error", {"error": f"no booking {CODE.lower()} for {EMAIL}"}, at(DAY, 15, 4))
    ev(today, 11, "tool_call", {"tool_use_id": "tu2", "name": f"{NAME} {PHONE}",
                                "input": {"confirmation_code": CODE}}, at(DAY, 15, 4))
    ev(spanning, 1, "user_turn", {"content": [{"type": "text", "text": "late one"}]},
       at(before, 23, 55))
    ev(spanning, 2, "assistant_turn", {"content": [], "stop_reason": "end_turn"},
       at(DAY, 0, 5))
    ev(late, 1, "user_turn", {"erased": True}, at(DAY, 23, 56))
    ev(late, 2, "assistant_turn", {"content": []}, at(after, 0, 2))

    def res(created: datetime, **kw) -> Reservation:
        r = Reservation(company_id=COMPANY_ID, location_id=LOCATION_ID, party_size=2,
                        start_at=at(after, 19), end_at=at(after, 21), status="booked",
                        diner_name=NAME, diner_phone=PHONE, diner_email=EMAIL,
                        notes=f"{NAME} allergic", source="voice_agent",
                        created_at=created, updated_at=created, **kw)
        db.add(r)
        return r

    booked = res(at(DAY, 15, 3), conversation_id=today.id, confirmation_code=CODE)
    res(at(before, 12), confirmation_code="ZZ3456")
    db.add(MockExternalBooking(company_id=COMPANY_ID, location_id=LOCATION_ID,
                               start_at=at(after, 18), end_at=at(after, 20), party_size=4,
                               status="booked", code="ZZ2345", diner_name=NAME,
                               diner_phone=PHONE, created_at=at(DAY, 9), updated_at=at(DAY, 9)))
    db.add(MockExternalBooking(company_id=COMPANY_ID, location_id=LOCATION_ID,
                               start_at=at(after, 18), end_at=at(after, 20), party_size=4,
                               status="held", created_at=at(after, 9), updated_at=at(after, 9)))
    # A hold that lapsed: lands as expired.
    db.add(MockExternalBooking(company_id=COMPANY_ID, location_id=LOCATION_ID,
                               start_at=at(after, 18), end_at=at(after, 20), party_size=3,
                               status="held", held_until=at(DAY, 10, 10), diner_name=NAME,
                               diner_phone=PHONE, created_at=at(DAY, 10),
                               updated_at=at(DAY, 10)))
    db.add(AgentUsage(company_id=COMPANY_ID, ownership_group_id="ogalyt01",
                      location_id=LOCATION_ID, conversation_id=today.id,
                      cost_usd=Decimal("0.031000"), billed_usd=Decimal("0.25"),
                      period_start=at(DAY, 0), created_at=at(DAY, 15, 6)))
    db.add(ScreenedCall(company_id=COMPANY_ID, location_id=LOCATION_ID,
                        caller_key=source_key_for(PHONE), decision="answer",
                        reason="default", created_at=at(DAY, 14, 59)))
    db.add(ScreenedCall(company_id=COMPANY_ID, location_id=LOCATION_ID, caller_key=None,
                        decision="block", reason="withheld", created_at=at(after, 1)))
    await db.commit()
    return {"today": today.id, "spanning": spanning.id, "late": late.id, "booked": booked.id}


@pytest.mark.asyncio
async def test_conversations_land_on_their_start_day(db_session, seeded) -> None:
    out = await extract.day(db_session, DAY, runs_dir=None)
    ids = [c["conversation_id"] for c in out["conversations"]]
    assert ids == [seeded["today"], seeded["late"]]
    today = out["conversations"][0]
    assert today["billed_usd"] == Decimal("0.25")
    assert set(today) == set(schemas.CONVERSATIONS.names)
    prev = await extract.day(db_session, DAY - timedelta(days=1), runs_dir=None)
    assert [c["conversation_id"] for c in prev["conversations"]] == [seeded["spanning"]]


@pytest.mark.asyncio
async def test_events_land_on_their_own_day(db_session, seeded) -> None:
    out = await extract.day(db_session, DAY, runs_dir=None)
    keys = [(e["conversation_id"], e["seq"]) for e in out["events"]]
    assert (seeded["spanning"], 2) in keys and (seeded["spanning"], 1) not in keys
    assert (seeded["late"], 1) in keys and (seeded["late"], 2) not in keys
    assert len(out["events"]) == 11 + 1 + 1


@pytest.mark.asyncio
async def test_event_redaction(db_session, seeded) -> None:
    out = await extract.day(db_session, DAY, runs_dir=None)
    ev = {e["seq"]: e for e in out["events"] if e["conversation_id"] == seeded["today"]}
    for e in out["events"]:
        assert set(e) == set(schemas.EVENTS.names)
    # Free text never lands, only its length (the [Now: ...] block excluded).
    words = f"Hi I'm {NAME}, call {PHONE} or (415) 555-0123, mail {EMAIL}, code {CODE}"
    assert ev[1]["text_chars"] == len(words) and ev[1]["payload"] is None
    assert ev[2]["text_chars"] == len(f"Booking for {PHONE}")
    assert json.loads(ev[2]["payload"]) == {"outcome": "tool_use"}
    assert ev[2]["llm_latency_ms"] == 812 and ev[2]["tools_latency_ms"] is None
    assert json.loads(ev[3]["payload"]) == {
        "name": "hold_slot", "party_size": 2, "start": "2026-11-20T19:00:00-05:00",
        "code_present": False}
    assert json.loads(ev[4]["payload"]) == [{"name": "hold_slot", "party_size": 2,
                                             "code_present": True, "outcome": "ok"}]
    assert ev[4]["tools_latency_ms"] == 45
    # The result carried the code ("booked QX7K9P"): only its label lands.
    assert json.loads(ev[5]["payload"]) == {"name": "confirm_hold", "outcome": "booked"}
    assert ev[5]["text_chars"] > 0
    # A free-form handoff reason is "other"; a fixed code is kept.
    assert json.loads(ev[6]["payload"]) == {"reason": "other"}
    assert ev[6]["text_chars"] == len(f"caller {EMAIL} asked for a manager")
    assert json.loads(ev[8]["payload"]) == {"reason": "lookup_lockout"}
    assert ev[8]["text_chars"] is None
    assert json.loads(ev[7]["payload"]) == {"error": "llm_error", "code_present": False}
    assert json.loads(ev[9]["payload"]) == {"name": "unknown", "outcome": "other"}
    # The day's known code is masked exactly, whatever its case.
    assert json.loads(ev[10]["payload"])["error"] == "no booking [code] for [email]"
    assert json.loads(ev[11]["payload"]) == {"name": "unknown", "code_present": True}
    erased = next(e for e in out["events"] if e["conversation_id"] == seeded["late"])
    assert erased["text_chars"] is None and erased["payload"] is None


@pytest.mark.asyncio
async def test_reservations_from_both_providers(db_session, seeded) -> None:
    out = await extract.day(db_session, DAY, runs_dir=None)
    rows = out["reservations"]
    assert sorted((r["provider"], r["status"]) for r in rows) == [
        ("mock_external", "booked"), ("mock_external", "expired"), ("wizscheduler", "booked")]
    wiz = next(r for r in rows if r["provider"] == "wizscheduler")
    mock = next(r for r in rows if r["provider"] == "mock_external" and r["status"] == "booked")
    assert wiz["reservation_id"] == seeded["booked"]
    assert wiz["conversation_id"] == seeded["today"] and wiz["source"] == "voice_agent"
    assert mock["source"] is None and mock["party_size"] == 4
    for r in rows:
        assert set(r) == set(schemas.RESERVATIONS.names)


@pytest.mark.asyncio
async def test_usage_and_screened_calls(db_session, seeded) -> None:
    out = await extract.day(db_session, DAY, runs_dir=None)
    assert [u["conversation_id"] for u in out["agent_usage"]] == [seeded["today"]]
    assert [(s["decision"], s["reason"]) for s in out["screened_calls"]] == [("answer", "default")]


@pytest.mark.asyncio
async def test_eval_results_skipped_without_a_runs_directory(db_session, seeded,
                                                             tmp_path: Path) -> None:
    out = await extract.day(db_session, DAY, runs_dir=tmp_path / "missing")
    assert "eval_results" not in out
    assert extract.skipped(out) == ["eval_results"]


def test_eval_results_by_run_date(tmp_path: Path) -> None:
    def run(run_id: str, created: str) -> None:
        (tmp_path / f"{run_id}.json").write_text(json.dumps({
            "id": run_id, "created_at": created,
            "meta": {"git_sha": "abc", "agent_model": "m", "mode": "replay"},
            "results": [{"scenario_id": "s1", "attempt": 0, "passed": False,
                         "failures": [f"expected code {CODE} for {PHONE}"],
                         "metrics": {"llm_ms_p50": 800},
                         "conversation_snapshot": {"diner_name": NAME}}],
            "report": NAME}))

    run("r1", "2026-11-18T23:30:00+00:00")
    (tmp_path / "v1.json").write_text(json.dumps({
        "id": "v1", "created_at": "2026-11-18T10:00:00Z", "meta": {"recognizer": "noisy"},
        "results": [{"scenario_id": "s2", "attempt": 1, "passed": True, "failures": [],
                     "metrics": {"turns": 4, "outcome": "booked", "llm_ms": [1, 2],
                                 "errors": ["llm_error"], "handoff": False,
                                 "total_cost_usd": 0.02, "surprise": NAME,
                                 "voice": {"recognizer": "noisy", "heard": [NAME, PHONE],
                                           "errors": [f"misheard {NAME}"], "repeats": [{}],
                                           "asr_ms": [10], "ssml_errors": []}}}]}))
    run("r2", "2026-11-18T20:00:00-05:00")  # 01:00Z on the 19th
    (tmp_path / "junk.json").write_text("{not json")
    rows = extract.eval_results(DAY, tmp_path)
    assert [r["run_id"] for r in rows] == ["r1", "v1"]
    assert json.loads(rows[0]["failures"]) == ["expected_code"]
    assert rows[0]["failure_count"] == 1 and rows[1]["failure_count"] == 0
    assert json.loads(rows[1]["metrics"]) == {
        "turns": 4, "outcome": "booked", "handoff": False, "total_cost_usd": 0.02,
        "error_count": 1,
        "voice": {"recognizer": "noisy", "heard_count": 2, "error_count": 1,
                  "repeat_count": 1, "ssml_error_count": 0}}
    assert [r["run_id"] for r in extract.eval_results(DAY + timedelta(days=1), tmp_path)] == ["r2"]


# checker.py's own formats, verbatim shapes (expected booking not found
# quotes the scenario diner's name and phone after "for").
CHECKER_FAILURES = [
    (f"expected booking not found: party 2 2026-11-20 19:00 booked for {NAME} {PHONE} "
     f"code {CODE}; found party 2 2026-11-20 19:00 booked code={CODE} name='{NAME}' "
     f"phone={PHONE} (wrong: phone)", "expected_booking_not_found"),
    (f"unexpected booking: party 2 2026-11-20 19:00 booked code=None name='{NAME}' "
     f"phone={PHONE}", "unexpected_booking"),
    ("outcome 'abandoned', expected one of ['booked']", "outcome"),
    (f"kept booking {CODE} is missing", "kept_booking"),
    ("12 turns, more than max_turns 10", "turns"),
    ("handoff without handoff_to_staff (reason Jane wanted out)",
     "handoff_without_handoff_to_staff"),
    ("error event: llm_error", "error_event"),
    ("conversation is not at the scenario's location", "conversation_is_not_at_the"),
    (f"ssml_invalid: <say>{NAME}</say>", "ssml_invalid"),
    (f"{NAME}: {PHONE}", "other"),
    ("", "other"),
]


@pytest.mark.parametrize("failure,category", CHECKER_FAILURES)
def test_eval_failures_land_as_categories(failure: str, category: str) -> None:
    assert extract.failure_category(failure) == category


def test_eval_failures_drop_their_text(tmp_path: Path) -> None:
    (tmp_path / "r.json").write_text(json.dumps({
        "id": "r", "created_at": "2026-11-18T01:00:00+00:00",
        "results": [{"scenario_id": "s", "attempt": 0, "passed": False,
                     "failures": [f for f, _ in CHECKER_FAILURES]}]}))
    rows = extract.eval_results(DAY, tmp_path)
    assert json.loads(rows[0]["failures"]) == [c for _, c in CHECKER_FAILURES]
    assert rows[0]["failure_count"] == len(CHECKER_FAILURES)


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


@pytest.mark.asyncio
async def test_no_identifier_reaches_the_parquet_files(db_session, seeded, tmp_path: Path) -> None:
    """Every table, landed and read back: no seeded name, phone, email or
    code in any string value, nor in the file bytes."""
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "r1.json").write_text(json.dumps({
        "id": "r1", "created_at": "2026-11-18T12:00:00+00:00", "meta": {},
        "results": [{"scenario_id": "s1", "attempt": 0, "passed": False,
                     "failures": [f"{PHONE} {EMAIL}",
                                  *(f for f, _ in CHECKER_FAILURES)],
                     "metrics": {"voice": {"recognizer": "noisy",
                                           "heard": [f"I'm {NAME}", PHONE],
                                           "errors": [f"misheard {NAME}"]}},
                     "conversation_snapshot": {"diner_name": NAME}}]}))
    out = await extract.day(db_session, DAY, runs_dir=runs)
    sink = lake.LocalSink(tmp_path / "lake")
    for table, schema in schemas.TABLES.items():
        assert out[table], table  # every table has rows to scan
        assert lake.write_partition(table, DAY, out[table], schema, sink=sink) == len(out[table])

    files = sorted((tmp_path / "lake").rglob("*.parquet"))
    assert len(files) == len(schemas.TABLES)
    needles = NEEDLES + ["ZZ2345", source_key_for(PHONE)]
    for path in files:
        raw = path.read_bytes()
        for row in pq.read_table(path).to_pylist():
            for s in _strings(row):
                for needle in needles:
                    assert needle.lower() not in s.lower(), (path, s)
        for needle in needles:
            assert needle.encode() not in raw, path
