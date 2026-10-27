"""Location-scoped schedule lock at the routes (#180), against Postgres with a
separate session per request (`isolated_client`), as in production.

Tenant layout (no ownership group unless a test builds one):
  * Alpha and Bravo share an employee (Sam), so each one's lock set is {A, B};
  * Charlie shares nobody (Cleo works only there), so its set is {C}.

The pipeline is replaced by a fast fake that resolves locations with the
same helper the graph uses and can be held at a gate, so a test can keep one
generate in flight while it fires another request.
"""
import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import (
    Company, Employee, Location, ManagerLocation, Region, ScheduleLock, ShiftSchedule,
    ShiftTemplate, User,
)
from backend.models.ownership_group import OwnershipGroup
from backend.scheduling.pipeline_locations import recurring_templates_by_location
from tests.conftest import _id, _make_token, get_test_session_factory

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

WEEK = "2026-11-02"
GENERATE = "/api/v1/schedules/generate"


@dataclass
class Tenant:
    company_id: str
    region_id: str
    a: str
    b: str
    c: str
    names: dict[str, str]
    templates: dict[str, str]
    admin_id: str
    admin_token: str
    other_id: str
    sam: str
    cleo: str


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _manager(db: AsyncSession, company_id: str, name: str, *,
                   manager_type: str = "admin") -> str:
    uid = _id()
    db.add(User(
        id=uid, company_id=company_id, email=f"{uid}@example.com", hashed_password="x",
        full_name=name, user_role="manager", manager_type=manager_type,
        email_verified_at=datetime.now(timezone.utc),
    ))
    await db.flush()
    return uid


@pytest_asyncio.fixture
async def tenant(db_session: AsyncSession) -> Tenant:
    company_id, region_id = _id(), _id()
    db_session.add(Company(id=company_id, name="Lock Co", slug=_id()))
    await db_session.flush()
    db_session.add(Region(id=region_id, company_id=company_id, name="R"))
    await db_session.flush()
    names: dict[str, str] = {}
    templates: dict[str, str] = {}
    ids = {}
    for key, name in (("a", "Alpha"), ("b", "Bravo"), ("c", "Charlie")):
        lid = _id()
        ids[key] = lid
        names[lid] = name
        db_session.add(Location(id=lid, company_id=company_id, region_id=region_id,
                                name=name, timezone="UTC"))
    await db_session.flush()
    for lid in names:
        tid = _id()
        templates[lid] = tid
        db_session.add(ShiftTemplate(id=tid, company_id=company_id, location_id=lid,
                                     name="Week", weekly_schedule={}))
    sam, cleo = _id(), _id()
    db_session.add(Employee(id=sam, company_id=company_id, full_name="Sam",
                            location_ids=[ids["a"], ids["b"]]))
    db_session.add(Employee(id=cleo, company_id=company_id, full_name="Cleo",
                            location_ids=[ids["c"]]))
    admin_id = await _manager(db_session, company_id, "Ada Admin")
    other_id = await _manager(db_session, company_id, "Ben Boss")
    await db_session.commit()
    return Tenant(
        company_id=company_id, region_id=region_id, a=ids["a"], b=ids["b"], c=ids["c"],
        names=names, templates=templates, admin_id=admin_id,
        admin_token=_make_token(admin_id, company_id, "manager"),
        other_id=other_id, sam=sam, cleo=cleo,
    )


@dataclass
class Gate:
    """Holds the fake pipeline before each location's result."""
    go: asyncio.Event = field(default_factory=asyncio.Event)
    entered: dict[str, asyncio.Event] = field(default_factory=dict)
    before_yield: Callable[[str], Any] | None = None

    def event(self, location_id: str) -> asyncio.Event:
        return self.entered.setdefault(location_id, asyncio.Event())

    async def reached(self, location_id: str) -> None:
        await asyncio.wait_for(self.event(location_id).wait(), 10)


