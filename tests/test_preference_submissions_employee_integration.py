"""Employee side of preference submissions (#189)."""
import pytest
import pytest_asyncio
from sqlalchemy import func, select

from backend.models import (
    Company, Employee, EmployeeAffinity, EmployeeDayBlackout, EmployeeDayPreference,
    EmployeeHourRangeCap, EmployeeHourRangePreference,
)
from backend.models.preference_submission import EmployeePreferenceSubmission
from datetime import date, datetime, timezone
from tests.conftest import (
    COMPANY2_ID, COMPANY_ID, EMPLOYEE_USER_ID, LOCATION_ID, OTHER_LOCATION_ID, _id,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
URL = "/api/v1/preference-submissions"


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _enable(db_session, on: bool = True) -> None:
    c = await db_session.get(Company, COMPANY_ID)
    c.employee_preference_submissions_enabled = on
    await db_session.commit()


@pytest_asyncio.fixture
async def me(db_session, seed_location, other_location, seed_employee_user) -> Employee:
    e = Employee(id=_id(), company_id=COMPANY_ID, full_name="Me", user_id=EMPLOYEE_USER_ID,
                 location_ids=[LOCATION_ID])
    db_session.add(e)
    await db_session.commit()
    return e


async def _coworker(db_session, name="Cow", locs=None, company=COMPANY_ID, archived=False):
    e = Employee(id=_id(), company_id=company, full_name=name,
                 location_ids=locs if locs is not None else [LOCATION_ID],
                 archived_at=datetime.now(timezone.utc) if archived else None)
    db_session.add(e)
    await db_session.commit()
    return e


FULL = {
    "max_hours_per_week": 30,
    "blackouts": [{"day_of_week": 1, "start_time": "08:00", "end_time": "12:00"}],
    "day_preferences": [{"day_of_week": 2, "employee_importance": "high"}],
    "hour_range_preferences": [{"start_time": "09:00", "end_time": "17:00",
                                "employee_importance": "low"}],
    "caps": [{"start_time": "22:00", "end_time": "06:00", "max_per_week": 2,
              "employee_importance": "medium"}],
}


def _keys(obj, acc=None):
    acc = set() if acc is None else acc
    if isinstance(obj, dict):
        for k, v in obj.items():
            acc.add(k)
            _keys(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _keys(v, acc)
    return acc


async def test_disabled_state_and_put_403(client, db_session, me, employee_token):
    r = await client.get(f"{URL}/me", headers=_h(employee_token))
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    r = await client.put(f"{URL}/me", json=FULL, headers=_h(employee_token))
    assert r.status_code == 403
    assert r.json()["detail"] == "employee_preference_submissions_disabled"


async def test_submit_all_sections_creates_pending(client, db_session, me, employee_token):
    await _enable(db_session)
    cow = await _coworker(db_session)
    body = {**FULL, "affinities": [{"target_employee_id": cow.id, "relationship": "prefer_with"}]}
    r = await client.put(f"{URL}/me", json=body, headers=_h(employee_token))
    assert r.status_code == 200, r.text
    assert r.json()["pending"]["payload"]["caps"][0]["max_per_week"] == 2
    g = (await client.get(f"{URL}/me", headers=_h(employee_token))).json()
    assert g["enabled"] is True
    assert g["pending"]["payload"]["affinities"][0]["target_employee_id"] == cow.id
    assert g["last_decision"] is None


async def test_resubmit_supersedes(client, db_session, me, employee_token):
    await _enable(db_session)
    for hrs in (10, 20):
        r = await client.put(f"{URL}/me", json={"max_hours_per_week": hrs},
                             headers=_h(employee_token))
        assert r.status_code == 200
    rows = (await db_session.execute(
        select(EmployeePreferenceSubmission.status).where(
            EmployeePreferenceSubmission.employee_id == me.id))).scalars().all()
    assert sorted(rows) == ["pending", "superseded"]
    assert r.json()["pending"]["payload"]["max_hours_per_week"] == 20


@pytest.mark.parametrize("body", [
    {"day_preferences": [{"day_of_week": 1, "employee_importance": "low", "weight": 0.5}]},
    {"hour_range_preferences": [{"start_time": "09:00", "end_time": "10:00",
                                 "employee_importance": "low", "weight": 0.5}]},
    {"caps": [{"start_time": "09:00", "end_time": "10:00", "max_per_week": 1,
               "employee_importance": "low", "weight": 0.5}]},
    {"affinities": [{"target_employee_id": "x", "relationship": "prefer_with", "level": 1}]},
    {"day_preferences": [{"day_of_week": d, "employee_importance": "low"} for d in range(4)]},
    {"day_preferences": [{"day_of_week": 1, "employee_importance": "low"}] * 2},
    {"blackouts": [{"day_of_week": 1, "start_time": "12:00", "end_time": "08:00"}]},
    {"hour_range_preferences": [{"start_time": "09:00", "end_time": "09:00",
                                 "employee_importance": "low"}]},
    {"max_hours_per_week": 200},
    {"bogus": 1},
])
async def test_invalid_payloads_422(client, db_session, me, employee_token, body):
    await _enable(db_session)
    r = await client.put(f"{URL}/me", json=body, headers=_h(employee_token))
    assert r.status_code == 422, r.text


async def test_affinity_target_rules(client, db_session, me, employee_token):
    await _enable(db_session)
    archived = await _coworker(db_session, archived=True)
    apart = await _coworker(db_session, locs=[OTHER_LOCATION_ID])
    ok = await _coworker(db_session)
    bad = [me.id, "nonexist", archived.id, apart.id]
    for t in bad:
        r = await client.put(f"{URL}/me", headers=_h(employee_token), json={
            "affinities": [{"target_employee_id": t, "relationship": "prefer_with"}]})
        assert r.status_code == 422, (t, r.text)
    r = await client.put(f"{URL}/me", headers=_h(employee_token), json={
        "affinities": [{"target_employee_id": ok.id, "relationship": "prefer_with"},
                       {"target_employee_id": ok.id, "relationship": "prefer_apart"}]})
    assert r.status_code == 422
    r = await client.put(f"{URL}/me", headers=_h(employee_token), json={
        "affinities": [{"target_employee_id": ok.id, "relationship": "never_together"}]})
    assert r.status_code == 200


async def test_other_company_target_422(client, db_session, me, employee_token,
                                         other_company_employee_id):
    await _enable(db_session)
    r = await client.put(f"{URL}/me", headers=_h(employee_token), json={
        "affinities": [{"target_employee_id": other_company_employee_id, "relationship": "prefer_with"}]})
    assert r.status_code == 422


async def test_archived_submitter_and_manager_404(client, db_session, me, employee_token,
                                                   manager_token):
    r = await client.get(f"{URL}/me", headers=_h(manager_token))
    assert r.status_code == 404
    me.archived_at = datetime.now(timezone.utc)
    await db_session.commit()
    r = await client.get(f"{URL}/me", headers=_h(employee_token))
    assert r.status_code == 404
    await _enable(db_session)
    r = await client.put(f"{URL}/me", json={}, headers=_h(employee_token))
    assert r.status_code == 404


async def test_current_view_redacted_and_outgoing_only(client, db_session, me, employee_token):
    cow = await _coworker(db_session, "Pat")
    cow2 = await _coworker(db_session, "Sam")
    today = datetime.now(timezone.utc).date()
    cid = COMPANY_ID
    db_session.add_all([
        EmployeeAffinity(company_id=cid, employee_id=me.id, target_employee_id=cow.id,
                         level=-1, entry_date=today),
        EmployeeAffinity(company_id=cid, employee_id=me.id, target_employee_id=cow2.id,
                         level=0.5, entry_date=today),
        EmployeeAffinity(company_id=cid, employee_id=cow.id, target_employee_id=me.id,
                         level=1, entry_date=today),
        EmployeeDayPreference(company_id=cid, employee_id=me.id, day_of_week=3, weight=0.9),
        EmployeeHourRangePreference(company_id=cid, employee_id=me.id, start_time="09:00",
                                    end_time="12:00", weight=0.4),
        EmployeeHourRangeCap(company_id=cid, employee_id=me.id, start_time="20:00",
                             end_time="23:00", max_per_week=1, weight=0.8),
        EmployeeDayBlackout(company_id=cid, employee_id=me.id, day_of_week=0,
                            start_time="08:00", end_time="09:00"),
    ])
    await db_session.commit()
    body = (await client.get(f"{URL}/me", headers=_h(employee_token))).json()
    cur = body["current"]
    assert not ({"weight", "level", "employee_importance"} & _keys(cur))
    rels = {a["target_name"]: a["relationship"] for a in cur["affinities"]}
    assert rels == {"Pat": "never_together", "Sam": "prefer_with"}
    assert cur["day_preferences"] == [{"day_of_week": 3}]
    assert len(cur["caps"]) == len(cur["blackouts"]) == len(cur["hour_range_preferences"]) == 1


async def test_submit_leaves_live_tables_untouched(client, db_session, me, employee_token):
    await _enable(db_session)
    cow = await _coworker(db_session)
    body = {**FULL, "affinities": [{"target_employee_id": cow.id, "relationship": "prefer_with"}]}
    assert (await client.put(f"{URL}/me", json=body, headers=_h(employee_token))).status_code == 200
    for m in (EmployeeAffinity, EmployeeDayBlackout, EmployeeDayPreference,
              EmployeeHourRangePreference, EmployeeHourRangeCap):
        n = (await db_session.execute(select(func.count()).select_from(m))).scalar_one()
        assert n == 0, m
    await db_session.refresh(me)
    assert me.max_hours_per_week is None


async def test_coworkers_endpoint(client, db_session, me, employee_token):
    ok = await _coworker(db_session, "Ok")
    await _coworker(db_session, "Archived", archived=True)
    await _coworker(db_session, "Elsewhere", locs=[OTHER_LOCATION_ID])
    r = await client.get(f"{URL}/me/coworkers", headers=_h(employee_token))
    assert r.status_code == 200
    assert r.json() == [{"id": ok.id, "full_name": "Ok"}]


async def test_last_decision_redacted(client, db_session, me, employee_token):
    cow = await _coworker(db_session, "Pat")
    now = datetime.now(timezone.utc)
    db_session.add(EmployeePreferenceSubmission(
        company_id=COMPANY_ID, employee_id=me.id, status="approved", submitted_payload={},
        reviewed_at=now,
        approved_payload={
            "max_hours_per_week": 20,
            "affinities": [{"target_employee_id": cow.id, "relationship": "prefer_with",
                            "level": 0.5}],
            "blackouts": [],
            "day_preferences": [{"day_of_week": 2, "weight": 0.7}],
            "hour_range_preferences": [], "caps": []}))
    await db_session.commit()
    ld = (await client.get(f"{URL}/me", headers=_h(employee_token))).json()["last_decision"]
    assert ld["status"] == "approved"
    assert ld["approved"]["coworkers"] == [{"target_name": "Pat"}]
    assert ld["approved"]["day_preferences"] == [{"day_of_week": 2}]
    assert not ({"weight", "level", "employee_importance", "relationship"} & _keys(ld))
