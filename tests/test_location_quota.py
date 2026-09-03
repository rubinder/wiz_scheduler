"""Free plan: one week per location per calendar month, two attempts.

The rules under test:
  * a location may END the month holding 1 schedule;
  * it may RUN 2 generations getting there, so a bad first draft is
    recoverable;
  * rejecting a draft frees the slot but keeps the attempt spent;
  * the retry must target the SAME WEEK as the draft it replaces.
"""
from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import Company, Location, Region, ShiftSchedule
from backend.models.ownership_group import OwnershipGroup
from backend.services.location_quota import resolve_location_quota
from tests.conftest import _id

pytestmark = pytest.mark.asyncio

WEEK_A = "2026-09-07"
WEEK_B = "2026-09-14"


@pytest_asyncio.fixture
async def tenant(db_session: AsyncSession) -> dict:
    og_id, company_id, region_id = _id(), _id(), _id()
    loc_a, loc_b = _id(), _id()
    db_session.add(OwnershipGroup(id=og_id, name="G"))
    await db_session.flush()
    db_session.add(Company(id=company_id, name="C", slug=_id(),
                           ownership_group_id=og_id))
    await db_session.flush()
    db_session.add(Region(id=region_id, company_id=company_id, name="R"))
    await db_session.flush()
    for lid, name in ((loc_a, "A"), (loc_b, "B")):
        db_session.add(Location(id=lid, company_id=company_id,
                                region_id=region_id, name=name,
                                timezone="America/New_York"))
    await db_session.commit()
    return {"og_id": og_id, "company_id": company_id, "a": loc_a, "b": loc_b}


async def _schedule(
    db: AsyncSession, tenant: dict, location_id: str, *,
    week: str = WEEK_A, status: str = "draft", month_offset_days: int = 0,
) -> None:
    created = datetime.now(timezone.utc) - timedelta(days=month_offset_days)
    db.add(ShiftSchedule(
        id=_id(),
        company_id=tenant["company_id"],
        location_id=location_id,
        week_start_date=date.fromisoformat(week),
        status=status,
        created_at=created,
    ))
    await db.commit()


async def _quota(db: AsyncSession, tenant: dict, week: str | None = WEEK_A):
    return await resolve_location_quota(
        db, tenant["company_id"], [tenant["a"], tenant["b"]], week
    )


# ---------------------------------------------------------------------------
# The basic allowance
# ---------------------------------------------------------------------------

async def test_a_fresh_location_may_generate(db_session, tenant):
    q = await _quota(db_session, tenant)
    assert q[tenant["a"]]["allowed"] is True
    assert q[tenant["a"]]["attempts_used"] == 0


async def test_a_scheduled_location_is_blocked(db_session, tenant):
    await _schedule(db_session, tenant, tenant["a"], status="draft")

    q = await _quota(db_session, tenant)
    assert q[tenant["a"]]["allowed"] is False
    assert q[tenant["a"]]["code"] == "location_scheduled_this_month"


async def test_the_cap_is_per_location_not_pooled(db_session, tenant):
    """The whole point of the change: location A being spent must not spend
    location B. Under the old pooled counter it did."""
    await _schedule(db_session, tenant, tenant["a"], status="draft")

    q = await _quota(db_session, tenant)
    assert q[tenant["a"]]["allowed"] is False
    assert q[tenant["b"]]["allowed"] is True


async def test_an_approved_schedule_holds_the_slot(db_session, tenant):
    await _schedule(db_session, tenant, tenant["a"], status="approved")

    q = await _quota(db_session, tenant)
    assert q[tenant["a"]]["code"] == "location_scheduled_this_month"


async def test_last_months_schedule_does_not_count(db_session, tenant):
    """Monthly reset: the allowance is per calendar month."""
    await _schedule(db_session, tenant, tenant["a"], month_offset_days=45)

    q = await _quota(db_session, tenant)
    assert q[tenant["a"]]["allowed"] is True


# ---------------------------------------------------------------------------
# Rejection frees the slot; the attempt stays spent
# ---------------------------------------------------------------------------

