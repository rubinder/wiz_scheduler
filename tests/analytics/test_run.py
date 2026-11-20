"""run.run, quality.check and the nightly script (#259 step 10)."""

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq
import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.analytics import extract, lake, quality, run
from backend.analytics.models import AgentDailyMetric
from backend.models import Location, OwnershipGroup
from backend.reservations.agent.models import AgentConversation, AgentEvent
from backend.reservations.mock_external_models import MockExternalBooking
from backend.reservations.models import Reservation, ReservationRules
from backend.reservations.screening_models import ScreenedCall
from backend.reservations.subscription import billed_amount
from backend.reservations.subscription_models import AgentUsage
from tests.conftest import COMPANY_ID, LOCATION_ID

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

DAY = date(2026, 11, 18)  # a Wednesday
RULES = {"service_periods": [
    {"name": "Lunch", "weekdays": list(range(7)), "first_seating": "11:00",
     "last_seating": "14:00"},
    {"name": "Dinner", "weekdays": list(range(7)), "first_seating": "17:00",
     "last_seating": "21:30"}]}


def at(d: date, h: int, m: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, h, m, tzinfo=timezone.utc)


@pytest.fixture
def alerts(monkeypatch) -> list[tuple[str, list[str]]]:
    sent: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(run, "send_analytics_quality_alert",
                        lambda day, failures: sent.append((day, failures)) or True)
    return sent


@pytest_asyncio.fixture
async def seeded(db_session: AsyncSession, seed_location: Location) -> dict[str, str]:
    db = db_session
    db.add(OwnershipGroup(id="ogalyt02", name="Group", ai_credits_usd=0.0))
    db.add(ReservationRules(company_id=COMPANY_ID, location_id=LOCATION_ID, rules=RULES,
                            version=1, updated_at=at(DAY, 0)))

    def conv(started: datetime, **kw) -> AgentConversation:
        c = AgentConversation(company_id=COMPANY_ID, location_id=LOCATION_ID, channel="web",
                              source_key="s" * 64, started_at=started, created_at=started,
                              **kw)
        db.add(c)
        return c

    booked = conv(at(DAY, 15), outcome="booked", status="closed",
                  cost_usd=Decimal("0.030000"), ended_at=at(DAY, 15, 5))
    mock = conv(at(DAY, 16), outcome="booked", status="closed", provider="mock_external",
                cost_usd=Decimal("0.010000"), ended_at=at(DAY, 16, 5))
    handed = conv(at(DAY, 17), outcome="handed_off", status="closed",
                  cost_usd=Decimal("0.005000"))
    await db.flush()
    # Booked for 19:00 New York time on DAY (00:00Z next day): Dinner.
    res = Reservation(company_id=COMPANY_ID, location_id=LOCATION_ID, party_size=2,
                      start_at=at(DAY + timedelta(days=1), 0),
                      end_at=at(DAY + timedelta(days=1), 2), status="booked",
                      source="web_agent", conversation_id=booked.id,
                      created_at=at(DAY, 15, 3), updated_at=at(DAY, 15, 3))
    # 12:00 New York on DAY: Lunch.
    ext = MockExternalBooking(company_id=COMPANY_ID, location_id=LOCATION_ID,
                              start_at=at(DAY, 17), end_at=at(DAY, 19), party_size=4,
                              status="booked", created_at=at(DAY, 16, 2),
                              updated_at=at(DAY, 16, 2))
    db.add_all([res, ext])
    await db.flush()
    booked.reservation_id = res.id
    mock.reservation_id = ext.id

    def ev(c: AgentConversation, seq: int, type_: str, payload: dict, ts: datetime,
           **kw) -> None:
        db.add(AgentEvent(company_id=COMPANY_ID, conversation_id=c.id, seq=seq, type=type_,
                          payload=payload, created_at=ts, **kw))

    ev(booked, 1, "user_turn", {"content": [{"type": "text", "text": "table for 2"}]},
       at(DAY, 15))
    ev(booked, 2, "assistant_turn", {"content": [], "stop_reason": "tool_use"},
       at(DAY, 15, 1), latency_ms={"llm": 500})
    ev(booked, 3, "tool_call", {"tool_use_id": "t1", "name": "find_slots",
                                "input": {"party_size": 2}}, at(DAY, 15, 1))
    ev(handed, 1, "user_turn", {"content": [{"type": "text", "text": "big party"}]},
       at(DAY, 17))
    ev(handed, 2, "assistant_turn", {"content": [], "stop_reason": "end_turn"},
       at(DAY, 17, 1), latency_ms={"llm": 700})
    ev(handed, 3, "handoff", {"reason": "large_party"}, at(DAY, 17, 1))
    db.add(AgentUsage(company_id=COMPANY_ID, ownership_group_id="ogalyt02",
                      location_id=LOCATION_ID, conversation_id=booked.id,
                      cost_usd=Decimal("0.030000"), billed_usd=billed_amount(Decimal("0.03")),
                      period_start=at(DAY, 0), created_at=at(DAY, 15, 6)))
    # The mock conversation is billed after midnight: another day's usage.
    db.add(AgentUsage(company_id=COMPANY_ID, ownership_group_id="ogalyt02",
                      location_id=LOCATION_ID, conversation_id=mock.id,
                      cost_usd=Decimal("0.010000"), billed_usd=billed_amount(Decimal("0.01")),
                      period_start=at(DAY, 0), created_at=at(DAY + timedelta(days=1), 0, 5)))
    for decision in ("answer", "block", "block"):
        db.add(ScreenedCall(company_id=COMPANY_ID, location_id=LOCATION_ID, caller_key=None,
                            decision=decision, reason="default" if decision == "answer"
                            else "ftc", created_at=at(DAY, 14)))
    await db.commit()
    return {"booked": booked.id, "mock": mock.id, "handed": handed.id}