@pytest.fixture
def gate(monkeypatch, tenant: Tenant) -> Gate:
    g = Gate()

    async def fake_pipeline(*, company_id: str, db: AsyncSession,
                            template_ids: list[str] | None = None, **_: Any
                            ) -> AsyncIterator[dict]:
        by_location = await recurring_templates_by_location(db, company_id, template_ids)
        for location_id in sorted(by_location):
            g.event(location_id).set()
            await asyncio.wait_for(g.go.wait(), 10)
            if g.before_yield is not None:
                await g.before_yield(location_id)
            yield {
                "location_id": location_id,
                "location_name": tenant.names.get(location_id, ""),
                "shifts": [{
                    "employee_id": tenant.sam if location_id != tenant.c else tenant.cleo,
                    "location_id": location_id,
                    "role_id": "r", "role_name": "R", "date": WEEK,
                    "start_time": f"{WEEK}T09:00:00+00:00",
                    "end_time": f"{WEEK}T17:00:00+00:00", "status": "ok",
                }],
                "errors": [],
                "status": "ok",
            }

    monkeypatch.setattr("backend.scheduling.graph.run_scheduling_pipeline", fake_pipeline)
    return g


def _generate(client: AsyncClient, token: str, *locations: str, tenant: Tenant):
    body: dict[str, Any] = {"week_start_date": WEEK, "use_local": True}
    if locations:
        body["template_ids"] = [tenant.templates[lid] for lid in locations]
    return client.post(GENERATE, json=body, headers=_h(token))