async def test_rejecting_frees_the_slot(db_session, tenant):
    """Without this the free tier is one-shot, and a first run against a
    half-entered roster is unrecoverable for up to 30 days."""
    await _schedule(db_session, tenant, tenant["a"], status="rejected")

    q = await _quota(db_session, tenant)
    assert q[tenant["a"]]["allowed"] is True
    assert q[tenant["a"]]["attempts_used"] == 1


async def test_two_rejections_exhaust_the_attempts(db_session, tenant):
    for _ in range(settings.FREE_PLAN_ATTEMPTS_PER_LOCATION):
        await _schedule(db_session, tenant, tenant["a"], status="rejected")

    q = await _quota(db_session, tenant)
    assert q[tenant["a"]]["allowed"] is False
    assert q[tenant["a"]]["code"] == "location_retries_exhausted"


async def test_reject_then_keep_blocks_further_generation(db_session, tenant):
    """Rejected once, then produced a schedule they kept — slot is held and
    attempts are spent. Both reasons apply; the held slot is the one that
    matters to the user."""
    await _schedule(db_session, tenant, tenant["a"], status="rejected")
    await _schedule(db_session, tenant, tenant["a"], status="draft")

    q = await _quota(db_session, tenant)
    assert q[tenant["a"]]["allowed"] is False
    assert q[tenant["a"]]["code"] == "location_scheduled_this_month"


# ---------------------------------------------------------------------------
# The retry is pinned to the rejected week
# ---------------------------------------------------------------------------

async def test_the_retry_must_target_the_same_week(db_session, tenant):
    """The second attempt exists to redo a bad week, not to buy a second
    one — otherwise the allowance is quietly two weeks per location."""
    await _schedule(db_session, tenant, tenant["a"], week=WEEK_A, status="rejected")

    q = await _quota(db_session, tenant, week=WEEK_B)
    assert q[tenant["a"]]["allowed"] is False
    assert q[tenant["a"]]["code"] == "retry_week_mismatch"
    assert q[tenant["a"]]["required_week"] == WEEK_A


async def test_the_retry_is_allowed_for_the_same_week(db_session, tenant):
    await _schedule(db_session, tenant, tenant["a"], week=WEEK_A, status="rejected")

    q = await _quota(db_session, tenant, week=WEEK_A)
    assert q[tenant["a"]]["allowed"] is True
    assert q[tenant["a"]]["required_week"] == WEEK_A


async def test_the_required_week_is_reported_without_choosing_one(
    db_session, tenant
):
    """Passing no week asks "what is the state of this location?" — used by
    the banner, which has to say which week the retry is pinned to before
    the user has picked anything."""
    await _schedule(db_session, tenant, tenant["a"], week=WEEK_A, status="rejected")

    q = await _quota(db_session, tenant, week=None)
    assert q[tenant["a"]]["allowed"] is True
    assert q[tenant["a"]]["required_week"] == WEEK_A


async def test_the_week_pin_does_not_apply_to_a_fresh_location(
    db_session, tenant
):
    await _schedule(db_session, tenant, tenant["a"], week=WEEK_A, status="rejected")

    q = await _quota(db_session, tenant, week=WEEK_B)
    assert q[tenant["b"]]["allowed"] is True
    assert q[tenant["b"]]["required_week"] is None


# ---------------------------------------------------------------------------
# Who the allowance does not apply to
# ---------------------------------------------------------------------------

async def test_paid_groups_are_unlimited(db_session, tenant):
    og = await db_session.get(OwnershipGroup, tenant["og_id"])
    og.stripe_subscription_id = "sub_live"
    await db_session.commit()
    await _schedule(db_session, tenant, tenant["a"], status="approved")

    q = await _quota(db_session, tenant)
    assert q[tenant["a"]]["allowed"] is True


async def test_a_canceled_subscription_drops_back_to_the_free_rules(
    db_session, tenant
):
    og = await db_session.get(OwnershipGroup, tenant["og_id"])
    og.stripe_subscription_id = "sub_live"
    og.canceled_at = datetime.now(timezone.utc)
    await db_session.commit()
    await _schedule(db_session, tenant, tenant["a"], status="approved")

    q = await _quota(db_session, tenant)
    assert q[tenant["a"]]["allowed"] is False