async def _metrics(db: AsyncSession) -> list[AgentDailyMetric]:
    db.expire_all()
    return list((await db.execute(select(AgentDailyMetric))).scalars())


async def test_run_lands_checks_and_writes_metrics(db_session, seeded, alerts,
                                                   tmp_path: Path) -> None:
    summary = await run.run(DAY, lake.LocalSink(tmp_path), db_session, runs_dir=None)
    assert summary["ok"] and summary["failures"] == [] and alerts == []
    assert summary["landed"]["conversations"] == 3
    assert summary["skipped"] == ["eval_results"]
    assert pq.read_table(tmp_path / "conversations" / "dt=2026-11-18").num_rows == 3
    [m] = await _metrics(db_session)
    assert (m.company_id, m.location_id, m.day) == (COMPANY_ID, LOCATION_ID, DAY)
    assert (m.conversations, m.resolved, m.booked, m.handed_off) == (3, 2, 2, 1)
    assert (m.abandoned, m.failed, m.booking_intent) == (0, 0, 1)
    assert m.handoff_reasons == {"large_party": 1}
    assert (m.llm_p50_ms, m.llm_p95_ms) == (500, 700)
    assert (m.turn_p50_ms, m.turn_p95_ms) == (500, 700)
    assert m.cost_usd == Decimal("0.045000")
    # Billed on matching rows, including the usage written after midnight.
    assert m.billed_usd == billed_amount(Decimal("0.03")) + billed_amount(Decimal("0.01"))
    assert (m.screened, m.blocked) == (3, 2)
    assert m.covers_by_period == {"Dinner": 2, "Lunch": 4}


async def test_a_rerun_is_idempotent(db_session, seeded, alerts, tmp_path: Path) -> None:
    sink = lake.LocalSink(tmp_path)
    await run.run(DAY, sink, db_session, runs_dir=None)
    first = [(m.location_id, m.conversations, m.billed_usd) for m in await _metrics(db_session)]
    again = await run.run(DAY, sink, db_session, runs_dir=None)
    assert again["ok"] and again["metrics_rows"] == 1
    assert [(m.location_id, m.conversations, m.billed_usd)
            for m in await _metrics(db_session)] == first
    assert [p.name for p in (tmp_path / "events" / "dt=2026-11-18").iterdir()] == \
        ["part-0.parquet"]


async def test_an_empty_day(db_session, seeded, alerts, tmp_path: Path) -> None:
    empty = DAY - timedelta(days=5)
    summary = await run.run(empty, lake.LocalSink(tmp_path), db_session, runs_dir=None)
    assert summary["ok"] and summary["metrics_rows"] == 0
    assert all(n == 0 for n in summary["landed"].values())
    assert pq.read_table(tmp_path / "events" / "dt=2026-11-13").num_rows == 0
    assert await _metrics(db_session) == []


# --- each quality check on a crafted defect ---------------------------------


async def _fails_and_alerts(db_session, alerts, tmp_path: Path, needle: str) -> None:
    # A previous good run's metrics are left untouched by a failing one.
    summary = await run.run(DAY, lake.LocalSink(tmp_path), db_session, runs_dir=None)
    assert not summary["ok"]
    assert any(f.startswith(needle) for f in summary["failures"]), summary["failures"]
    assert summary["metrics_rows"] == 0
    assert len(alerts) == 1 and alerts[0][0] == "2026-11-18"
    # The lake is landed anyway.
    assert (tmp_path / "conversations" / "dt=2026-11-18" / "part-0.parquet").exists()