def _lines(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


async def _lock_rows(company_id: str) -> list[ScheduleLock]:
    async with get_test_session_factory()() as s:
        return list((await s.execute(
            select(ScheduleLock).where(ScheduleLock.company_id == company_id)
            .order_by(ScheduleLock.location_id)
        )).scalars().all())


async def _schedules(company_id: str) -> list[ShiftSchedule]:
    async with get_test_session_factory()() as s:
        return list((await s.execute(
            select(ShiftSchedule).where(ShiftSchedule.company_id == company_id)
        )).scalars().all())


async def _hold(tenant: Tenant, *location_ids: str, user_id: str | None = None) -> None:
    """A live lock row held by another manager's operation."""
    async with get_test_session_factory()() as s:
        for lid in location_ids:
            s.add(ScheduleLock(
                company_id=tenant.company_id, location_id=lid, lock_group="heldgrp1",
                locked_by_user_id=user_id or tenant.other_id, operation="generate",
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            ))
        await s.commit()


# ---------------------------------------------------------------------------
# Lock sets at the route
# ---------------------------------------------------------------------------


async def test_generate_succeeds_while_an_unrelated_location_is_locked(
    isolated_client, tenant, gate,
):
    await _hold(tenant, tenant.c)
    gate.go.set()
    resp = await _generate(isolated_client, tenant.admin_token, tenant.a, tenant=tenant)
    assert resp.status_code == 200, resp.text
    assert [r["status"] for r in _lines(resp.text)] == ["ok"]


async def test_generate_409s_naming_the_location_that_shares_an_employee(
    isolated_client, tenant, gate,
):
    """Bravo is not in the request, but Sam works at Alpha and Bravo."""
    await _hold(tenant, tenant.b)
    resp = await _generate(isolated_client, tenant.admin_token, tenant.a, tenant=tenant)
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "schedule_locked"
    assert detail["locked_by"] == "Ben Boss"
    assert detail["location_id"] == tenant.b
    assert detail["location_name"] == "Bravo"
    assert "expires_at" in detail


async def test_archived_employee_does_not_widen_the_set(
    isolated_client, db_session, tenant, gate,
):
    await db_session.execute(update(Employee).where(Employee.id == tenant.sam)
                             .values(archived_at=datetime.now(timezone.utc)))
    await db_session.commit()
    await _hold(tenant, tenant.b)
    gate.go.set()
    resp = await _generate(isolated_client, tenant.admin_token, tenant.a, tenant=tenant)
    assert resp.status_code == 200, resp.text


async def test_whole_company_generate_conflicts_with_any_held_lock(
    isolated_client, tenant, gate,
):
    """No template filter from an admin locks every location."""
    await _hold(tenant, tenant.c)
    resp = await _generate(isolated_client, tenant.admin_token, tenant=tenant)
    assert resp.status_code == 409
    assert resp.json()["detail"]["location_name"] == "Charlie"


async def test_restricted_manager_without_filter_locks_only_their_locations(
    isolated_client, db_session, tenant, gate,
):
    """A regular manager's "everything" is their in-scope templates, so a
    lock on a location outside their scope (and its pool) does not block."""
    regular = await _manager(db_session, tenant.company_id, "Rita Regular",
                             manager_type="regular")
    db_session.add(ManagerLocation(manager_id=regular, location_id=tenant.a))
    await db_session.commit()
    await _hold(tenant, tenant.c)

    task = asyncio.create_task(_generate(
        isolated_client, _make_token(regular, tenant.company_id, "manager"), tenant=tenant,
    ))
    await gate.reached(tenant.a)
    held = {(r.location_id, r.lock_group == "heldgrp1") for r in await _lock_rows(tenant.company_id)}
    assert held == {(tenant.a, False), (tenant.b, False), (tenant.c, True)}
    gate.go.set()
    resp = await task
    assert resp.status_code == 200, resp.text
    assert [r["location_id"] for r in _lines(resp.text)] == [tenant.a]


async def test_archived_location_in_the_set_locks_fine(
    isolated_client, db_session, tenant, gate,
):
    """Sam's Bravo is archived: it stays in the set (history may still be
    approved there) and its lock row must not trip the FK."""
    await db_session.execute(update(Location).where(Location.id == tenant.b)
                             .values(archived_at=datetime.now(timezone.utc)))
    await db_session.commit()
    task = asyncio.create_task(
        _generate(isolated_client, tenant.admin_token, tenant.a, tenant=tenant))
    await gate.reached(tenant.a)
    assert [r.location_id for r in await _lock_rows(tenant.company_id)] == sorted([tenant.a, tenant.b])
    gate.go.set()
    assert (await task).status_code == 200


# ---------------------------------------------------------------------------
# Real concurrent generates
# ---------------------------------------------------------------------------


async def test_disjoint_generates_run_at_the_same_time(isolated_client, tenant, gate):
    first = asyncio.create_task(
        _generate(isolated_client, tenant.admin_token, tenant.a, tenant=tenant))
    await gate.reached(tenant.a)
    second = asyncio.create_task(
        _generate(isolated_client, tenant.admin_token, tenant.c, tenant=tenant))
    # Both are inside their pipelines, each holding its own set.
    await gate.reached(tenant.c)
    rows = await _lock_rows(tenant.company_id)
    groups = {r.location_id: r.lock_group for r in rows}
    assert set(groups) == {tenant.a, tenant.b, tenant.c}
    assert groups[tenant.a] == groups[tenant.b] != groups[tenant.c]

    gate.go.set()
    r1, r2 = await asyncio.gather(first, second)
    assert (r1.status_code, r2.status_code) == (200, 200), (r1.text, r2.text)
    assert {s.location_id for s in await _schedules(tenant.company_id)} == {tenant.a, tenant.c}
    assert await _lock_rows(tenant.company_id) == []


async def test_overlapping_generates_one_streams_one_409s(isolated_client, tenant, gate):
    first = asyncio.create_task(
        _generate(isolated_client, tenant.admin_token, tenant.a, tenant=tenant))
    await gate.reached(tenant.a)
    resp = await _generate(isolated_client, tenant.admin_token, tenant.b, tenant=tenant)
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["locked_by"] == "Ada Admin"
    assert detail["location_id"] in {tenant.a, tenant.b}
    assert detail["location_name"] == tenant.names[detail["location_id"]]
    gate.go.set()
    assert (await first).status_code == 200
    assert await _lock_rows(tenant.company_id) == []


async def test_racing_overlapping_generates_never_both_write_an_employee(
    isolated_client, tenant, gate,
):
    """The double-booking check: Alpha and Bravo both schedule Sam for the
    same week. Fired together, exactly one run commits a schedule; the
    other is refused with 409 before it writes anything."""
    async def slow(_: str) -> None:
        await asyncio.sleep(0.3)

    gate.before_yield = slow
    gate.go.set()
    r1, r2 = await asyncio.gather(
        _generate(isolated_client, tenant.admin_token, tenant.a, tenant=tenant),
        _generate(isolated_client, tenant.admin_token, tenant.b, tenant=tenant),
    )
    assert sorted([r1.status_code, r2.status_code]) == [200, 409], (r1.text, r2.text)
    schedules = await _schedules(tenant.company_id)
    assert len(schedules) == 1
    assert [s["employee_id"] for s in json.loads(schedules[0].raw_llm_output)] == [tenant.sam]
    assert await _lock_rows(tenant.company_id) == []


# ---------------------------------------------------------------------------
# approve / edit_approved against an in-flight generate
# ---------------------------------------------------------------------------


async def _draft(db: AsyncSession, tenant: Tenant, location_id: str, *,
                 status: str = "draft") -> str:
    sid = _id()
    db.add(ShiftSchedule(
        id=sid, company_id=tenant.company_id, location_id=location_id,
        week_start_date=date.fromisoformat(WEEK), status=status, raw_llm_output="[]",
        created_at=datetime.now(timezone.utc),
    ))
    await db.commit()
    return sid


async def test_approve_and_edit_are_blocked_by_an_overlapping_generate(
    isolated_client, db_session, tenant, gate,
):
    draft_b = await _draft(db_session, tenant, tenant.b)
    approved_b = await _draft(db_session, tenant, tenant.b, status="approved")
    draft_c = await _draft(db_session, tenant, tenant.c)

    running = asyncio.create_task(
        _generate(isolated_client, tenant.admin_token, tenant.a, tenant=tenant))
    await gate.reached(tenant.a)

    approve = await isolated_client.post(
        f"/api/v1/schedules/{draft_b}/approve", headers=_h(tenant.admin_token))
    assert approve.status_code == 409
    assert approve.json()["detail"]["code"] == "schedule_locked"
    assert approve.json()["detail"]["location_id"] in {tenant.a, tenant.b}

    edit = await isolated_client.put(
        f"/api/v1/schedules/{approved_b}/approved-shifts",
        json={"edits": []}, headers=_h(tenant.admin_token))
    assert edit.status_code == 409
    assert edit.json()["detail"]["code"] == "schedule_locked"

    # Charlie shares nobody with Alpha: approving there is not blocked.
    disjoint = await isolated_client.post(
        f"/api/v1/schedules/{draft_c}/approve", headers=_h(tenant.admin_token))
    assert disjoint.status_code == 200, disjoint.text

    gate.go.set()
    assert (await running).status_code == 200
    assert await _lock_rows(tenant.company_id) == []


async def test_edit_locks_the_locations_of_an_assigned_employee(
    isolated_client, db_session, tenant, gate,
):
    """Assigning Sam on a Charlie schedule pulls Sam's Alpha and Bravo into
    the edit's set, so it conflicts with a generate on Alpha."""
    approved_c = await _draft(db_session, tenant, tenant.c, status="approved")
    running = asyncio.create_task(
        _generate(isolated_client, tenant.admin_token, tenant.a, tenant=tenant))
    await gate.reached(tenant.a)
    edit = await isolated_client.put(
        f"/api/v1/schedules/{approved_c}/approved-shifts",
        json={"edits": [{"employee_id": tenant.sam, "role_id": "nope", "date": WEEK,
                         "start_time": f"{WEEK}T09:00:00+00:00",
                         "end_time": f"{WEEK}T17:00:00+00:00"}]},
        headers=_h(tenant.admin_token))
    assert edit.status_code == 409, edit.text
    gate.go.set()
    await running


# ---------------------------------------------------------------------------
# The pool widening mid-run
# ---------------------------------------------------------------------------


async def test_widened_pool_held_elsewhere_skips_the_write_with_conflict(
    isolated_client, tenant, gate,
):
    running = asyncio.create_task(
        _generate(isolated_client, tenant.admin_token, tenant.c, tenant=tenant))
    await gate.reached(tenant.c)
    # Mid-run, Cleo is also given Alpha, which another manager is scheduling.
    async with get_test_session_factory()() as s:
        await s.execute(update(Employee).where(Employee.id == tenant.cleo)
                        .values(location_ids=[tenant.c, tenant.a]))
        await s.commit()
    await _hold(tenant, tenant.a)
    gate.go.set()

    resp = await running
    assert resp.status_code == 200
    [line] = _lines(resp.text)
    assert line["status"] == "CONFLICT"
    assert line["location_id"] == tenant.c
    assert "Ben Boss" in line["errors"][0] and "Alpha" in line["errors"][0]
    assert "schedule_id" not in line
    assert await _schedules(tenant.company_id) == []
    # Only the other manager's row is left.
    assert [(r.location_id, r.lock_group) for r in await _lock_rows(tenant.company_id)] == [
        (tenant.a, "heldgrp1"),
    ]


async def test_widened_pool_that_is_free_is_locked_and_written(
    isolated_client, tenant, gate,
):
    async def widen(_: str) -> None:
        async with get_test_session_factory()() as s:
            await s.execute(update(Employee).where(Employee.id == tenant.cleo)
                            .values(location_ids=[tenant.c, tenant.a]))
            await s.commit()

    gate.before_yield = widen
    gate.go.set()
    resp = await _generate(isolated_client, tenant.admin_token, tenant.c, tenant=tenant)
    assert resp.status_code == 200
    [line] = _lines(resp.text)
    assert line["status"] == "ok"
    assert "schedule_id" in line
    assert await _lock_rows(tenant.company_id) == []


# ---------------------------------------------------------------------------
# Lock release on every exit path
# ---------------------------------------------------------------------------


async def _call_generate(db: AsyncSession, admin: User, tenant: Tenant):
    """The route called directly, to drive its body iterator by hand."""
    from backend.routers.schedules import generate_schedule
    from backend.schemas.schedule import GenerateRequest
    from backend.services.location_scope import LocationScope

    return await generate_schedule(
        body=GenerateRequest(week_start_date=date.fromisoformat(WEEK), use_local=True,
                             template_ids=[tenant.templates[tenant.a]]),
        request=SimpleNamespace(client=None),
        current_user=admin,
        db=db,
        scope=LocationScope(ids=None),
    )


async def test_lock_released_when_the_client_disconnects(db_session, tenant, gate):
    """Starlette closes the body iterator when the client goes away; the
    stream's finally must release the whole group then too."""
    admin = await db_session.get(User, tenant.admin_id)
    gate.go.set()
    async with get_test_session_factory()() as request_db:
        iterator = (await _call_generate(request_db, admin, tenant)).body_iterator
        first = await iterator.__anext__()
        assert json.loads(first)["status"] == "ok"
        assert {r.location_id for r in await _lock_rows(tenant.company_id)} == {tenant.a, tenant.b}
        await iterator.aclose()
    assert await _lock_rows(tenant.company_id) == []


async def test_lock_not_left_behind_when_the_stream_never_starts(db_session, tenant, gate):
    """A client gone before the first byte: the generator never runs, so its
    finally cannot release. The rows were never committed either, and the
    request session's close rolls them back."""
    admin = await db_session.get(User, tenant.admin_id)
    async with get_test_session_factory()() as request_db:
        response = await _call_generate(request_db, admin, tenant)
        assert await _lock_rows(tenant.company_id) == []  # not yet visible
        del response
    assert await _lock_rows(tenant.company_id) == []
    # And nothing blocks the next generate on those locations.
    async with get_test_session_factory()() as s:
        from backend.services.schedule_lock import acquire, release
        handle = await acquire(s, company_id=tenant.company_id, user_id=tenant.other_id,
                               operation="generate", location_ids=[tenant.a, tenant.b])
        await release(s, handle.group)
        await s.commit()


async def test_lock_released_after_an_empty_run(isolated_client, tenant, monkeypatch):
    async def nothing(**_: Any) -> AsyncIterator[dict]:
        return
        yield  # pragma: no cover

    monkeypatch.setattr("backend.scheduling.graph.run_scheduling_pipeline", nothing)
    resp = await _generate(isolated_client, tenant.admin_token, tenant.a, tenant=tenant)
    assert resp.status_code == 200
    assert await _lock_rows(tenant.company_id) == []


async def test_lock_released_when_the_pipeline_raises(isolated_client, tenant, monkeypatch):
    async def boom(**_: Any) -> AsyncIterator[dict]:
        raise RuntimeError("boom")
        yield  # pragma: no cover

    monkeypatch.setattr("backend.scheduling.graph.run_scheduling_pipeline", boom)
    resp = await _generate(isolated_client, tenant.admin_token, tenant.a, tenant=tenant)
    assert resp.status_code == 200
    assert _lines(resp.text)[0]["status"] == "PIPELINE_ERROR"
    assert await _lock_rows(tenant.company_id) == []


# ---------------------------------------------------------------------------
# Free-plan quota, re-checked at write time
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def free_group(db_session: AsyncSession, tenant: Tenant) -> SimpleNamespace:
    """A free ownership group of two companies: ours (Alpha, Bravo and
    Charlie, with Bravo and Charlie archived so the plan's location count
    stays at its limit) and a sibling with one location, Delta.

    Bravo already started this month, so one of FREE_PLAN_MAX_LOCATIONS (2)
    starts is left: Alpha passes the route's pre-check."""
    og_id, sibling_id, delta = _id(), _id(), _id()
    db_session.add(OwnershipGroup(id=og_id, name="Free G"))
    await db_session.flush()
    await db_session.execute(update(Company).where(Company.id == tenant.company_id)
                             .values(ownership_group_id=og_id))
    db_session.add(Company(id=sibling_id, name="Sibling", slug=_id(), ownership_group_id=og_id))
    await db_session.flush()
    sibling_region = _id()
    db_session.add(Region(id=sibling_region, company_id=sibling_id, name="R2"))
    await db_session.flush()
    db_session.add(Location(id=delta, company_id=sibling_id, region_id=sibling_region,
                            name="Delta", timezone="UTC"))
    await db_session.execute(update(Location).where(Location.id.in_([tenant.b, tenant.c]))
                             .values(archived_at=datetime.now(timezone.utc)))
    await db_session.flush()
    db_session.add(ShiftSchedule(
        id=_id(), company_id=tenant.company_id, location_id=tenant.b,
        week_start_date=date.fromisoformat(WEEK), status="draft",
        created_at=datetime.now(timezone.utc),
    ))
    await db_session.commit()
    assert settings.FREE_PLAN_MAX_LOCATIONS == 2
    return SimpleNamespace(og_id=og_id, sibling_id=sibling_id, delta=delta)


async def _started_locations(og_company_ids: list[str]) -> set[str]:
    async with get_test_session_factory()() as s:
        return {str(r) for r in (await s.execute(
            select(ShiftSchedule.location_id).where(
                ShiftSchedule.company_id.in_(og_company_ids)).distinct()
        )).scalars().all()}


async def test_quota_rechecked_after_a_sibling_commits_mid_run(
    isolated_client, tenant, gate, free_group,
):
    """A sibling company's run (a different company, so no shared schedule
    lock) starts Delta between our pre-check and our write. The write-time
    re-check refuses Alpha instead of starting a third location."""
    async def sibling_commits(_: str) -> None:
        async with get_test_session_factory()() as s:
            s.add(ShiftSchedule(
                id=_id(), company_id=free_group.sibling_id, location_id=free_group.delta,
                week_start_date=date.fromisoformat(WEEK), status="draft",
                created_at=datetime.now(timezone.utc),
            ))
            await s.commit()

    gate.before_yield = sibling_commits
    gate.go.set()
    resp = await _generate(isolated_client, tenant.admin_token, tenant.a, tenant=tenant)
    assert resp.status_code == 200, resp.text
    [line] = _lines(resp.text)
    assert line["status"] == "QUOTA_EXCEEDED"
    assert "schedule_id" not in line
    started = await _started_locations([tenant.company_id, free_group.sibling_id])
    assert started == {tenant.b, free_group.delta}
    assert len(started) <= settings.FREE_PLAN_MAX_LOCATIONS
    assert await _lock_rows(tenant.company_id) == []


async def test_quota_recheck_waits_for_a_concurrent_write_in_the_group(
    isolated_client, tenant, gate, free_group,
):
    """The sibling's write holds the ownership-group row lock when ours
    arrives. Without our FOR UPDATE we would read before its commit, pass,
    and start a third location; with it we wait, then see Delta."""
    holding = asyncio.Event()
    finished: list[asyncio.Task] = []

    async def sibling_write() -> None:
        async with get_test_session_factory()() as s:
            await s.execute(select(OwnershipGroup.id).where(
                OwnershipGroup.id == free_group.og_id).with_for_update())
            s.add(ShiftSchedule(
                id=_id(), company_id=free_group.sibling_id, location_id=free_group.delta,
                week_start_date=date.fromisoformat(WEEK), status="draft",
                created_at=datetime.now(timezone.utc),
            ))
            await s.flush()
            holding.set()
            await asyncio.sleep(0.5)
            await s.commit()

    async def start_sibling(_: str) -> None:
        finished.append(asyncio.create_task(sibling_write()))
        await asyncio.wait_for(holding.wait(), 10)

    gate.before_yield = start_sibling
    gate.go.set()
    resp = await _generate(isolated_client, tenant.admin_token, tenant.a, tenant=tenant)
    await asyncio.gather(*finished)
    [line] = _lines(resp.text)
    assert line["status"] == "QUOTA_EXCEEDED", line
    started = await _started_locations([tenant.company_id, free_group.sibling_id])
    assert started == {tenant.b, free_group.delta}


# ---------------------------------------------------------------------------
# Final-review fix round
# ---------------------------------------------------------------------------


async def test_pipeline_quota_refusal_passes_through_and_the_generated_location_is_written(
    isolated_client, db_session, tenant, monkeypatch,
):
    """C1. The pipeline's quota verdict is per batch: with one start left it
    allows Alpha and refuses Charlie, yielding Charlie's refusal first. That
    refusal must reach the stream as is. Re-checking Charlie on its own,
    before Alpha is written, passed it, saved an empty draft that spent the
    start, and then refused the schedule Alpha really generated."""
    from backend.services.location_quota import resolve_location_quota

    og_id, sibling_id, delta = _id(), _id(), _id()
    db_session.add(OwnershipGroup(id=og_id, name="Free G"))
    await db_session.flush()
    await db_session.execute(update(Company).where(Company.id == tenant.company_id)
                             .values(ownership_group_id=og_id))
    db_session.add(Company(id=sibling_id, name="Sib", slug=_id(), ownership_group_id=og_id))
    await db_session.flush()
    sibling_region = _id()
    db_session.add(Region(id=sibling_region, company_id=sibling_id, name="R2"))
    await db_session.flush()
    # The sibling's archived Delta already started this month: 1 of 2 starts.
    db_session.add(Location(id=delta, company_id=sibling_id, region_id=sibling_region,
                            name="Delta", timezone="UTC",
                            archived_at=datetime.now(timezone.utc)))
    await db_session.execute(update(Location).where(Location.id == tenant.b)
                             .values(archived_at=datetime.now(timezone.utc)))
    await db_session.flush()
    db_session.add(ShiftSchedule(
        id=_id(), company_id=sibling_id, location_id=delta,
        week_start_date=date.fromisoformat(WEEK), status="draft",
        created_at=datetime.now(timezone.utc),
    ))
    await db_session.commit()
    assert settings.FREE_PLAN_MAX_LOCATIONS == 2

    async def batch_pipeline(*, company_id: str, db: AsyncSession, **_: Any
                             ) -> AsyncIterator[dict]:
        # As graph.run_scheduling_pipeline: one verdict for the batch,
        # refusals yielded before any result.
        order = [tenant.a, tenant.c]
        quota = await resolve_location_quota(db, company_id, order, WEEK)
        assert [quota[lid]["allowed"] for lid in order] == [True, False], quota
        for lid in order:
            if not quota[lid]["allowed"]:
                yield {"location_id": lid, "location_name": tenant.names[lid], "shifts": [],
                       "errors": [quota[lid]["message"]], "status": "QUOTA_EXCEEDED"}
        for lid in order:
            if quota[lid]["allowed"]:
                yield {"location_id": lid, "location_name": tenant.names[lid],
                       "shifts": [], "errors": [], "status": "ok"}

    monkeypatch.setattr("backend.scheduling.graph.run_scheduling_pipeline", batch_pipeline)
    resp = await _generate(isolated_client, tenant.admin_token, tenant=tenant)
    assert resp.status_code == 200, resp.text
    lines = {line["location_id"]: line for line in _lines(resp.text)}
    assert lines[tenant.c]["status"] == "QUOTA_EXCEEDED"
    assert "schedule_id" not in lines[tenant.c]
    assert lines[tenant.a]["status"] == "ok"
    assert "schedule_id" in lines[tenant.a]
    assert [s.location_id for s in await _schedules(tenant.company_id)] == [tenant.a]
    assert await _lock_rows(tenant.company_id) == []


async def test_lock_released_on_an_asgi_client_disconnect(db_session, tenant, gate):
    """I1. Driven through Starlette's StreamingResponse as uvicorn does
    (ASGI spec 2.3): a disconnect cancels the task running the stream, and
    anyio cancellation is level-triggered, so an unshielded release in the
    stream's finally is itself cancelled and the committed rows leak for
    the whole TTL."""
    admin = await db_session.get(User, tenant.admin_id)
    async with get_test_session_factory()() as request_db:
        response = await _call_generate(request_db, admin, tenant)

        async def receive() -> dict:
            await gate.reached(tenant.a)  # the pipeline is in flight
            assert {r.location_id for r in await _lock_rows(tenant.company_id)} == {
                tenant.a, tenant.b}
            return {"type": "http.disconnect"}

        async def send(_message: dict) -> None:
            pass

        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}}
        await asyncio.wait_for(response(scope, receive, send), 10)
        # Released by the stream's own cleanup, not by the session closing.
        assert await _lock_rows(tenant.company_id) == []
    assert await _schedules(tenant.company_id) == []


