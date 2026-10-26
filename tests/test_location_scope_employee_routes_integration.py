"""Location-scoped managers on employee-keyed routes (#149, spec Q2).

Employees: A and A2 at LOCATION_ID only, B at OTHER_LOCATION_ID only, AB at
both. The regular manager is assigned LOCATION_ID, so they see A, A2 and AB,
edit only A and A2, and get 404 for anything else. The admin sees and edits
everything, as before.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Employee,
    EmployeeAffinity,
    EmployeeAvailability,
    EmployeeDayBlackout,
    EmployeeDayPreference,
    EmployeeHourRangeCap,
    EmployeeHourRangePreference,
    EmployeeInvite,
    Location,
    User,
)
from tests.conftest import COMPANY_ID, LOCATION_ID, OTHER_LOCATION_ID, _id

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

API = "/api/v1"
LOCS = {
    "A": [LOCATION_ID],
    "A2": [LOCATION_ID],
    "B": [OTHER_LOCATION_ID],
    "AB": [LOCATION_ID, OTHER_LOCATION_ID],
}
REGULAR_VISIBLE = {"A", "A2", "AB"}
ALL = set(LOCS)


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def world(
    db_session: AsyncSession,
    other_location: Location,
    regular_manager: User,
) -> dict:
    """Employees plus one row of every employee-keyed resource each."""
    emp: dict[str, str] = {}
    for key, lids in LOCS.items():
        e = Employee(
            id=_id(), company_id=COMPANY_ID, full_name=f"Emp {key}",
            email=f"emp-{key.lower()}@test.com", location_ids=lids,
        )
        db_session.add(e)
        emp[key] = e.id
    await db_session.flush()

    rows: dict[str, dict[str, str]] = {
        k: {} for k in ("avail", "blackout", "day", "hour", "cap", "invite")
    }
    start = datetime(2026, 11, 2, 14, tzinfo=timezone.utc)
    for key, eid in emp.items():
        objs = {
            "avail": EmployeeAvailability(
                id=_id(), company_id=COMPANY_ID, employee_id=eid,
                year=2026, month=11, day=2,
                start_time=start, end_time=start + timedelta(hours=8),
            ),
            "blackout": EmployeeDayBlackout(
                id=_id(), company_id=COMPANY_ID, employee_id=eid,
                day_of_week=0, start_time="09:00", end_time="12:00",
            ),
            "day": EmployeeDayPreference(
                id=_id(), company_id=COMPANY_ID, employee_id=eid,
                day_of_week=1, weight=0.5,
            ),
            "hour": EmployeeHourRangePreference(
                id=_id(), company_id=COMPANY_ID, employee_id=eid,
                start_time="08:00", end_time="12:00", weight=0.5,
            ),
            "cap": EmployeeHourRangeCap(
                id=_id(), company_id=COMPANY_ID, employee_id=eid,
                start_time="18:00", end_time="23:00", max_per_week=2, weight=0.5,
            ),
            "invite": EmployeeInvite(
                id=_id(), company_id=COMPANY_ID, employee_id=eid,
                token=_id() + _id(), email=f"emp-{key.lower()}@test.com",
                status="pending",
                expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            ),
        }
        for kind, obj in objs.items():
            db_session.add(obj)
            rows[kind][key] = obj.id

    aff: dict[str, str] = {}
    for name, (a, b) in {
        "A-A2": ("A", "A2"),   # visible, editable
        "A-AB": ("A", "AB"),   # visible, not editable
        "A-B": ("A", "B"),     # not visible
    }.items():
        obj = EmployeeAffinity(
            id=_id(), company_id=COMPANY_ID, employee_id=emp[a],
            target_employee_id=emp[b], level=0.5,
            entry_date=datetime.now(timezone.utc).date(),
        )
        db_session.add(obj)
        aff[name] = obj.id
    await db_session.commit()
    return {"emp": emp, "rows": rows, "aff": aff}


def _keys_by_employee(world: dict, items: list[dict], field: str = "employee_id") -> set[str]:
    by_id = {v: k for k, v in world["emp"].items()}
    return {by_id[i[field]] for i in items if i[field] in by_id}


# ── Employees ──


async def test_list_employees_scoped(
    client: AsyncClient, world: dict, manager_token: str, regular_token: str,
) -> None:
    admin = await client.get(f"{API}/employees/", headers=_h(manager_token))
    regular = await client.get(f"{API}/employees/", headers=_h(regular_token))
    assert admin.status_code == regular.status_code == 200
    assert _keys_by_employee(world, admin.json(), "id") == ALL
    assert _keys_by_employee(world, regular.json(), "id") == REGULAR_VISIBLE


@pytest.mark.parametrize(
    "location_ids, expected",
    [
        ([LOCATION_ID], 201),
        ([OTHER_LOCATION_ID], 404),
        ([LOCATION_ID, OTHER_LOCATION_ID], 404),
        ([], 404),
        (None, 404),
    ],
)
async def test_create_employee_requires_all_locations(
    client: AsyncClient, world: dict, regular_token: str, location_ids, expected: int,
) -> None:
    body = {"full_name": "New Hire", "location_ids": location_ids}
    resp = await client.post(f"{API}/employees/", headers=_h(regular_token), json=body)
    assert resp.status_code == expected, resp.text


async def test_admin_create_employee_anywhere(
    client: AsyncClient, world: dict, manager_token: str,
) -> None:
    for lids in ([OTHER_LOCATION_ID], None):
        resp = await client.post(
            f"{API}/employees/", headers=_h(manager_token),
            json={"full_name": "Admin Hire", "location_ids": lids},
        )
        assert resp.status_code == 201, resp.text


@pytest.mark.parametrize("key, expected", [("A", 200), ("B", 404), ("AB", 404)])
async def test_update_employee_requires_editable(
    client: AsyncClient, world: dict, regular_token: str, manager_token: str,
    key: str, expected: int,
) -> None:
    url = f"{API}/employees/{world['emp'][key]}"
    resp = await client.put(url, headers=_h(regular_token), json={"full_name": "Renamed"})
    assert resp.status_code == expected
    if expected == 404:
        assert resp.json()["detail"] == "Employee not found"
    admin = await client.put(url, headers=_h(manager_token), json={"full_name": "Admin Renamed"})
    assert admin.status_code == 200


async def test_update_employee_cannot_add_out_of_scope_location(
    client: AsyncClient, world: dict, regular_token: str,
) -> None:
    url = f"{API}/employees/{world['emp']['A']}"
    resp = await client.put(
        url, headers=_h(regular_token),
        json={"location_ids": [LOCATION_ID, OTHER_LOCATION_ID]},
    )
    assert resp.status_code == 404
    resp = await client.put(url, headers=_h(regular_token), json={"location_ids": []})
    assert resp.status_code == 404
    resp = await client.put(url, headers=_h(regular_token), json={"location_ids": [LOCATION_ID]})
    assert resp.status_code == 200


@pytest.mark.parametrize("key, expected", [("A", 204), ("B", 404), ("AB", 404)])
async def test_archive_and_restore_employee_require_editable(
    client: AsyncClient, world: dict, regular_token: str, manager_token: str,
    key: str, expected: int,
) -> None:
    eid = world["emp"][key]
    resp = await client.delete(f"{API}/employees/{eid}", headers=_h(regular_token))
    assert resp.status_code == expected
    if expected == 404:
        admin = await client.delete(f"{API}/employees/{eid}", headers=_h(manager_token))
        assert admin.status_code == 204
    restore = await client.post(f"{API}/employees/{eid}/restore", headers=_h(regular_token))
    assert restore.status_code == (200 if expected == 204 else 404)


async def test_bulk_upload_is_admin_only(
    client: AsyncClient, world: dict, regular_token: str,
) -> None:
    resp = await client.post(
        f"{API}/employees/bulk-upload", headers=_h(regular_token),
        files={"file": ("e.csv", "full_name,email\nX,x@test.com\n", "text/csv")},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Admin access required"


# ── Availability and day blackouts ──


@pytest.mark.parametrize("path", ["/employees/availability/all", "/employees/day-blackouts"])
async def test_company_wide_availability_lists_scoped(
    client: AsyncClient, world: dict, manager_token: str, regular_token: str, path: str,
) -> None:
    admin = await client.get(f"{API}{path}", headers=_h(manager_token))
    regular = await client.get(f"{API}{path}", headers=_h(regular_token))
    assert admin.status_code == regular.status_code == 200
    assert _keys_by_employee(world, admin.json()) == ALL
    assert _keys_by_employee(world, regular.json()) == REGULAR_VISIBLE


@pytest.mark.parametrize("suffix", ["availability", "day-blackouts"])
@pytest.mark.parametrize("key, expected", [("A", 200), ("AB", 200), ("B", 404)])
async def test_per_employee_availability_read_needs_visible(
    client: AsyncClient, world: dict, regular_token: str, manager_token: str,
    suffix: str, key: str, expected: int,
) -> None:
    url = f"{API}/employees/{world['emp'][key]}/{suffix}"
    assert (await client.get(url, headers=_h(regular_token))).status_code == expected
    assert (await client.get(url, headers=_h(manager_token))).status_code == 200


@pytest.mark.parametrize("key, expected", [("A", 201), ("AB", 404), ("B", 404)])
async def test_availability_and_blackout_create_need_editable(
    client: AsyncClient, world: dict, regular_token: str, key: str, expected: int,
) -> None:
    eid = world["emp"][key]
    avail = await client.post(
        f"{API}/employees/availability", headers=_h(regular_token),
        json={
            "employee_id": eid, "year": 2026, "month": 11, "day": 3,
            "start_time": "2026-11-03T14:00:00+00:00",
            "end_time": "2026-11-03T20:00:00+00:00",
        },
    )
    assert avail.status_code == expected
    blackout = await client.post(
        f"{API}/employees/day-blackouts", headers=_h(regular_token),
        json={"employee_id": eid, "day_of_week": 2, "start_time": "13:00", "end_time": "15:00"},
    )
    assert blackout.status_code == expected


@pytest.mark.parametrize("key, expected", [("A", 204), ("AB", 404), ("B", 404)])
async def test_availability_and_blackout_delete_need_editable(
    client: AsyncClient, world: dict, regular_token: str, key: str, expected: int,
) -> None:
    rows = world["rows"]
    avail = await client.delete(
        f"{API}/employees/availability/{rows['avail'][key]}", headers=_h(regular_token),
    )
    assert avail.status_code == expected
    if expected == 404:
        assert avail.json()["detail"] == "Availability not found"
    blackout = await client.delete(
        f"{API}/employees/day-blackouts/{rows['blackout'][key]}", headers=_h(regular_token),
    )
    assert blackout.status_code == expected
    if expected == 404:
        assert blackout.json()["detail"] == "Blackout not found"


async def test_admin_deletes_any_availability(
    client: AsyncClient, world: dict, manager_token: str,
) -> None:
    rows = world["rows"]
    for key in ("B", "AB"):
        r1 = await client.delete(
            f"{API}/employees/availability/{rows['avail'][key]}", headers=_h(manager_token),
        )
        r2 = await client.delete(
            f"{API}/employees/day-blackouts/{rows['blackout'][key]}", headers=_h(manager_token),
        )
        assert r1.status_code == r2.status_code == 204


# ── Scheduling preferences ──

PREFS = [
    ("days", "day", {"day_of_week": 3, "weight": 0.5}, {"weight": 0.3}),
    ("hour-ranges", "hour", {"start_time": "13:00", "end_time": "17:00", "weight": 0.5}, {"weight": 0.3}),
    ("caps", "cap", {"start_time": "06:00", "end_time": "09:00", "max_per_week": 1, "weight": 0.5},
     {"max_per_week": 3}),
]


@pytest.mark.parametrize("path, kind, create_body, update_body", PREFS)
async def test_preference_lists_scoped(
    client: AsyncClient, world: dict, manager_token: str, regular_token: str,
    path: str, kind: str, create_body: dict, update_body: dict,
) -> None:
    url = f"{API}/scheduling-preferences/{path}"
    admin = await client.get(url, headers=_h(manager_token))
    regular = await client.get(url, headers=_h(regular_token))
    assert admin.status_code == regular.status_code == 200
    assert _keys_by_employee(world, admin.json()) == ALL
    assert _keys_by_employee(world, regular.json()) == REGULAR_VISIBLE


@pytest.mark.parametrize("path, kind, create_body, update_body", PREFS)
@pytest.mark.parametrize("key, ok", [("A", True), ("AB", False), ("B", False)])
async def test_preference_writes_need_editable(
    client: AsyncClient, world: dict, regular_token: str, manager_token: str,
    path: str, kind: str, create_body: dict, update_body: dict, key: str, ok: bool,
) -> None:
    base = f"{API}/scheduling-preferences/{path}"
    eid = world["emp"][key]
    row_id = world["rows"][kind][key]

    created = await client.post(
        base, headers=_h(regular_token), json={"employee_id": eid, **create_body},
    )
    assert created.status_code == (201 if ok else 404), created.text
    updated = await client.put(f"{base}/{row_id}", headers=_h(regular_token), json=update_body)
    assert updated.status_code == (200 if ok else 404), updated.text
    deleted = await client.delete(f"{base}/{row_id}", headers=_h(regular_token))
    assert deleted.status_code == (204 if ok else 404)

    if not ok:
        # The admin is unaffected.
        created = await client.post(
            base, headers=_h(manager_token), json={"employee_id": eid, **create_body},
        )
        assert created.status_code == 201
        updated = await client.put(f"{base}/{row_id}", headers=_h(manager_token), json=update_body)
        assert updated.status_code == 200
        deleted = await client.delete(f"{base}/{row_id}", headers=_h(manager_token))
        assert deleted.status_code == 204


# ── Affinities ──


async def test_affinity_list_needs_both_visible(
    client: AsyncClient, world: dict, manager_token: str, regular_token: str,
) -> None:
    by_id = {v: k for k, v in world["aff"].items()}
    admin = await client.get(f"{API}/affinities/", headers=_h(manager_token))
    regular = await client.get(f"{API}/affinities/", headers=_h(regular_token))
    assert {by_id[a["id"]] for a in admin.json()} == {"A-A2", "A-AB", "A-B"}
    assert {by_id[a["id"]] for a in regular.json()} == {"A-A2", "A-AB"}


@pytest.mark.parametrize(
    "pair, expected", [(("A2", "A"), 201), (("A", "AB"), 404), (("B", "A"), 404)],
)
async def test_affinity_create_needs_both_editable(
    client: AsyncClient, world: dict, regular_token: str, manager_token: str,
    pair: tuple[str, str], expected: int,
) -> None:
    body = {
        "employee_id": world["emp"][pair[0]],
        "target_employee_id": world["emp"][pair[1]],
        "level": 0.5, "entry_date": str(datetime.now(timezone.utc).date()),
    }
    resp = await client.post(f"{API}/affinities/", headers=_h(regular_token), json=body)
    assert resp.status_code == expected
    admin = await client.post(f"{API}/affinities/", headers=_h(manager_token), json=body)
    assert admin.status_code == 201


@pytest.mark.parametrize("name, ok", [("A-A2", True), ("A-AB", False), ("A-B", False)])
async def test_affinity_update_delete_need_both_editable(
    client: AsyncClient, world: dict, regular_token: str, manager_token: str,
    name: str, ok: bool,
) -> None:
    url = f"{API}/affinities/{world['aff'][name]}"
    updated = await client.put(url, headers=_h(regular_token), json={"level": 0.2})
    assert updated.status_code == (200 if ok else 404)
    deleted = await client.delete(url, headers=_h(regular_token))
    assert deleted.status_code == (204 if ok else 404)
    if not ok:
        assert deleted.json()["detail"] == "Affinity not found"
        assert (await client.delete(url, headers=_h(manager_token))).status_code == 204


async def test_affinity_update_cannot_retarget_out_of_scope(
    client: AsyncClient, world: dict, regular_token: str,
) -> None:
    url = f"{API}/affinities/{world['aff']['A-A2']}"
    for key in ("AB", "B"):
        resp = await client.put(
            url, headers=_h(regular_token), json={"target_employee_id": world["emp"][key]},
        )
        assert resp.status_code == 404


# ── Invites ──


async def test_invite_list_scoped(
    client: AsyncClient, world: dict, manager_token: str, regular_token: str,
) -> None:
    admin = await client.get(f"{API}/employees/invites", headers=_h(manager_token))
    regular = await client.get(f"{API}/employees/invites", headers=_h(regular_token))
    assert admin.status_code == regular.status_code == 200
    assert _keys_by_employee(world, admin.json()) == ALL
    assert _keys_by_employee(world, regular.json()) == REGULAR_VISIBLE


@pytest.mark.parametrize("key, expected", [("A", 200), ("AB", 404), ("B", 404)])
async def test_send_invite_needs_editable(
    client: AsyncClient, world: dict, regular_token: str, manager_token: str,
    key: str, expected: int,
) -> None:
    url = f"{API}/employees/{world['emp'][key]}/invite"
    resp = await client.post(url, headers=_h(regular_token))
    assert resp.status_code == expected, resp.text
    if expected == 404:
        assert (await client.post(url, headers=_h(manager_token))).status_code == 200