async def test_negative_latency_fails(db_session, seeded, alerts, tmp_path) -> None:
    db_session.add(AgentEvent(company_id=COMPANY_ID, conversation_id=seeded["handed"], seq=4,
                              type="assistant_turn", payload={"content": []},
                              latency_ms={"llm": -3}, created_at=at(DAY, 17, 2)))
    await db_session.commit()
    await _fails_and_alerts(db_session, alerts, tmp_path, "negative_latency: 1 event(s)")
    assert seeded["handed"] in alerts[0][1][0]


async def test_unresolved_booking_ref_fails(db_session, seeded, alerts, tmp_path) -> None:
    conv = await db_session.get(AgentConversation, seeded["mock"])
    conv.reservation_id = "zzzzzzzz"  # not in mock_external_bookings
    booked = await db_session.get(AgentConversation, seeded["booked"])
    ext_id = (await db_session.execute(select(MockExternalBooking.id))).scalar_one()
    booked.reservation_id = ext_id  # a mock id, but this conversation is wizscheduler
    await db_session.commit()
    await _fails_and_alerts(db_session, alerts, tmp_path, "booked_ref: 2 of 2")


async def test_a_metrics_day_is_not_touched_by_a_failing_rerun(db_session, seeded, alerts,
                                                               tmp_path) -> None:
    await run.run(DAY, lake.LocalSink(tmp_path), db_session, runs_dir=None)
    conv = await db_session.get(AgentConversation, seeded["booked"])
    conv.reservation_id = None
    await db_session.commit()
    summary = await run.run(DAY, lake.LocalSink(tmp_path), db_session, runs_dir=None)
    assert not summary["ok"] and len(alerts) == 1
    [m] = await _metrics(db_session)
    assert m.booked == 2


async def test_a_truncated_lake_write_fails(db_session, seeded, alerts, tmp_path,
                                            monkeypatch) -> None:
    real = lake.LocalSink._write

    def truncating(self, path, data):
        real(self, path, data[:-10] if "events" in str(path) else data)

    monkeypatch.setattr(lake.LocalSink, "_write", truncating)
    await _fails_and_alerts(db_session, alerts, tmp_path,
                            "landed_count: events landed an unreadable file of 6")
    assert await _metrics(db_session) == []


async def _usage(db: AsyncSession, conv_id: str) -> AgentUsage:
    return (await db.execute(select(AgentUsage).where(
        AgentUsage.conversation_id == conv_id))).scalar_one()


async def test_usage_cost_disagreeing_with_its_conversation_fails(db_session, seeded, alerts,
                                                                  tmp_path) -> None:
    u = await _usage(db_session, seeded["booked"])
    u.cost_usd = Decimal("0.031000")  # metered before the last turn's cost
    u.billed_usd = billed_amount(Decimal("0.031"))
    await db_session.commit()
    await _fails_and_alerts(db_session, alerts, tmp_path, "usage_cost: 1 of 2 usage row(s)")
    assert seeded["booked"] in alerts[0][1][0]
    assert not any(f.startswith("usage_billed") for f in alerts[0][1])


async def test_usage_billed_off_the_formula_fails(db_session, seeded, alerts,
                                                  tmp_path) -> None:
    u = await _usage(db_session, seeded["mock"])  # its usage landed the next day
    u.billed_usd = billed_amount(u.cost_usd) + Decimal("0.01")
    await db_session.commit()
    await _fails_and_alerts(db_session, alerts, tmp_path, "usage_billed: 1 of 2 usage row(s)")
    assert seeded["mock"] in alerts[0][1][0]


async def test_usage_on_a_blocked_or_zero_cost_conversation_fails(db_session, seeded, alerts,
                                                                  tmp_path) -> None:
    ids = []
    for outcome, cost in (("blocked", Decimal("0.010000")), ("abandoned", Decimal("0"))):
        c = AgentConversation(company_id=COMPANY_ID, location_id=LOCATION_ID, channel="voice",
                              source_key="s" * 64, started_at=at(DAY, 18), outcome=outcome,
                              status="closed", cost_usd=cost)
        db_session.add(c)
        await db_session.flush()
        db_session.add(AgentUsage(company_id=COMPANY_ID, ownership_group_id="ogalyt02",
                                  location_id=LOCATION_ID, conversation_id=c.id,
                                  cost_usd=Decimal("0.010000"),
                                  billed_usd=billed_amount(Decimal("0.01")),
                                  period_start=at(DAY, 0), created_at=at(DAY, 18, 1)))
        ids.append(c.id)
    await db_session.commit()
    await _fails_and_alerts(db_session, alerts, tmp_path,
                            "usage_unexpected: 2 blocked or zero-cost conversation(s)")
    assert all(i in alerts[0][1][0] for i in ids)