async def test_lock_released_when_the_pipeline_aborts_the_transaction(
    isolated_client, tenant, monkeypatch,
):
    """I1. A DB error leaves the request transaction aborted; the cleanup
    rolls back before releasing instead of failing the release too."""
    from sqlalchemy import text

    async def db_error(*, db: AsyncSession, **_: Any) -> AsyncIterator[dict]:
        await db.execute(text("SELECT 1/0"))
        yield {}  # pragma: no cover

    monkeypatch.setattr("backend.scheduling.graph.run_scheduling_pipeline", db_error)
    resp = await _generate(isolated_client, tenant.admin_token, tenant.a, tenant=tenant)
    assert resp.status_code == 200
    assert _lines(resp.text)[0]["status"] == "PIPELINE_ERROR"
    assert await _lock_rows(tenant.company_id) == []


async def test_each_write_refreshes_the_runs_whole_lock(isolated_client, tenant, gate):
    """M1. The per-write relock is a heartbeat: after a write every row the
    run holds has a fresh expiry, even ones that had already expired and
    ones outside the written location's own set."""
    seen: list[str] = []
    after_first_write: list[ScheduleLock] = []

    async def expire_then_snapshot(location_id: str) -> None:
        seen.append(location_id)
        if len(seen) == 1:
            async with get_test_session_factory()() as s:
                await s.execute(update(ScheduleLock).where(
                    ScheduleLock.company_id == tenant.company_id,
                ).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
                await s.commit()
        else:
            after_first_write.extend(await _lock_rows(tenant.company_id))

    gate.before_yield = expire_then_snapshot
    gate.go.set()
    resp = await _generate(isolated_client, tenant.admin_token, tenant.a, tenant.c,
                           tenant=tenant)
    assert resp.status_code == 200, resp.text
    assert [line["status"] for line in _lines(resp.text)] == ["ok", "ok"]
    assert {r.location_id for r in after_first_write} == {tenant.a, tenant.b, tenant.c}
    now = datetime.now(timezone.utc)
    assert all(r.expires_at > now for r in after_first_write), [
        (r.location_id, r.expires_at) for r in after_first_write]
    assert await _lock_rows(tenant.company_id) == []


async def test_a_draft_is_never_approved_twice(
    isolated_client, db_session, tenant, monkeypatch,
):
    """M7. The second approve read `draft` before the first took the lock,
    then waited until the first had approved and released. Its status
    check must be redone under the lock: 400, and the shifts are not
    materialised a second time."""
    from backend.routers import schedules as schedules_router

    sid = _id()
    db_session.add(ShiftSchedule(
        id=sid, company_id=tenant.company_id, location_id=tenant.c,
        week_start_date=date.fromisoformat(WEEK), status="draft",
        raw_llm_output="[]", created_at=datetime.now(timezone.utc),
    ))
    await db_session.commit()

    real_lock = schedules_router._lock_locations
    late_is_waiting, first_done = asyncio.Event(), asyncio.Event()
    calls = 0

    async def lock_after_first(*args: Any, **kwargs: Any):
        nonlocal calls
        calls += 1
        if calls == 1:  # the late request: read status, now held back
            late_is_waiting.set()
            await asyncio.wait_for(first_done.wait(), 10)
        return await real_lock(*args, **kwargs)

    monkeypatch.setattr(schedules_router, "_lock_locations", lock_after_first)
    url = f"/api/v1/schedules/{sid}/approve"
    late = asyncio.create_task(isolated_client.post(url, headers=_h(tenant.admin_token)))
    await asyncio.wait_for(late_is_waiting.wait(), 10)
    first = await isolated_client.post(url, headers=_h(tenant.admin_token))
    first_done.set()
    second = await late
    assert first.status_code == 200, first.text
    assert second.status_code == 400, second.text
    assert second.json()["detail"] == "Schedule already approved"
    assert await _lock_rows(tenant.company_id) == []
