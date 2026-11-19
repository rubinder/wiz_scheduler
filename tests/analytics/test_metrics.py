"""metrics.compute (#259 step 10): pure, over extracted rows."""

import json
from datetime import date, datetime, time, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from backend.analytics import metrics
from backend.analytics.extract import COVERS
from backend.reservations.rules import RulesSchema, ServicePeriod

DAY = date(2026, 11, 18)  # a Wednesday
NY = ZoneInfo("America/New_York")
TS = datetime(2026, 11, 18, 15, tzinfo=timezone.utc)


def _conv(cid: str, outcome: str | None, loc: str = "loc1", cost: str = "0.01",
          billed: str | None = None) -> dict:
    return {"conversation_id": cid, "company_id": "co1", "location_id": loc,
            "provider": "wizscheduler", "outcome": outcome, "cost_usd": Decimal(cost),
            "billed_usd": Decimal(billed) if billed is not None else None}


def _ev(cid: str, seq: int, type_: str, payload: dict | None = None, llm: int | None = None,
        tools: int | None = None, loc: str = "loc1") -> dict:
    return {"conversation_id": cid, "company_id": "co1", "location_id": loc, "seq": seq,
            "type": type_, "payload": json.dumps(payload) if payload is not None else None,
            "llm_latency_ms": llm, "tools_latency_ms": tools}


def _rules() -> metrics.LocationRules:
    every = list(range(7))
    return metrics.LocationRules(rules=RulesSchema(service_periods=[
        ServicePeriod(name="Lunch", weekdays=every, first_seating=time(11),
                      last_seating=time(14)),
        ServicePeriod(name="Dinner", weekdays=every, first_seating=time(17),
                      last_seating=time(21, 30)),
    ]), tz=NY)


def _seeded() -> dict:
    convs = [_conv("c1", "booked", billed="0.25"), _conv("c2", "modified"),
             _conv("c3", "cancelled"), _conv("c4", "handed_off"), _conv("c5", "abandoned"),
             _conv("c6", "failed", cost="0.005"), _conv("c7", None),
             _conv("c8", "booked", loc="loc2", billed="0.10")]
    events = [
        _ev("c1", 1, "user_turn"),
        _ev("c1", 2, "assistant_turn", {"outcome": "tool_use"}, llm=100),
        _ev("c1", 3, "tool_call", {"name": "find_slots", "party_size": 2}),
        _ev("c1", 4, "tool_result", [{"name": "find_slots"}], tools=50),
        _ev("c1", 5, "assistant_turn", {"outcome": "end_turn"}, llm=300),
        _ev("c1", 6, "user_turn"),
        _ev("c1", 7, "assistant_turn", {"outcome": "end_turn"}, llm=200),
        _ev("c4", 1, "user_turn"),
        _ev("c4", 2, "assistant_turn", {"outcome": "end_turn"}, llm=400),
        _ev("c4", 3, "handoff", {"reason": "large_party"}),
        _ev("c5", 1, "tool_call", {"name": "find_slots"}),
        _ev("c6", 1, "handoff", {"reason": "other"}),
        _ev("c2", 1, "handoff", {"reason": "large_party"}),
    ]
    screened = [{"company_id": "co1", "location_id": "loc1", "decision": d}
                for d in ("answer", "answer", "block")]
    covers = [
        # 12:30 and 19:00 New York time; 15:30 is between periods.
        {"provider": "wizscheduler", "company_id": "co1", "location_id": "loc1",
         "party_size": 2, "start_at": datetime(2026, 11, 18, 17, 30, tzinfo=timezone.utc)},
        {"provider": "mock_external", "company_id": "co1", "location_id": "loc1",
         "party_size": 4, "start_at": datetime(2026, 11, 19, 0, 0, tzinfo=timezone.utc)},
        {"provider": "wizscheduler", "company_id": "co1", "location_id": "loc1",
         "party_size": 3, "start_at": datetime(2026, 11, 19, 0, 0, tzinfo=timezone.utc)},
        {"provider": "wizscheduler", "company_id": "co1", "location_id": "loc1",
         "party_size": 5, "start_at": datetime(2026, 11, 18, 20, 30, tzinfo=timezone.utc)},
    ]
    return {"conversations": convs, "events": events, "screened_calls": screened,
            COVERS: covers, "reservations": [], "agent_usage": []}


def test_each_metric_on_a_seeded_day() -> None:
    rows = metrics.compute(_seeded(), {"loc1": _rules()}, day=DAY)
    assert [r["location_id"] for r in rows] == ["loc1", "loc2"]
    r = rows[0]
    assert r["company_id"] == "co1" and r["day"] == DAY
    assert r["conversations"] == 7
    assert r["resolved"] == 3  # booked + modified + cancelled
    assert (r["booked"], r["handed_off"], r["abandoned"], r["failed"]) == (1, 1, 1, 1)
    assert r["booking_intent"] == 2  # c1 and c5 called find_slots
    assert r["handoff_reasons"] == {"large_party": 2, "other": 1}
    # llm latencies 100, 300, 200, 400: nearest rank p50 = 2nd, p95 = 4th.
    assert (r["llm_p50_ms"], r["llm_p95_ms"]) == (200, 400)
    # Turns: c1 = 100+50+300, c1 = 200, c4 = 400.
    assert (r["turn_p50_ms"], r["turn_p95_ms"]) == (400, 450)
    assert r["cost_usd"] == Decimal("0.065")
    assert r["billed_usd"] == Decimal("0.25")
    assert (r["screened"], r["blocked"]) == (3, 1)
    assert r["covers_by_period"] == {"Dinner": 7, "Lunch": 2, "other": 5}
    loc2 = rows[1]
    assert loc2["conversations"] == 1 and loc2["booked"] == 1
    assert loc2["billed_usd"] == Decimal("0.10")
    assert loc2["llm_p50_ms"] is None and loc2["covers_by_period"] == {}
    assert loc2["handoff_reasons"] == {}


def test_an_empty_day_has_no_rows() -> None:
    empty = {"conversations": [], "events": [], "screened_calls": [], COVERS: []}
    assert metrics.compute(empty, {}, day=DAY) == []


def test_percentile_is_nearest_rank() -> None:
    assert metrics.percentile([], 50) is None
    assert metrics.percentile([7], 95) == 7
    vals = list(range(1, 21))  # 1..20
    assert metrics.percentile(vals, 50) == 10
    assert metrics.percentile(vals, 95) == 19
    assert metrics.percentile(vals, 100) == 20


def test_a_location_without_rules_counts_covers_as_other() -> None:
    day = _seeded()
    rows = metrics.compute(day, {}, day=DAY)
    assert rows[0]["covers_by_period"] == {"other": 14}
