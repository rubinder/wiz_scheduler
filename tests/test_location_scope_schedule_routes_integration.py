"""Location-scoped managers (#149, #150) on the scheduling routers.

Covers schedules, shift templates, special hours, open shifts and check-ins.
Two locations in one company: the admin (`manager_token`) sees and acts on
both exactly as before; the regular manager (`regular_token`) is assigned
LOCATION_ID only, so every row at OTHER_LOCATION_ID answers 404 (the same as
another company's row) and every list leaves it out.
"""

from datetime import datetime, time, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import (
    Company,
    Employee,
    EmployeeCheckIn,
    EmployeeRole,
    Shift,
    ShiftSchedule,
    ShiftTemplate,
    SpecialHoursDay,
)
from backend.models.ownership_group import OwnershipGroup
from tests.conftest import (
    COMPANY_ID,
    LOCATION_ID,
    OTHER_LOCATION_ID,
    ROLE_FLOOR_ID,
    _id,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

TOMORROW = datetime.now(timezone.utc).date() + timedelta(days=1)
WEEK = TOMORROW - timedelta(days=TOMORROW.weekday())


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _entry(location_id: str, employee_id: str) -> dict:
    return {
        "employee_id": employee_id, "employee_name": "X",
        "role_id": ROLE_FLOOR_ID, "role_name": "Floor Associate",
        "location_id": location_id, "date": TOMORROW.isoformat(),
        "start_time": "09:00", "end_time": "17:00", "status": "ok",
    }


@pytest_asyncio.fixture
async def world(
    db_session: AsyncSession, seed_roles, seed_location, other_location, regular_manager,
) -> dict:
    """One of everything at each location, on a paid plan."""
    og_id = _id()
    db_session.add(OwnershipGroup(id=og_id, name="G", stripe_subscription_id="sub_x"))
    await db_session.flush()
    await db_session.execute(
        update(Company).where(Company.id == COMPANY_ID).values(ownership_group_id=og_id)
    )

    ids: dict = {"in": {}, "out": {}}
    emp_in, emp_out, emp_both = _id(), _id(), _id()
    for eid, name, locs in (
        (emp_in, "In Ivy", [LOCATION_ID]),
        (emp_out, "Out Olga", [OTHER_LOCATION_ID]),
        (emp_both, "Both Bea", [LOCATION_ID, OTHER_LOCATION_ID]),
    ):
        db_session.add(Employee(id=eid, company_id=COMPANY_ID, full_name=name,
                                email=f"{eid}@x.com", location_ids=locs))
    await db_session.flush()
    for eid in (emp_in, emp_out, emp_both):
        db_session.add(EmployeeRole(id=_id(), company_id=COMPANY_ID, employee_id=eid,
                                    role_id=ROLE_FLOOR_ID, skill_level=3))
    ids.update(emp_in=emp_in, emp_out=emp_out, emp_both=emp_both)

    start = datetime.combine(TOMORROW, time(13, 0), tzinfo=timezone.utc)
    for key, loc, emp in (("in", LOCATION_ID, emp_in), ("out", OTHER_LOCATION_ID, emp_out)):
        tmpl = ShiftTemplate(id=_id(), company_id=COMPANY_ID, location_id=loc,
                             name=f"T-{key}", weekly_schedule=[])
        db_session.add(tmpl)
        await db_session.flush()
        sh = SpecialHoursDay(id=_id(), company_id=COMPANY_ID, location_id=loc,
                             date=TOMORROW + timedelta(days=30),
                             open_time=time(10), close_time=time(14))
        draft = ShiftSchedule(id=_id(), company_id=COMPANY_ID, location_id=loc,
                              week_start_date=WEEK, status="draft",
                              raw_llm_output="[]")
        approved = ShiftSchedule(id=_id(), company_id=COMPANY_ID, location_id=loc,
                                 week_start_date=WEEK, status="approved",
                                 raw_llm_output="[]")
        db_session.add_all([sh, draft, approved])
        await db_session.flush()
        # An open shift (counted by the badge) and an assigned one with a
        # check-in (reported).
        open_shift = Shift(id=_id(), company_id=COMPANY_ID, shift_schedule_id=approved.id,
                           location_id=loc, employee_id=None, role_id=ROLE_FLOOR_ID,
                           role_name="Floor Associate", date=TOMORROW,
                           start_time=start, end_time=start + timedelta(hours=8))
        held = Shift(id=_id(), company_id=COMPANY_ID, shift_schedule_id=approved.id,
                     location_id=loc, employee_id=emp, role_id=ROLE_FLOOR_ID,
                     role_name="Floor Associate", date=TOMORROW,
                     start_time=start, end_time=start + timedelta(hours=8))
        db_session.add_all([open_shift, held])
        await db_session.flush()
        now = datetime.now(timezone.utc)
        db_session.add(EmployeeCheckIn(
            company_id=COMPANY_ID, location_id=loc, employee_id=emp, shift_id=None,
            checked_in_at=now, local_date=now.date(), counter=0, status="on_time",
        ))
        ids[key] = {"template": tmpl.id, "special": sh.id, "draft": draft.id,
                    "approved": approved.id, "location": loc}
    await db_session.commit()
    return ids


# ---------------------------------------------------------------------------
# Single-row endpoints: admin reaches both locations, regular only their own
# ---------------------------------------------------------------------------

def _single_row_calls(w: dict, side: str) -> list[tuple[str, str, dict | None]]:
    r = w[side]
    return [
        ("PUT", f"/api/v1/schedules/{r['draft']}/shifts", {"shifts": []}),
        ("PUT", f"/api/v1/schedules/{r['approved']}/approved-shifts", {"edits": []}),
        ("PUT", f"/api/v1/shift-templates/{r['template']}", {"name": "Renamed"}),
        ("PUT", f"/api/v1/special-hours/{r['special']}", {"label": "Holiday"}),
        ("GET", f"/api/v1/check-ins/qr?location_id={r['location']}", None),
        ("POST", "/api/v1/schedules/candidates", {
            "location_id": r["location"], "role_id": ROLE_FLOOR_ID,
            "date": TOMORROW.isoformat(), "start_time": "09:00", "end_time": "17:00",
        }),
    ]


@pytest.fixture(autouse=True)
def _qr_secret(monkeypatch):
    monkeypatch.setattr(settings, "CHECKIN_QR_SECRET", "test-secret-value")


@pytest.mark.parametrize("index", range(6))
async def test_regular_manager_gets_404_out_of_scope_and_200_in_scope(
    client: AsyncClient, world, manager_token, regular_token, index,
):
    method, url, body = _single_row_calls(world, "out")[index]
    resp = await client.request(method, url, headers=_h(regular_token), json=body)
    assert resp.status_code == 404, resp.text

    method, url, body = _single_row_calls(world, "in")[index]
    resp = await client.request(method, url, headers=_h(regular_token), json=body)
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("index", range(6))
async def test_admin_unchanged_on_both_locations(
    client: AsyncClient, world, manager_token, index,
):
    for side in ("in", "out"):
        method, url, body = _single_row_calls(world, side)[index]
        resp = await client.request(method, url, headers=_h(manager_token), json=body)
        assert resp.status_code == 200, (side, resp.text)


async def test_reject_and_approve_out_of_scope_are_404(
    client: AsyncClient, world, manager_token, regular_token,
):
    for action in ("approve", "reject"):
        resp = await client.post(
            f"/api/v1/schedules/{world['out']['draft']}/{action}", headers=_h(regular_token))
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"] == "Schedule not found"

    resp = await client.post(
        f"/api/v1/schedules/{world['in']['draft']}/reject", headers=_h(regular_token))
    assert resp.status_code == 200, resp.text
    resp = await client.post(
        f"/api/v1/schedules/{world['out']['draft']}/reject", headers=_h(manager_token))
    assert resp.status_code == 200, resp.text


async def test_approve_in_scope_and_admin(
    client: AsyncClient, world, manager_token, regular_token,
):
    resp = await client.post(
        f"/api/v1/schedules/{world['in']['draft']}/approve", headers=_h(regular_token))
    assert resp.status_code == 200, resp.text
    resp = await client.post(
        f"/api/v1/schedules/{world['out']['draft']}/approve", headers=_h(manager_token))
    assert resp.status_code == 200, resp.text


async def test_approve_refuses_a_draft_entry_at_an_out_of_scope_location(
    client: AsyncClient, db_session: AsyncSession, world, regular_token,
):
    import json

    await db_session.execute(
        update(ShiftSchedule).where(ShiftSchedule.id == world["in"]["draft"]).values(
            raw_llm_output=json.dumps([_entry(OTHER_LOCATION_ID, world["emp_both"])]))
    )
    await db_session.commit()
    resp = await client.post(
        f"/api/v1/schedules/{world['in']['draft']}/approve", headers=_h(regular_token))
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Location not found"


async def test_draft_save_refuses_an_entry_at_an_out_of_scope_location(
    client: AsyncClient, world, manager_token, regular_token,
):
    url = f"/api/v1/schedules/{world['in']['draft']}/shifts"
    body = {"shifts": [_entry(OTHER_LOCATION_ID, world["emp_both"])]}
    resp = await client.put(url, headers=_h(regular_token), json=body)
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Location not found"

    body = {"shifts": [_entry(LOCATION_ID, world["emp_both"])]}
    resp = await client.put(url, headers=_h(regular_token), json=body)
    assert resp.status_code == 200, resp.text

    # The admin may still put any company location in a draft.
    body = {"shifts": [_entry(OTHER_LOCATION_ID, world["emp_both"])]}
    resp = await client.put(url, headers=_h(manager_token), json=body)
    assert resp.status_code == 200, resp.text


async def test_week_list_is_filtered(client: AsyncClient, world, manager_token, regular_token):
    url = f"/api/v1/schedules/week/{WEEK.isoformat()}"
    admin = {s["location_id"] for s in (await client.get(url, headers=_h(manager_token))).json()}
    assert admin == {LOCATION_ID, OTHER_LOCATION_ID}
    regular = (await client.get(url, headers=_h(regular_token))).json()
    assert {s["location_id"] for s in regular} == {LOCATION_ID}
    assert len(regular) == 2


async def test_company_wide_schedule_reads_stay_open(
    client: AsyncClient, world, regular_token,
):
    for url in ("/api/v1/schedules/ai-credits", "/api/v1/schedules/schedule-quota"):
        resp = await client.get(url, headers=_h(regular_token))
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Generate: which templates (and so locations) reach the graph
# ---------------------------------------------------------------------------

@pytest.fixture
def captured(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    async def fake_pipeline(**kwargs):
        calls.append(kwargs)
        if False:
            yield {}

    monkeypatch.setattr("backend.scheduling.graph.run_scheduling_pipeline", fake_pipeline)
    return calls


async def _generate(client: AsyncClient, token: str, **extra) -> int:
    resp = await client.post(
        "/api/v1/schedules/generate", headers=_h(token),
        json={"week_start_date": WEEK.isoformat(), "use_local": True, **extra},
    )
    async for _ in resp.aiter_lines():
        pass
    return resp.status_code


async def test_generate_all_means_all_for_admin(client, world, manager_token, captured):
    assert await _generate(client, manager_token) == 200
    assert captured[0]["template_ids"] is None


async def test_generate_all_means_scoped_for_regular(client, world, regular_token, captured):
    assert await _generate(client, regular_token) == 200
    assert captured[0]["template_ids"] == [world["in"]["template"]]


async def test_generate_named_in_scope_passes_through(client, world, regular_token, captured):
    status = await _generate(
        client, regular_token,
        location_ids=[LOCATION_ID], template_ids=[world["in"]["template"]],
    )
    assert status == 200
    assert captured[0]["template_ids"] == [world["in"]["template"]]


@pytest.mark.parametrize("extra", [
    {"location_ids": [OTHER_LOCATION_ID]},
    {"location_ids": [LOCATION_ID, OTHER_LOCATION_ID]},
    {"template_ids": ["OUT"]},
])
async def test_generate_out_of_scope_is_404(client, world, regular_token, captured, extra):
    if extra.get("template_ids") == ["OUT"]:
        extra = {"template_ids": [world["in"]["template"], world["out"]["template"]]}
    assert await _generate(client, regular_token, **extra) == 404
    assert captured == []


async def test_generate_admin_may_name_any_location(client, world, manager_token, captured):
    status = await _generate(
        client, manager_token,
        location_ids=[OTHER_LOCATION_ID], template_ids=[world["out"]["template"]],
    )
    assert status == 200
    assert captured[0]["template_ids"] == [world["out"]["template"]]


async def test_generate_with_nothing_in_scope_schedules_nothing(
    client, db_session: AsyncSession, world, regular_token, captured,
):
    from sqlalchemy import delete

    await db_session.execute(
        delete(ShiftTemplate).where(ShiftTemplate.id == world["in"]["template"]))
    await db_session.commit()
    assert await _generate(client, regular_token) == 200
    assert captured == []


# ---------------------------------------------------------------------------
# Shift templates and special hours
# ---------------------------------------------------------------------------

async def test_shift_template_list_and_writes(
    client: AsyncClient, world, manager_token, regular_token,
):
    for qs in ("", "?include_overrides=true"):
        url = f"/api/v1/shift-templates/{qs}"
        admin = {t["location_id"] for t in (await client.get(url, headers=_h(manager_token))).json()}
        assert admin == {LOCATION_ID, OTHER_LOCATION_ID}
        regular = {t["location_id"] for t in (await client.get(url, headers=_h(regular_token))).json()}
        assert regular == {LOCATION_ID}

    body = {"location_id": OTHER_LOCATION_ID, "name": "N", "weekly_schedule": []}
    resp = await client.post("/api/v1/shift-templates/", headers=_h(regular_token), json=body)
    assert resp.status_code == 404, resp.text
    resp = await client.post("/api/v1/shift-templates/", headers=_h(manager_token), json=body)
    assert resp.status_code == 201, resp.text
    resp = await client.post("/api/v1/shift-templates/", headers=_h(regular_token),
                             json={**body, "location_id": LOCATION_ID})
    assert resp.status_code == 201, resp.text

    resp = await client.delete(f"/api/v1/shift-templates/{world['out']['template']}",
                               headers=_h(regular_token))
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Shift template not found"
    resp = await client.delete(f"/api/v1/shift-templates/{world['in']['template']}",
                               headers=_h(regular_token))
    assert resp.status_code == 204
    resp = await client.delete(f"/api/v1/shift-templates/{world['out']['template']}",
                               headers=_h(manager_token))
    assert resp.status_code == 204


async def test_special_hours_list_and_writes(
    client: AsyncClient, world, manager_token, regular_token,
):
    url = "/api/v1/special-hours/"
    admin = {r["location_id"] for r in (await client.get(url, headers=_h(manager_token))).json()}
    assert admin == {LOCATION_ID, OTHER_LOCATION_ID}
    regular = (await client.get(url, headers=_h(regular_token))).json()
    assert {r["location_id"] for r in regular} == {LOCATION_ID}
    # Naming the other location explicitly still returns nothing.
    resp = await client.get(f"{url}?location_id={OTHER_LOCATION_ID}", headers=_h(regular_token))
    assert resp.json() == []

    body = {"location_id": OTHER_LOCATION_ID, "date": (TOMORROW + timedelta(days=40)).isoformat(),
            "open_time": "10:00:00", "close_time": "14:00:00"}
    resp = await client.post(url, headers=_h(regular_token), json=body)
    assert resp.status_code == 404, resp.text
    resp = await client.post(url, headers=_h(manager_token), json=body)
    assert resp.status_code == 200, resp.text
    resp = await client.post(url, headers=_h(regular_token),
                             json={**body, "location_id": LOCATION_ID})
    assert resp.status_code == 200, resp.text

    resp = await client.delete(f"{url}{world['out']['special']}", headers=_h(regular_token))
    assert resp.status_code == 404
    resp = await client.delete(f"{url}{world['in']['special']}", headers=_h(regular_token))
    assert resp.status_code == 204
    resp = await client.delete(f"{url}{world['out']['special']}", headers=_h(manager_token))
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Open shifts and check-ins
# ---------------------------------------------------------------------------

async def test_candidates_at_an_in_scope_location_match_the_admin(
    client: AsyncClient, world, manager_token, regular_token,
):
    # Only employees at the location are ever offered, so a scoped manager
    # sees exactly the admin's list (and never someone only at another one).
    # A day nobody holds a shift, so no one is dropped for a clash.
    body = {"location_id": LOCATION_ID, "role_id": ROLE_FLOOR_ID,
            "date": (TOMORROW + timedelta(days=3)).isoformat(),
            "start_time": "09:00", "end_time": "17:00"}
    url = "/api/v1/schedules/candidates"
    admin = (await client.post(url, headers=_h(manager_token), json=body)).json()
    regular = (await client.post(url, headers=_h(regular_token), json=body)).json()
    assert regular == admin
    assert {c["employee_id"] for c in regular} == {world["emp_in"], world["emp_both"]}


async def test_open_shift_count_is_scoped(
    client: AsyncClient, world, manager_token, regular_token,
):
    url = "/api/v1/schedules/open-shifts/count"
    assert (await client.get(url, headers=_h(manager_token))).json() == {"count": 2}
    assert (await client.get(url, headers=_h(regular_token))).json() == {"count": 1}


async def test_check_in_report_is_scoped(
    client: AsyncClient, world, manager_token, regular_token,
):
    url = "/api/v1/check-ins/report"
    admin = (await client.get(url, headers=_h(manager_token))).json()
    assert {r["location_id"] for r in admin["rows"]} == {LOCATION_ID, OTHER_LOCATION_ID}
    regular = (await client.get(url, headers=_h(regular_token))).json()
    assert {r["location_id"] for r in regular["rows"]} == {LOCATION_ID}
    assert all(a["location_id"] == LOCATION_ID for a in regular["attestation"])


async def test_draft_save_refuses_an_employee_who_works_only_elsewhere(
    client: AsyncClient, world, manager_token, regular_token,
):
    url = f"/api/v1/schedules/{world['in']['draft']}/shifts"
    body = {"shifts": [_entry(LOCATION_ID, world["emp_out"])]}
    resp = await client.put(url, headers=_h(regular_token), json=body)
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Employee not found"

    resp = await client.put(url, headers=_h(manager_token), json=body)
    assert resp.status_code == 200, resp.text


async def test_generate_refuses_unknown_and_one_off_template_ids(
    client, db_session: AsyncSession, world, regular_token, captured,
):
    """The graph schedules every location when no recurring template
    matches, so an id that matches nothing must be refused, not passed on."""
    assert await _generate(client, regular_token, template_ids=["zzzzzzzz"]) == 404
    one_off = ShiftTemplate(id=_id(), company_id=COMPANY_ID, location_id=LOCATION_ID,
                            name="One-off", weekly_schedule=[],
                            specific_date=TOMORROW + timedelta(days=3))
    db_session.add(one_off)
    await db_session.commit()
    assert await _generate(client, regular_token, template_ids=[one_off.id]) == 404
    assert captured == []


async def test_approved_edit_refuses_a_shift_at_an_out_of_scope_location(
    client: AsyncClient, db_session: AsyncSession, world, regular_token,
):
    """A schedule at an in-scope location holding a shift elsewhere: that
    shift is only as editable as its own location."""
    start = datetime.combine(TOMORROW, time(13, 0), tzinfo=timezone.utc)
    stray = Shift(id=_id(), company_id=COMPANY_ID, shift_schedule_id=world["in"]["approved"],
                  location_id=OTHER_LOCATION_ID, employee_id=None, role_id=ROLE_FLOOR_ID,
                  role_name="Floor Associate", date=TOMORROW,
                  start_time=start, end_time=start + timedelta(hours=8))
    db_session.add(stray)
    await db_session.commit()
    stray_id = stray.id
    resp = await client.put(
        f"/api/v1/schedules/{world['in']['approved']}/approved-shifts",
        headers=_h(regular_token),
        json={"edits": [{"shift_id": stray_id, "deleted": True}]},
    )
    assert resp.status_code == 400, resp.text
    from sqlalchemy import select
    still = (await db_session.execute(select(Shift.id).where(Shift.id == stray_id))).scalar_one_or_none()
    assert still == stray_id