async def test_a_landed_usage_row_without_its_conversation_fails(db_session, seeded, alerts,
                                                                 tmp_path) -> None:
    # A conversation from the week before, metered today, then deleted:
    # agent_usage.conversation_id is SET NULL.
    gone = AgentConversation(company_id=COMPANY_ID, location_id=LOCATION_ID, channel="web",
                             source_key="s" * 64, started_at=at(DAY - timedelta(days=7), 9),
                             outcome="booked", status="closed", cost_usd=Decimal("0.02"))
    db_session.add(gone)
    await db_session.flush()
    db_session.add(AgentUsage(company_id=COMPANY_ID, ownership_group_id="ogalyt02",
                              location_id=LOCATION_ID, conversation_id=gone.id,
                              cost_usd=Decimal("0.020000"),
                              billed_usd=billed_amount(Decimal("0.02")),
                              period_start=at(DAY, 0), created_at=at(DAY, 9)))
    await db_session.commit()
    await db_session.execute(delete(AgentConversation).where(AgentConversation.id == gone.id))
    await db_session.commit()
    await _fails_and_alerts(db_session, alerts, tmp_path,
                            "usage_orphan: 1 landed usage row(s) reference no conversation")


async def test_runs_of_the_same_day_are_serialised(db_session, seeded, alerts,
                                                   tmp_path) -> None:
    params = {"key": run.LOCK_KEY, "day": DAY.toordinal()}
    async with db_session.bind.connect() as other:
        await other.execute(text("SELECT pg_advisory_lock(hashtext(:key), :day)"), params)
        task = asyncio.create_task(
            run.run(DAY, lake.LocalSink(tmp_path), db_session, runs_dir=None))
        await asyncio.sleep(0.5)
        assert not task.done()
        assert not (tmp_path / "conversations").exists()  # not even the lake
        await other.execute(text("SELECT pg_advisory_unlock(hashtext(:key), :day)"), params)
        await other.commit()
    summary = await asyncio.wait_for(task, 30)
    assert summary["ok"]


async def test_a_failing_unlock_never_masks_the_original_error(db_session, caplog) -> None:
    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        async with run.day_lock(db_session, DAY) as conn:
            pid = (await conn.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            # Kill the lock's connection, so the unlock in `finally` fails too.
            async with db_session.bind.connect() as other:
                await other.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
            raise Boom
    assert "advisory unlock failed" in caplog.text
    # The killed session took its lock with it: the day is free again.
    params = {"key": run.LOCK_KEY, "day": DAY.toordinal()}
    async with db_session.bind.connect() as probe:
        got = (await probe.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:key), :day)"), params)).scalar_one()
        assert got
        await probe.execute(text("SELECT pg_advisory_unlock(hashtext(:key), :day)"), params)
        await probe.commit()


# --- retention ---------------------------------------------------------------


async def test_retention_purges_old_daily_metrics(db_session, seed_location) -> None:
    from backend.config import settings
    from backend.services.data_retention import run_data_retention

    today = datetime.now(timezone.utc).date()
    old_day = today - timedelta(days=settings.RETENTION_RESERVATIONS_DAYS + 1)
    zero = {"conversations": 0, "resolved": 0, "booked": 0, "handed_off": 0,
            "abandoned": 0, "failed": 0, "booking_intent": 0, "handoff_reasons": {},
            "cost_usd": Decimal("0"), "billed_usd": Decimal("0"), "screened": 0,
            "blocked": 0, "covers_by_period": {}}
    db_session.add_all([
        AgentDailyMetric(company_id=COMPANY_ID, location_id=LOCATION_ID, day=old_day, **zero),
        AgentDailyMetric(company_id=COMPANY_ID, location_id=LOCATION_ID,
                         day=today - timedelta(days=10), **zero)])
    await db_session.commit()
    summary = await run_data_retention(db_session)
    assert summary["old_agent_daily_metrics_deleted"] == 1
    assert [m.day for m in await _metrics(db_session)] == [today - timedelta(days=10)]


# --- group deletion ----------------------------------------------------------


async def test_group_deletion_deletes_the_daily_metrics(db_session, seed_location) -> None:
    from backend.models import Company
    from backend.services.billing import delete_og_and_dependents

    og = OwnershipGroup(id="ogalyt03", name="Group", ai_credits_usd=0.0)
    db_session.add(og)
    await db_session.flush()
    (await db_session.get(Company, COMPANY_ID)).ownership_group_id = og.id
    db_session.add(AgentDailyMetric(
        company_id=COMPANY_ID, location_id=LOCATION_ID, day=DAY, conversations=1,
        resolved=1, booked=1, handed_off=0, abandoned=0, failed=0, booking_intent=1,
        handoff_reasons={}, cost_usd=Decimal("0.01"), billed_usd=Decimal("0.02"),
        screened=0, blocked=0, covers_by_period={}))
    await db_session.commit()

    await delete_og_and_dependents(db_session, og)
    await db_session.commit()
    assert await _metrics(db_session) == []
    assert await db_session.get(Company, COMPANY_ID) is None
