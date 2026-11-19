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
                    {"type": "tool_use", "id": "tu1", "name": "hold_table",
                     "input": {"name": NAME, "phone": PHONE}}],
        "raw": NAME, "stop_reason": "tool_use"},
       at(DAY, 15, 1), latency_ms={"llm": 812.4}, input_tokens=100, output_tokens=20,
       cost_usd=Decimal("0.001234"))
    ev(today, 3, "tool_call", {"tool_use_id": "tu1", "name": "hold_table",
                               "input": {"start_iso": "2026-11-20T19:00:00-05:00",
                                         "party_size": 2, "name": NAME, "phone": PHONE}},
       at(DAY, 15, 1))
    result = {"confirmation_code": CODE, "name": NAME, "party_size": 2, "status": "booked"}
    ev(today, 4, "tool_result",
       {"content": [{"type": "tool_result", "tool_use_id": "tu1",
                     "content": json.dumps(result)}],
        "raw": json.dumps(result)}, at(DAY, 15, 2), latency_ms={"tools": 45})
    ev(today, 5, "action", {"action": "confirm_hold", "result": "booked",
                            "reply": f"Booked, {NAME}! Code {CODE}, we'll text {PHONE}."},
       at(DAY, 15, 3))
    ev(today, 6, "handoff", {"reason": f"caller {EMAIL} asked for a manager"}, at(DAY, 15, 4))
    ev(today, 7, "error", {"error": "llm_error", "detail": f"{NAME} {PHONE}"}, at(DAY, 15, 4))
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
    assert today["source_key"] == source_key_for(PHONE)
    assert "caller_number" not in today
    prev = await extract.day(db_session, DAY - timedelta(days=1), runs_dir=None)
    assert [c["conversation_id"] for c in prev["conversations"]] == [seeded["spanning"]]


@pytest.mark.asyncio
async def test_events_land_on_their_own_day(db_session, seeded) -> None:
    out = await extract.day(db_session, DAY, runs_dir=None)
    keys = [(e["conversation_id"], e["seq"]) for e in out["events"]]
    assert (seeded["spanning"], 2) in keys and (seeded["spanning"], 1) not in keys
    assert (seeded["late"], 1) in keys and (seeded["late"], 2) not in keys
    assert len(out["events"]) == 7 + 1 + 1


@pytest.mark.asyncio
async def test_event_redaction(db_session, seeded) -> None:
    out = await extract.day(db_session, DAY, runs_dir=None)
    ev = {e["seq"]: e for e in out["events"] if e["conversation_id"] == seeded["today"]}
    # redact.text cannot recognise a name the diner types; see extract's docstring.
    assert ev[1]["text"] == f"Hi I'm {NAME}, call [phone] or [phone], mail [email], code [code]"
    assert ev[2]["text"] == "Booking for [phone]"
    assert ev[2]["llm_latency_ms"] == 812 and ev[2]["tools_latency_ms"] is None
    assert json.loads(ev[3]["payload"]) == {
        "name": "hold_table", "party_size": 2, "start": "2026-11-20T19:00:00-05:00",
        "code_present": False}
    assert json.loads(ev[4]["payload"]) == [{"name": "hold_table", "party_size": 2,
                                             "code_present": True, "outcome": "ok"}]
    assert ev[4]["tools_latency_ms"] == 45
    assert json.loads(ev[5]["payload"]) == {"name": "confirm_hold", "outcome": "booked"}
    assert json.loads(ev[7]["payload"]) == {"error": "llm_error", "code_present": False}
    erased = next(e for e in out["events"] if e["conversation_id"] == seeded["late"])
    assert erased["text"] is None and erased["payload"] is None


@pytest.mark.asyncio
async def test_reservations_from_both_providers(db_session, seeded) -> None:
    out = await extract.day(db_session, DAY, runs_dir=None)
    rows = out["reservations"]
    assert sorted(r["provider"] for r in rows) == ["mock_external", "wizscheduler"]
    wiz = next(r for r in rows if r["provider"] == "wizscheduler")
    mock = next(r for r in rows if r["provider"] == "mock_external")
    assert wiz["reservation_id"] == seeded["booked"]
    assert wiz["conversation_id"] == seeded["today"] and wiz["source"] == "voice_agent"
    assert mock["source"] is None and mock["party_size"] == 4
    assert wiz["diner_key"] == mock["diner_key"] == source_key_for(PHONE)
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
    run("r2", "2026-11-18T20:00:00-05:00")  # 01:00Z on the 19th
    (tmp_path / "junk.json").write_text("{not json")
    rows = extract.eval_results(DAY, tmp_path)
    assert [r["run_id"] for r in rows] == ["r1"]
    assert json.loads(rows[0]["failures"]) == ["expected code [code] for [phone]"]
    assert extract.eval_results(DAY + timedelta(days=1), tmp_path)[0]["run_id"] == "r2"
    assert extract.eval_results(DAY, tmp_path / "nope") is None


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
    """Every table, landed and read back: no seeded phone, email or code in
    any string value, and no seeded name outside free text the diner typed
    (redact.text cannot recognise names; see the module docstring)."""
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "r1.json").write_text(json.dumps({
        "id": "r1", "created_at": "2026-11-18T12:00:00+00:00", "meta": {},
        "results": [{"scenario_id": "s1", "attempt": 0, "passed": True,
                     "failures": [f"{PHONE} {EMAIL}"], "metrics": {},
                     "conversation_snapshot": {"diner_name": NAME}}]}))
    out = await extract.day(db_session, DAY, runs_dir=runs)
    sink = lake.LocalSink(tmp_path / "lake")
    for table, schema in schemas.TABLES.items():
        assert lake.write_partition(table, DAY, out[table], schema, sink=sink) == len(out[table])

    files = sorted((tmp_path / "lake").rglob("*.parquet"))
    assert len(files) == len(schemas.TABLES)
    for path in files:
        raw = path.read_bytes()
        for row in pq.read_table(path).to_pylist():
            for s in _strings(row):
                for needle in [PHONE, "4155550123", "555-0123", EMAIL, "example.org", CODE,
                               "ZZ2345"]:
                    assert needle not in s, (path, s)
                if path.parts[-3] != "events":
                    assert "Zelda" not in s and "Quixotic" not in s, (path, s)
        for needle in (PHONE, EMAIL, CODE):
            assert needle.encode() not in raw, path
    # The only name left is the diner's own free text, never a tool payload,
    # a reply or a column.
    events = pq.read_table(tmp_path / "lake" / "events").to_pylist()
    with_name = [e for e in events if any("Zelda" in s for s in _strings(e))]
    assert [(e["type"], "Zelda" in (e["payload"] or "")) for e in with_name] == [
        ("user_turn", False), ("action", False)]
