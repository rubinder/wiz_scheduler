"""GET /reservations/insights (#259 step 10): the AI host's daily metrics
for one location, read from agent_daily_metrics."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backend.analytics.models import AgentDailyMetric
from backend.config import settings
from backend.models import Company, Location, Region
from tests.conftest import COMPANY2_ID, COMPANY_ID, LOCATION_ID, OTHER_LOCATION_ID

URL = "/api/v1/reservations/insights"

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", True)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _yesterday() -> date:
    return datetime.now(timezone.utc).date() - timedelta(days=1)


def _metric(location_id: str, day: date, **over) -> AgentDailyMetric:
    row = {"conversations": 10, "resolved": 7, "booked": 5, "handed_off": 2,
           "abandoned": 1, "failed": 0, "booking_intent": 8,
           "handoff_reasons": {"large_party": 2}, "llm_p50_ms": 800,
           "llm_p95_ms": 2100, "turn_p50_ms": 1200, "turn_p95_ms": 3000,
           "cost_usd": Decimal("0.123456"), "billed_usd": Decimal("2.50"),
           "screened": 12, "blocked": 2, "covers_by_period": {"dinner": 14}}
    row.update(over)
    return AgentDailyMetric(company_id=COMPANY_ID, location_id=location_id, day=day, **row)


async def test_empty_response_defaults_to_the_last_30_days(client, manager_token, seed_location):
    r = await client.get(URL, params={"location_id": LOCATION_ID}, headers=_auth(manager_token))
    assert r.status_code == 200
    to = _yesterday()
    assert r.json() == {"location_id": LOCATION_ID, "from": str(to - timedelta(days=29)),
                        "to": str(to), "days": []}


async def test_returns_the_raw_rows_in_range_oldest_first(
    client, manager_token, seed_location, other_location, db_session
):
    to = _yesterday()
    db_session.add_all([
        _metric(LOCATION_ID, to),
        _metric(LOCATION_ID, to - timedelta(days=3), llm_p50_ms=None, booked=0),
        _metric(LOCATION_ID, to - timedelta(days=30)),  # before the default range
        _metric(OTHER_LOCATION_ID, to),  # another location
    ])
    await db_session.commit()
    r = await client.get(URL, params={"location_id": LOCATION_ID}, headers=_auth(manager_token))
    assert r.status_code == 200
    days = r.json()["days"]
    assert [d["day"] for d in days] == [str(to - timedelta(days=3)), str(to)]
    assert days[0]["llm_p50_ms"] is None and days[0]["booked"] == 0
    assert days[1] == {
        "day": str(to), "conversations": 10, "resolved": 7, "booked": 5,
        "handed_off": 2, "abandoned": 1, "failed": 0, "booking_intent": 8,
        "handoff_reasons": {"large_party": 2}, "llm_p50_ms": 800, "llm_p95_ms": 2100,
        "turn_p50_ms": 1200, "turn_p95_ms": 3000, "cost_usd": "0.123456",
        "billed_usd": "2.50", "screened": 12, "blocked": 2,
        "covers_by_period": {"dinner": 14},
    }


async def test_explicit_range_is_inclusive(client, manager_token, seed_location, db_session):
    d0 = date(2026, 3, 1)
    db_session.add_all([_metric(LOCATION_ID, d0 + timedelta(days=i)) for i in range(-1, 4)])
    await db_session.commit()
    r = await client.get(URL, params={"location_id": LOCATION_ID, "from": "2026-03-01",
                                      "to": "2026-03-02"}, headers=_auth(manager_token))
    assert r.status_code == 200
    body = r.json()
    assert (body["from"], body["to"]) == ("2026-03-01", "2026-03-02")
    assert [d["day"] for d in body["days"]] == ["2026-03-01", "2026-03-02"]


async def test_from_alone_ends_yesterday(client, manager_token, seed_location):
    frm = _yesterday() - timedelta(days=5)
    r = await client.get(URL, params={"location_id": LOCATION_ID, "from": str(frm)},
                         headers=_auth(manager_token))
    assert r.status_code == 200
    assert (r.json()["from"], r.json()["to"]) == (str(frm), str(_yesterday()))


async def test_to_alone_starts_29_days_earlier(client, manager_token, seed_location):
    r = await client.get(URL, params={"location_id": LOCATION_ID, "to": "2026-03-31"},
                         headers=_auth(manager_token))
    assert r.status_code == 200
    assert (r.json()["from"], r.json()["to"]) == ("2026-03-02", "2026-03-31")


@pytest.mark.parametrize("frm,to", [
    ("2026-03-02", "2026-03-01"),  # from after to
    ("2026-01-01", "2026-06-30"),  # 181 days
])
async def test_range_out_of_bounds_is_422(client, manager_token, seed_location, frm, to):
    r = await client.get(URL, params={"location_id": LOCATION_ID, "from": frm, "to": to},
                         headers=_auth(manager_token))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_range"


async def test_180_days_is_allowed(client, manager_token, seed_location):
    r = await client.get(URL, params={"location_id": LOCATION_ID, "from": "2026-01-01",
                                      "to": "2026-06-29"}, headers=_auth(manager_token))
    assert r.status_code == 200


async def test_location_id_is_required(client, manager_token, seed_location):
    r = await client.get(URL, headers=_auth(manager_token))
    assert r.status_code == 422


async def test_scoped_manager_cannot_read_another_location(
    client, regular_token, other_location, db_session
):
    db_session.add_all([_metric(LOCATION_ID, _yesterday()),
                        _metric(OTHER_LOCATION_ID, _yesterday())])
    await db_session.commit()
    h = _auth(regular_token)
    r = await client.get(URL, params={"location_id": OTHER_LOCATION_ID}, headers=h)
    assert r.status_code == 404
    r = await client.get(URL, params={"location_id": LOCATION_ID}, headers=h)
    assert r.status_code == 200
    assert len(r.json()["days"]) == 1


async def test_another_companys_location_is_404(client, manager_token, seed_location, db_session):
    db_session.add(Company(id=COMPANY2_ID, name="Other Co", slug="other-co"))
    await db_session.flush()
    db_session.add(Region(id="rgn2xxxx", company_id=COMPANY2_ID, name="R"))
    await db_session.flush()
    db_session.add(Location(id="loc2xxxx", company_id=COMPANY2_ID, region_id="rgn2xxxx",
                            name="L2", timezone="UTC"))
    await db_session.commit()
    r = await client.get(URL, params={"location_id": "loc2xxxx"}, headers=_auth(manager_token))
    assert r.status_code == 404


async def test_404_when_flag_off(client, manager_token, seed_location, monkeypatch):
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", False)
    r = await client.get(URL, params={"location_id": LOCATION_ID}, headers=_auth(manager_token))
    assert r.status_code == 404
    assert r.json()["detail"] == "Not found"
