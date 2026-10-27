"""Manager review of employee preference submissions (#189), and the
decision notification."""
import asyncio
import sys
from datetime import datetime, timezone
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import (
    Company, Employee, EmployeeAffinity, EmployeeDayBlackout, EmployeeDayPreference,
    EmployeeHourRangeCap, EmployeeHourRangePreference, User,
)
from backend.models.og_email_send_log import OgEmailSendLog
from backend.models.ownership_group import OwnershipGroup
from backend.models.preference_submission import EmployeePreferenceSubmission
from tests.conftest import (
    COMPANY2_ID, COMPANY_ID, EMPLOYEE_USER_ID, LOCATION_ID, MANAGER_USER_ID,
    OTHER_LOCATION_ID, _id, _make_token,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
URL = "/api/v1/preference-submissions"


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _keys(obj: Any, acc: set[str] | None = None) -> set[str]:
    acc = set() if acc is None else acc
    if isinstance(obj, dict):
        for k, v in obj.items():
            acc.add(k)
            _keys(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _keys(v, acc)
    return acc


async def _enable(db: AsyncSession, on: bool = True, company_id: str = COMPANY_ID) -> None:
    c = await db.get(Company, company_id)
    c.employee_preference_submissions_enabled = on
    await db.commit()


async def _employee(db: AsyncSession, name: str, locs: list[str], *, user_id: str | None = None,
                    email: str | None = None, archived: bool = False,
                    company_id: str = COMPANY_ID) -> Employee:
    e = Employee(id=_id(), company_id=company_id, full_name=name, location_ids=locs,
                 user_id=user_id, email=email,
                 archived_at=datetime.now(timezone.utc) if archived else None)
    db.add(e)
    await db.commit()
    return e


async def _pending(db: AsyncSession, emp: Employee, payload: dict | None = None,
                   status: str = "pending") -> EmployeePreferenceSubmission:
    s = EmployeePreferenceSubmission(
        company_id=emp.company_id, employee_id=emp.id, status=status,
        submitted_payload=payload or {"max_hours_per_week": 30},
    )
    db.add(s)
    await db.commit()
    return s


@pytest_asyncio.fixture
async def setup(db_session, seed_location, other_location, seed_manager, seed_employee_user):
    """Feature on; `me` (the employee user) works at both locations; Pat is a coworker."""
    await _enable(db_session)
    me = await _employee(db_session, "Me", [LOCATION_ID, OTHER_LOCATION_ID],
                         user_id=EMPLOYEE_USER_ID, email="me@test.com")
    pat = await _employee(db_session, "Pat", [LOCATION_ID])
    return me, pat


def _approved(pat_id: str) -> dict[str, Any]:
    return {
        "max_hours_per_week": 32,
        "affinities": [{"target_employee_id": pat_id, "relationship": "prefer_apart",
                        "level": -0.5}],
        "blackouts": [{"day_of_week": 1, "start_time": "08:00", "end_time": "12:00"}],
        "day_preferences": [{"day_of_week": 2, "weight": 0.3, "employee_importance": "high"}],
        "hour_range_preferences": [{"start_time": "09:00", "end_time": "17:00",
                                    "weight": 0.9, "employee_importance": "low"}],
        "caps": [{"start_time": "22:00", "end_time": "06:00", "max_per_week": 2,
                  "weight": 0.9, "employee_importance": "medium"}],
    }


# ── List / detail / scope ──

async def test_list_and_detail_admin_and_regular(client, db_session, setup, manager_token,
                                                 regular_token):
    me, pat = setup
    sub = await _pending(db_session, me)
    # The admin reviews `me`, who works at both locations.
    r = await client.get(f"{URL}/", headers=_h(manager_token))
    assert r.status_code == 200, r.text
    assert r.json() == [{
        "id": sub.id, "employee_id": me.id, "employee_name": "Me",
        "location_ids": [LOCATION_ID, OTHER_LOCATION_ID],
        "submitted_at": r.json()[0]["submitted_at"],
    }]
    d = await client.get(f"{URL}/{sub.id}", headers=_h(manager_token))
    assert d.status_code == 200, d.text
    assert d.json()["employee"] == {"id": me.id, "full_name": "Me",
                                    "location_ids": [LOCATION_ID, OTHER_LOCATION_ID]}
    assert d.json()["status"] == "pending"
    assert d.json()["submitted_payload"]["max_hours_per_week"] == 30

    # The regular manager (LOCATION_ID only) could not edit `me` directly, so
    # cannot review them either; Pat (LOCATION_ID only) is theirs.
    pat_sub = await _pending(db_session, pat)
    r = await client.get(f"{URL}/", headers=_h(regular_token))
    assert [row["id"] for row in r.json()] == [pat_sub.id]
    assert (await client.get(f"{URL}/{sub.id}", headers=_h(regular_token))).status_code == 404
    r = await client.post(f"{URL}/{sub.id}/approve", json=_approved(pat.id),
                          headers=_h(regular_token))
    assert r.status_code == 404
    assert (await client.get(f"{URL}/{pat_sub.id}", headers=_h(regular_token))).status_code == 200


async def test_regular_manager_affinity_changes_need_editable_coworker(
    client, db_session, setup, regular_token,
):
    """Adding, changing or removing an association with a coworker the
    regular manager could not edit is refused; leaving it as is works."""
    me, pat = setup  # me works at both locations, so me is not editable
    lee = await _employee(db_session, "Lee", [LOCATION_ID])
    lee_id, me_id, pat_id = lee.id, me.id, pat.id
    today = datetime.now(timezone.utc).date()
    db_session.add(EmployeeAffinity(company_id=COMPANY_ID, employee_id=lee_id,
                                    target_employee_id=me_id, level=1,
                                    entry_date=today))
    await db_session.commit()

    def body(affs: list[dict[str, Any]]) -> dict[str, Any]:
        return {"max_hours_per_week": None, "affinities": affs}

    keep_me = {"target_employee_id": me_id, "relationship": "prefer_with", "level": 1}
    add_pat = {"target_employee_id": pat_id, "relationship": "prefer_with", "level": 0.5}

    for affs in ([keep_me | {"level": 0.5}],   # changed
                 [],                             # removed
                 ):
        sub = await _pending(db_session, await db_session.get(Employee, lee_id))
        r = await client.post(f"{URL}/{sub.id}/approve", json=body(affs),
                              headers=_h(regular_token))
        # Refused with a coded 422, not a 404: the submission still exists.
        assert r.status_code == 422, (affs, r.text)
        detail = r.json()["detail"]
        assert detail["code"] == "affinity_coworker_out_of_scope", detail
        assert detail["target_employee_id"] == me_id
        assert "Me" in detail["message"]
        assert (await db_session.get(EmployeePreferenceSubmission, sub.id,
                                     populate_existing=True)).status == "pending"
        await db_session.rollback()
        await db_session.execute(
            update(EmployeePreferenceSubmission)
            .where(EmployeePreferenceSubmission.employee_id == lee_id,
                   EmployeePreferenceSubmission.status == "pending")
            .values(status="denied"))
        await db_session.commit()

    sub = await _pending(db_session, await db_session.get(Employee, lee_id))
    r = await client.post(f"{URL}/{sub.id}/approve", json=body([keep_me, add_pat]),
                          headers=_h(regular_token))
    assert r.status_code == 200, r.text
    rows = {a.target_employee_id: a for a in (await db_session.execute(
        select(EmployeeAffinity).where(EmployeeAffinity.employee_id == lee_id)
        .execution_options(populate_existing=True))).scalars().all()}
    assert set(rows) == {me_id, pat_id}
    assert float(rows[me_id].level) == 1.0
    assert rows[me_id].entry_date == today


@pytest.mark.parametrize("who", ["admin", "regular"])
async def test_level_zero_association_round_trips(client, db_session, setup, manager_token,
                                                  regular_token, who):
    """A live level-0 ("Neutral") association shows as neutral and approving
    it unchanged succeeds, also for a regular manager who could not edit the
    coworker (unchanged rows are allowed)."""
    me, pat = setup  # me works at both locations: not editable by `regular`
    lee = await _employee(db_session, "Lee", [LOCATION_ID])
    lee_id, me_id = lee.id, me.id
    today = datetime.now(timezone.utc).date()
    db_session.add(EmployeeAffinity(company_id=COMPANY_ID, employee_id=lee_id,
                                    target_employee_id=me_id, level=0, entry_date=today))
    await db_session.commit()
    sub = await _pending(db_session, lee, {
        "affinities": [{"target_employee_id": me_id, "relationship": "neutral"}]})
    token = manager_token if who == "admin" else regular_token
    d = (await client.get(f"{URL}/{sub.id}", headers=_h(token))).json()
    [cur] = d["current"]["affinities"]
    assert cur["relationship"] == "neutral" and cur["level"] == 0.0
    r = await client.post(f"{URL}/{sub.id}/approve", headers=_h(token), json={
        "affinities": [{"target_employee_id": me_id, "relationship": "neutral", "level": 0}]})
    assert r.status_code == 200, r.text
    [row] = (await db_session.execute(
        select(EmployeeAffinity).where(EmployeeAffinity.employee_id == lee_id)
        .execution_options(populate_existing=True))).scalars().all()
    assert float(row.level) == 0.0 and row.entry_date == today


async def test_detail_current_carries_weights_and_levels(client, db_session, setup, manager_token):
    me, pat = setup
    today = datetime.now(timezone.utc).date()
    db_session.add_all([
        EmployeeDayPreference(company_id=COMPANY_ID, employee_id=me.id, day_of_week=4, weight=0.8),
        EmployeeAffinity(company_id=COMPANY_ID, employee_id=me.id, target_employee_id=pat.id,
                         level=1, entry_date=today),
    ])
    await db_session.commit()
    sub = await _pending(db_session, me)
    cur = (await client.get(f"{URL}/{sub.id}", headers=_h(manager_token))).json()["current"]
    assert cur["day_preferences"] == [{"day_of_week": 4, "weight": 0.8}]
    assert cur["affinities"][0]["level"] == 1.0
    assert cur["affinities"][0]["target_name"] == "Pat"


async def test_employee_cannot_use_manager_routes(client, db_session, setup, employee_token):
    me, _ = setup
    sub = await _pending(db_session, me)
    assert (await client.get(f"{URL}/", headers=_h(employee_token))).status_code == 403
    assert (await client.get(f"{URL}/{sub.id}", headers=_h(employee_token))).status_code == 403


async def test_regular_manager_out_of_scope_404(client, db_session, setup, regular_token):
    _, pat = setup
    only_other = await _employee(db_session, "Elsewhere", [OTHER_LOCATION_ID])
    sub = await _pending(db_session, only_other)
    assert (await client.get(f"{URL}/", headers=_h(regular_token))).json() == []
    assert (await client.get(f"{URL}/{sub.id}", headers=_h(regular_token))).status_code == 404
    r = await client.post(f"{URL}/{sub.id}/approve", json={}, headers=_h(regular_token))
    assert r.status_code == 404
    r = await client.post(f"{URL}/{sub.id}/deny", json={}, headers=_h(regular_token))
    assert r.status_code == 404
    await db_session.refresh(sub)
    assert sub.status == "pending"


async def _other_company_manager(db: AsyncSession, og_id: str | None = None) -> str:
    db.add(Company(id=COMPANY2_ID, name="Other Co", slug="other-co",
                   ownership_group_id=og_id, employee_preference_submissions_enabled=True))
    await db.flush()
    uid = _id()
    db.add(User(id=uid, company_id=COMPANY2_ID, email="m2@test.com", hashed_password="x",
                full_name="Other Manager", user_role="manager", manager_type="admin"))
    await db.commit()
    return _make_token(uid, COMPANY2_ID, "manager")


async def test_other_company_manager_404(client, db_session, setup):
    me, _ = setup
    sub = await _pending(db_session, me)
    tok = await _other_company_manager(db_session)
    assert (await client.get(f"{URL}/", headers=_h(tok))).json() == []
    assert (await client.get(f"{URL}/{sub.id}", headers=_h(tok))).status_code == 404
    assert (await client.post(f"{URL}/{sub.id}/approve", json={},
                              headers=_h(tok))).status_code == 404
    assert (await client.post(f"{URL}/{sub.id}/deny", json={},
                              headers=_h(tok))).status_code == 404


async def test_two_companies_one_og_filtered_by_callers_company(client, db_session, setup,
                                                                 manager_token):
    """Review Focus 4: the flag is per company; a manager sees only their own
    company's submissions even when the OG has another company with it on."""
    me, _ = setup
    og = OwnershipGroup(id=_id(), name="Group")
    db_session.add(og)
    await db_session.flush()
    (await db_session.get(Company, COMPANY_ID)).ownership_group_id = og.id
    await db_session.commit()
    tok2 = await _other_company_manager(db_session, og.id)
    sister = await _employee(db_session, "Sister", [], company_id=COMPANY2_ID)
    sister_sub = await _pending(db_session, sister)
    my_sub = await _pending(db_session, me)
    await _enable(db_session, False)
    # Company 1 disabled: its manager sees nothing, not even the sister company's.
    assert (await client.get(f"{URL}/", headers=_h(manager_token))).json() == []
    await _enable(db_session, True)
    ids = [r["id"] for r in (await client.get(f"{URL}/", headers=_h(manager_token))).json()]
    assert ids == [my_sub.id]
    assert (await client.get(f"{URL}/{sister_sub.id}",
                             headers=_h(manager_token))).status_code == 404
    ids2 = [r["id"] for r in (await client.get(f"{URL}/", headers=_h(tok2))).json()]
    assert ids2 == [sister_sub.id]


async def test_disabled_hides_and_freezes(client, db_session, setup, manager_token):
    me, _ = setup
    sub = await _pending(db_session, me)
    await _enable(db_session, False)
    assert (await client.get(f"{URL}/", headers=_h(manager_token))).json() == []
    assert (await client.get(f"{URL}/{sub.id}", headers=_h(manager_token))).status_code == 404
    assert (await client.post(f"{URL}/{sub.id}/deny", json={},
                              headers=_h(manager_token))).status_code == 404
    await db_session.refresh(sub)
    assert sub.status == "pending"
    await _enable(db_session, True)
    assert [r["id"] for r in (await client.get(f"{URL}/", headers=_h(manager_token))).json()] \
        == [sub.id]


async def test_archived_employee_hidden(client, db_session, setup, manager_token):
    me, _ = setup
    sub = await _pending(db_session, me)
    me.archived_at = datetime.now(timezone.utc)
    await db_session.commit()
    assert (await client.get(f"{URL}/", headers=_h(manager_token))).json() == []
    assert (await client.get(f"{URL}/{sub.id}", headers=_h(manager_token))).status_code == 404
    assert (await client.post(f"{URL}/{sub.id}/approve", json={},
                              headers=_h(manager_token))).status_code == 404


async def test_list_ordered_by_submitted_at(client, db_session, setup, manager_token):
    me, pat = setup
    first = await _pending(db_session, pat)
    first.submitted_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    second = await _pending(db_session, me)
    await db_session.commit()
    ids = [r["id"] for r in (await client.get(f"{URL}/", headers=_h(manager_token))).json()]
    assert ids == [first.id, second.id]


# ── Approve ──

async def test_approve_replaces_live_rows(client, db_session, setup, manager_token):
    me, pat = setup
    gone_target = await _employee(db_session, "Sam", [LOCATION_ID])
    archived_target = await _employee(db_session, "Old", [LOCATION_ID], archived=True)
    moved_target = await _employee(db_session, "Moved", ["elsewhr1"])
    today = datetime.now(timezone.utc).date()
    cid = COMPANY_ID
    db_session.add_all([
        EmployeeDayPreference(company_id=cid, employee_id=me.id, day_of_week=5, weight=0.8),
        EmployeeHourRangePreference(company_id=cid, employee_id=me.id, start_time="06:00",
                                    end_time="08:00", weight=0.5),
        EmployeeHourRangeCap(company_id=cid, employee_id=me.id, start_time="10:00",
                             end_time="12:00", max_per_week=1, weight=0.5),
        EmployeeDayBlackout(company_id=cid, employee_id=me.id, day_of_week=6,
                            start_time="10:00", end_time="11:00"),
        EmployeeAffinity(company_id=cid, employee_id=me.id, target_employee_id=gone_target.id,
                         level=-1, entry_date=today),
        EmployeeAffinity(company_id=cid, employee_id=me.id,
                         target_employee_id=archived_target.id, level=1, entry_date=today),
        EmployeeAffinity(company_id=cid, employee_id=me.id,
                         target_employee_id=moved_target.id, level=0.5, entry_date=today),
        EmployeeAffinity(company_id=cid, employee_id=pat.id, target_employee_id=me.id,
                         level=1, entry_date=today),
    ])
    await db_session.commit()
    sub = await _pending(db_session, me)

    r = await client.post(f"{URL}/{sub.id}/approve", json=_approved(pat.id),
                          headers=_h(manager_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved"
    assert body["approved_payload"]["day_preferences"][0]["weight"] == 0.3

    async def rows(model: Any) -> list[Any]:
        return list((await db_session.execute(
            select(model).where(model.employee_id == me.id)
            .execution_options(populate_existing=True))).scalars().all())

    days = await rows(EmployeeDayPreference)
    assert [(d.day_of_week, float(d.weight)) for d in days] == [(2, 0.3)]
    ranges = await rows(EmployeeHourRangePreference)
    assert [(x.start_time, x.end_time, float(x.weight)) for x in ranges] == [("09:00", "17:00", 0.9)]
    caps = await rows(EmployeeHourRangeCap)
    assert [(c.start_time, c.end_time, c.max_per_week, float(c.weight)) for c in caps] \
        == [("22:00", "06:00", 2, 0.9)]
    blackouts = await rows(EmployeeDayBlackout)
    assert [(b.day_of_week, b.start_time, b.end_time) for b in blackouts] == [(1, "08:00", "12:00")]
    affs = {a.target_employee_id: a for a in await rows(EmployeeAffinity)}
    # Replaced: Sam gone, Pat added with the manager's level and today's entry date.
    assert set(affs) == {pat.id, archived_target.id, moved_target.id}
    assert float(affs[pat.id].level) == -0.5
    assert affs[pat.id].entry_date == today
    # Targets that are archived or no longer share a location were never
    # shown to the manager, so they are kept.
    assert float(affs[archived_target.id].level) == 1.0
    assert float(affs[moved_target.id].level) == 0.5
    # Incoming affinity untouched.
    incoming = (await db_session.execute(select(EmployeeAffinity).where(
        EmployeeAffinity.employee_id == pat.id))).scalars().all()
    assert len(incoming) == 1

    await db_session.refresh(me)
    assert me.max_hours_per_week == 32
    await db_session.refresh(sub)
    assert sub.status == "approved"
    assert sub.reviewed_by == MANAGER_USER_ID
    assert sub.reviewed_at is not None
    assert sub.approved_payload["affinities"][0]["level"] == -0.5
    assert sub.approved_payload["affinities"][0]["entry_date"] == today.isoformat()


async def test_approve_empty_clears_sections(client, db_session, setup, manager_token):
    me, _ = setup
    db_session.add(EmployeeDayPreference(company_id=COMPANY_ID, employee_id=me.id,
                                         day_of_week=5, weight=0.8))
    me.max_hours_per_week = 40
    await db_session.commit()
    sub = await _pending(db_session, me)
    r = await client.post(f"{URL}/{sub.id}/approve", json={}, headers=_h(manager_token))
    assert r.status_code == 200, r.text
    n = (await db_session.execute(select(func.count()).select_from(EmployeeDayPreference)
                                  .where(EmployeeDayPreference.employee_id == me.id))).scalar()
    assert n == 0
    await db_session.refresh(me)
    assert me.max_hours_per_week is None


@pytest.mark.parametrize("mutate", [
    lambda b: b["day_preferences"][0].pop("weight"),
    lambda b: b["hour_range_preferences"][0].pop("weight"),
    lambda b: b["caps"][0].pop("weight"),
    lambda b: b["affinities"][0].pop("level"),
    lambda b: b["affinities"][0].__setitem__("level", 0.7),
    lambda b: b.__setitem__("day_preferences", [
        {"day_of_week": d, "weight": 0.5} for d in range(4)]),
])
async def test_approve_validation_422(client, db_session, setup, manager_token, mutate):
    me, pat = setup
    sub = await _pending(db_session, me)
    body = _approved(pat.id)
    mutate(body)
    r = await client.post(f"{URL}/{sub.id}/approve", json=body, headers=_h(manager_token))
    assert r.status_code == 422, r.text
    await db_session.refresh(sub)
    assert sub.status == "pending"


async def test_approve_rejects_target_without_shared_location(client, db_session, setup,
                                                              manager_token):
    me, _ = setup
    loner = await _employee(db_session, "Loner", [])
    sub = await _pending(db_session, me)
    r = await client.post(f"{URL}/{sub.id}/approve", headers=_h(manager_token), json={
        "affinities": [{"target_employee_id": loner.id, "relationship": "prefer_with",
                        "level": 1}]})
    assert r.status_code == 422
    await db_session.refresh(sub)
    assert sub.status == "pending"


# ── Deny / 409 ──

async def test_deny_stores_reason_and_leaves_live_rows(client, db_session, setup, manager_token):
    me, _ = setup
    db_session.add(EmployeeDayPreference(company_id=COMPANY_ID, employee_id=me.id,
                                         day_of_week=5, weight=0.8))
    await db_session.commit()
    sub = await _pending(db_session, me, {"day_preferences": [
        {"day_of_week": 1, "employee_importance": "low"}]})
    r = await client.post(f"{URL}/{sub.id}/deny", json={"reason": "x" * 501},
                          headers=_h(manager_token))
    assert r.status_code == 422
    r = await client.post(f"{URL}/{sub.id}/deny", json={"reason": "Short staffed"},
                          headers=_h(manager_token))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "denied"
    assert r.json()["denial_reason"] == "Short staffed"
    await db_session.refresh(sub)
    assert (sub.status, sub.denial_reason, sub.reviewed_by) == ("denied", "Short staffed",
                                                                MANAGER_USER_ID)
    days = (await db_session.execute(select(EmployeeDayPreference.day_of_week).where(
        EmployeeDayPreference.employee_id == me.id))).scalars().all()
    assert days == [5]


async def test_second_decision_and_superseded_409(client, db_session, setup, manager_token):
    me, pat = setup
    old = await _pending(db_session, me, status="superseded")
    sub = await _pending(db_session, me)
    r = await client.post(f"{URL}/{old.id}/approve", json={}, headers=_h(manager_token))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "submission_already_decided"
    assert (await client.post(f"{URL}/{sub.id}/deny", json={},
                              headers=_h(manager_token))).status_code == 200
    for action, body in (("approve", _approved(pat.id)), ("deny", {"reason": None})):
        r = await client.post(f"{URL}/{sub.id}/{action}", json=body, headers=_h(manager_token))
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "submission_already_decided"


async def test_concurrent_approves_one_wins(db_session, setup, manager_token, isolated_client):
    me, pat = setup
    sub = await _pending(db_session, me)
    body = _approved(pat.id)
    r1, r2 = await asyncio.gather(
        isolated_client.post(f"{URL}/{sub.id}/approve", json=body, headers=_h(manager_token)),
        isolated_client.post(f"{URL}/{sub.id}/approve", json=body, headers=_h(manager_token)),
    )
    assert sorted([r1.status_code, r2.status_code]) == [200, 409], (r1.text, r2.text)
    n = (await db_session.execute(select(func.count()).select_from(EmployeeDayPreference)
                                  .where(EmployeeDayPreference.employee_id == me.id))).scalar()
    assert n == 1


# ── Housekeeping ──

async def test_erase_employee_removes_submissions(db_session, setup):
    from backend.services.archive import erase_employee
    me, _ = setup
    await _pending(db_session, me, status="denied")
    await _pending(db_session, me)
    await erase_employee(db_session, COMPANY_ID, me)
    await db_session.commit()
    n = (await db_session.execute(select(func.count()).select_from(EmployeePreferenceSubmission)
                                  .where(EmployeePreferenceSubmission.employee_id == me.id))
         ).scalar()
    assert n == 0


# ── Notification (Task 4) ──

@pytest.fixture
def fake_resend(monkeypatch) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    class FakeEmails:
        @staticmethod
        def send(payload: dict[str, Any]) -> None:
            sent.append(payload)

    class FakeResend:
        api_key = None
        Emails = FakeEmails()

    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(settings, "FROM_EMAIL", "noreply@wiz.test")
    monkeypatch.setitem(sys.modules, "resend", FakeResend)
    return sent


async def _with_og(db: AsyncSession) -> str:
    og = OwnershipGroup(id=_id(), name="Group")
    db.add(og)
    await db.flush()
    (await db.get(Company, COMPANY_ID)).ownership_group_id = og.id
    await db.commit()
    return og.id


async def _log_count(db: AsyncSession, og_id: str) -> int:
    return (await db.execute(select(func.count()).select_from(OgEmailSendLog).where(
        OgEmailSendLog.ownership_group_id == og_id,
        OgEmailSendLog.kind == "preference_decision"))).scalar()


async def test_approve_emails_employee_without_scale_values(client, db_session, setup,
                                                            manager_token, employee_token,
                                                            fake_resend):
    me, pat = setup
    og_id = await _with_og(db_session)
    sub = await _pending(db_session, me)
    r = await client.post(f"{URL}/{sub.id}/approve", json=_approved(pat.id),
                          headers=_h(manager_token))
    assert r.status_code == 200, r.text
    assert len(fake_resend) == 1
    mail = fake_resend[0]
    assert mail["to"] == ["me@test.com"]
    text = mail["subject"] + mail["html"]
    assert "Pat" in text and "22:00" in text
    for bad in ("0.3", "0.9", "-0.5", "prefer_apart", "Prefer apart", "weight", "level"):
        assert bad not in text, bad
    for word in ("low", "medium", "high"):
        assert f" {word}" not in text.lower() and f">{word}" not in text.lower(), word
    assert await _log_count(db_session, og_id) == 1

    me_body = (await client.get(f"{URL}/me", headers=_h(employee_token))).json()
    ld = me_body["last_decision"]
    assert ld["status"] == "approved"
    assert not ({"weight", "level", "employee_importance", "relationship"} & _keys(ld))
    assert "0.3" not in str(ld) and "0.9" not in str(ld)


async def test_deny_emails_linked_user_when_employee_has_no_email(client, db_session, setup,
                                                                  manager_token, fake_resend):
    me, _ = setup
    me.email = None
    await db_session.commit()
    await _with_og(db_session)
    sub = await _pending(db_session, me)
    r = await client.post(f"{URL}/{sub.id}/deny", json={"reason": "<b>no</b>"},
                          headers=_h(manager_token))
    assert r.status_code == 200
    assert [m["to"] for m in fake_resend] == [["employee@test.com"]]
    assert "&lt;b&gt;no&lt;/b&gt;" in fake_resend[0]["html"]


async def test_no_recipient_skips_email(client, db_session, setup, manager_token, fake_resend):
    _, pat = setup  # Pat has no email and no user
    og_id = await _with_og(db_session)
    sub = await _pending(db_session, pat)
    r = await client.post(f"{URL}/{sub.id}/deny", json={}, headers=_h(manager_token))
    assert r.status_code == 200
    assert fake_resend == []
    assert await _log_count(db_session, og_id) == 0


async def test_quota_exhausted_skips_email_but_approves(client, db_session, setup, manager_token,
                                                        fake_resend, monkeypatch):
    me, pat = setup
    og_id = await _with_og(db_session)
    monkeypatch.setattr(settings, "OG_EMAIL_DAILY_CAP", 0)
    sub = await _pending(db_session, me)
    r = await client.post(f"{URL}/{sub.id}/approve", json=_approved(pat.id),
                          headers=_h(manager_token))
    assert r.status_code == 200, r.text
    assert fake_resend == []
    assert await _log_count(db_session, og_id) == 0
    await db_session.refresh(sub)
    assert sub.status == "approved"


async def test_email_failure_never_fails_decision(client, db_session, setup, manager_token,
                                                  monkeypatch):
    me, _ = setup
    await _with_og(db_session)

    class Boom:
        api_key = None

        class Emails:
            @staticmethod
            def send(payload: dict[str, Any]) -> None:
                raise RuntimeError("resend down")

    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test_key")
    monkeypatch.setitem(sys.modules, "resend", Boom)
    sub = await _pending(db_session, me)
    r = await client.post(f"{URL}/{sub.id}/deny", json={}, headers=_h(manager_token))
    assert r.status_code == 200

    async def explode(*a: Any, **k: Any) -> None:
        raise RuntimeError("quota db down")

    import backend.routers.preference_submissions as router_mod
    monkeypatch.setattr(router_mod, "check_and_log_email", explode)
    sub2 = await _pending(db_session, me)
    r = await client.post(f"{URL}/{sub2.id}/deny", json={}, headers=_h(manager_token))
    assert r.status_code == 200
    await db_session.refresh(sub2)
    assert sub2.status == "denied"
