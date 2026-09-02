"""The weekly suspected-account report clusters, and only clusters.

The load-bearing tests here are the negative ones: a paying customer must
never appear in a report headed "might be up for deletion", and a lone
account must never be flagged for having an IP.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models.ownership_group import OwnershipGroup
from backend.services.abuse_report import (
    build_suspected_accounts_report,
    normalize_company_name,
)
from tests.conftest import _id

pytestmark = pytest.mark.asyncio


async def _og(
    db: AsyncSession,
    *,
    name: str = "Acme",
    ip: str | None = "203.0.0.0",
    device: str | None = None,
    email: str | None = None,
    paid: bool = False,
    age_days: int = 1,
    og_id: str | None = None,
) -> str:
    oid = og_id or _id()
    db.add(OwnershipGroup(
        id=oid,
        name=name,
        stripe_subscription_id="sub_live" if paid else None,
        signup_ip_masked=ip,
        signup_device_id=device,
        signup_email_normalized=email,
        created_at=datetime.now(timezone.utc) - timedelta(days=age_days),
    ))
    await db.commit()
    return oid


def _signals(report) -> set[str]:
    return {c["signal"] for c in report["clusters"]}


# ---------------------------------------------------------------------------
# Name normalization
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Acme Co.", "acme"),
        ("ACME, Inc", "acme"),
        ("acme llc", "acme"),
        ("  Acme   Bakery  ", "acme bakery"),
        ("Acme Bakery Limited", "acme bakery"),
    ],
)
def test_company_names_normalize(raw, expected):
    """An operator minting accounts varies the legal suffix; the suffix
    distinguishes nothing, so it is dropped."""
    assert normalize_company_name(raw) == expected


def test_distinct_companies_stay_distinct():
    assert normalize_company_name("Acme") != normalize_company_name("Acme Bakery")


# ---------------------------------------------------------------------------
# What gets flagged
# ---------------------------------------------------------------------------

async def test_same_name_and_ip_is_flagged(db_session: AsyncSession):
    await _og(db_session, name="Acme Co.", ip="203.0.0.0")
    await _og(db_session, name="ACME Inc", ip="203.0.0.0")

    report = await build_suspected_accounts_report(db_session)
    assert "company_name_and_ip" in _signals(report)
    assert report["accounts_flagged"] == 2


async def test_same_name_different_ip_is_not_flagged(db_session: AsyncSession):
    await _og(db_session, name="Acme", ip="203.0.0.0")
    await _og(db_session, name="Acme", ip="198.51.0.0")

    report = await build_suspected_accounts_report(db_session)
    assert "company_name_and_ip" not in _signals(report)


async def test_same_ip_alone_is_not_flagged(db_session: AsyncSession):
    """mask_ip zeroes the last TWO bytes, so a shared value is a /16 — an
    ISP region. On its own it would flag half a city."""
    await _og(db_session, name="Acme", ip="203.0.0.0")
    await _og(db_session, name="Totally Different Bakery", ip="203.0.0.0")

    report = await build_suspected_accounts_report(db_session)
    assert report["clusters"] == []


async def test_shared_device_is_flagged(db_session: AsyncSession):
    await _og(db_session, name="One", ip=None, device="dev-1")
    await _og(db_session, name="Two", ip=None, device="dev-1")

    report = await build_suspected_accounts_report(db_session)
    assert "device_id" in _signals(report)


async def test_shared_normalized_email_is_flagged(db_session: AsyncSession):
    await _og(db_session, name="One", ip=None, email="farm@gmail.com")
    await _og(db_session, name="Two", ip=None, email="farm@gmail.com")

    report = await build_suspected_accounts_report(db_session)
    clusters = [c for c in report["clusters"] if c["signal"] == "email_normalized"]
    assert len(clusters) == 1
    assert clusters[0]["confidence"] == "medium"


async def test_a_single_account_is_never_a_cluster(db_session: AsyncSession):
    await _og(db_session, name="Acme", ip="203.0.0.0", device="dev-1",
              email="solo@example.com")

    report = await build_suspected_accounts_report(db_session)
    assert report["clusters"] == []
    assert report["accounts_flagged"] == 0


# ---------------------------------------------------------------------------
# What must NEVER be flagged
# ---------------------------------------------------------------------------

async def test_paying_customers_are_excluded(db_session: AsyncSession):
    """A customer is not a suspected account. This report is headed "might
    be up for deletion" — it must never point at someone's live data."""
    await _og(db_session, name="Acme", ip="203.0.0.0", paid=True)
    await _og(db_session, name="Acme", ip="203.0.0.0", paid=True)

    report = await build_suspected_accounts_report(db_session)
    assert report["clusters"] == []
    assert report["groups_examined"] == 0


async def test_a_canceled_subscription_is_examined_again(db_session: AsyncSession):
    """Paid-then-canceled is back on the free tier, so it is in scope."""
    ids = [_id(), _id()]
    for oid in ids:
        db_session.add(OwnershipGroup(
            id=oid, name="Acme", stripe_subscription_id="sub_old",
            canceled_at=datetime.now(timezone.utc),
            signup_ip_masked="203.0.0.0",
            created_at=datetime.now(timezone.utc),
        ))
    await db_session.commit()

    report = await build_suspected_accounts_report(db_session)
    assert report["groups_examined"] == 2


async def test_the_demo_group_is_excluded(db_session: AsyncSession, monkeypatch):
    demo_id = _id()
    monkeypatch.setattr(settings, "DEMO_OWNERSHIP_GROUP_ID", demo_id)
    await _og(db_session, name="Acme", ip="203.0.0.0", og_id=demo_id)
    await _og(db_session, name="Acme", ip="203.0.0.0")

    report = await build_suspected_accounts_report(db_session)
    assert report["clusters"] == []


async def test_groups_outside_the_window_are_excluded(db_session: AsyncSession):
    await _og(db_session, name="Acme", ip="203.0.0.0", age_days=200)
    await _og(db_session, name="Acme", ip="203.0.0.0", age_days=200)

    report = await build_suspected_accounts_report(db_session, window_days=90)
    assert report["clusters"] == []


async def test_groups_with_no_signals_are_examined_but_not_flagged(
    db_session: AsyncSession,
):
    """Signals are absent for API clients, for anyone with storage disabled,
    and for every account created before the columns existed."""
    await _og(db_session, name="Acme", ip=None)
    await _og(db_session, name="Acme", ip=None)

    report = await build_suspected_accounts_report(db_session)
    assert report["groups_examined"] == 2
    assert report["clusters"] == []


# ---------------------------------------------------------------------------
# The report reports
# ---------------------------------------------------------------------------

async def test_the_report_carries_its_own_caveat(db_session: AsyncSession):
    """The caveat rides in the payload, not just the docstring — the payload
    is what whoever acts on this actually reads."""
    report = await build_suspected_accounts_report(db_session)
    assert "Suspected only" in report["note"]
    assert "nothing is deleted automatically" in report["note"].lower()


async def test_the_report_deletes_nothing(db_session: AsyncSession):
    await _og(db_session, name="Acme", ip="203.0.0.0", og_id="keepme01")
    await _og(db_session, name="Acme", ip="203.0.0.0")

    await build_suspected_accounts_report(db_session)

    assert await db_session.get(OwnershipGroup, "keepme01") is not None