async def test_the_shared_demo_is_exempt(db_session, tenant, monkeypatch):
    """Every visitor generates against the demo. A one-per-location cap
    would spend it within minutes of the month turning over."""
    monkeypatch.setattr(settings, "DEMO_OWNERSHIP_GROUP_ID", tenant["og_id"])
    await _schedule(db_session, tenant, tenant["a"], status="approved")

    q = await _quota(db_session, tenant)
    assert q[tenant["a"]]["allowed"] is True


async def test_no_locations_asked_about_returns_nothing(db_session, tenant):
    assert await resolve_location_quota(
        db_session, tenant["company_id"], [], WEEK_A
    ) == {}


# ---------------------------------------------------------------------------
# End to end, through the generate endpoint
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def seeded_free_group(db_session: AsyncSession, seed_company):
    """Attach an ownership group to the canonical test company.

    Without one, plan.py treats the tenant as UNLIMITED (the documented
    seed/dev convention), so the allowance never applies and an end-to-end
    test of it silently passes for the wrong reason.
    """
    og_id = _id()
    db_session.add(OwnershipGroup(id=og_id, name="Seeded Group"))
    await db_session.flush()
    seed_company.ownership_group_id = og_id
    await db_session.commit()
    return og_id


async def test_a_spent_location_is_reported_and_skipped_not_fatal(
    client, db_session: AsyncSession, seed_shift_template, manager_token,
    seeded_free_group,
):
    """A blocked location is streamed as QUOTA_EXCEEDED and skipped, the way
    PARSE_ERROR and CONFLICT are. Failing the whole run would deny a tenant
    the locations that still have allowance."""
    from backend.models import Location, ShiftTemplate
    from tests.conftest import COMPANY_ID, LOCATION_ID, REGION_ID

    # A second location, with its own template, so the run has something
    # left to generate once the first is spent.
    second = _id()
    db_session.add(Location(id=second, company_id=COMPANY_ID,
                            region_id=REGION_ID, name="Uptown Store",
                            timezone="America/New_York"))
    await db_session.flush()
    db_session.add(ShiftTemplate(
        id=_id(), company_id=COMPANY_ID, location_id=second,
        name="Weekday Standard", weekly_schedule=seed_shift_template.weekly_schedule,
    ))

    # The first location has already used its week.
    db_session.add(ShiftSchedule(
        id=_id(),
        company_id=COMPANY_ID,
        location_id=LOCATION_ID,
        week_start_date=date(2026, 6, 1),
        status="draft",
        created_at=datetime.now(timezone.utc),
    ))
    await db_session.commit()

    resp = await client.post(
        "/api/v1/schedules/generate",
        json={"week_start_date": "2026-06-08", "use_local": True},
        headers={"Authorization": f"Bearer {manager_token}"},
    )

    # 200 with a per-location verdict in the stream, not a 402 for the run:
    # the spent location is reported, the fresh one is still scheduled.
    assert resp.status_code == 200
    assert "QUOTA_EXCEEDED" in resp.text
    assert "Uptown Store" in resp.text


async def test_every_location_spent_is_a_single_402(
    client, db_session: AsyncSession, seed_shift_template, manager_token,
    seeded_free_group,
):
    """When nothing can be generated the caller gets one clear payment
    error rather than a stream of refusals."""
    from tests.conftest import COMPANY_ID, LOCATION_ID

    db_session.add(ShiftSchedule(
        id=_id(),
        company_id=COMPANY_ID,
        location_id=LOCATION_ID,
        week_start_date=date(2026, 6, 1),
        status="draft",
        created_at=datetime.now(timezone.utc),
    ))
    await db_session.commit()

    resp = await client.post(
        "/api/v1/schedules/generate",
        json={"week_start_date": "2026-06-08", "use_local": True},
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    # The seeded company has exactly one location, so one spent is all spent.
    assert resp.status_code == 402
    assert resp.json()["detail"]["code"] == "schedule_limit_reached"
