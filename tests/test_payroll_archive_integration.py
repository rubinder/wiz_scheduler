"""Paid payroll archive (#218): offer, purchase, day-90 write, retrieval."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

import asyncio
import csv
import io
import sys
import zipfile
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import BinaryIO

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import (
    Company, Employee, Location, Region, Role, Shift, ShiftSchedule, TimeEntry, User,
)
from backend.models.ownership_group import OwnershipGroup
from backend.models.payroll_archive import PayrollArchive, PayrollArchiveToken
from backend.services import archive_storage
from backend.services.payroll_archive import hash_token, record_archive_purchase
from tests.conftest import _id, _make_token, get_test_session_factory

TODAY = datetime.now(timezone.utc).date()
STATUS_URL = "/api/v1/billing/payroll-archive"
CHECKOUT_URL = "/api/v1/billing/payroll-archive/checkout"
CONFIRM_URL = "/api/v1/billing/payroll-archive/confirm"
RETRIEVE_URL = "/api/v1/payroll-archive/retrieve"
DOWNLOAD_URL = "/api/v1/payroll-archive/download"


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail: Exception | None = None
        self.puts = 0

    async def put(self, key: str, fileobj: BinaryIO, size: int) -> None:
        self.puts += 1
        if self.fail is not None:
            raise self.fail
        data = fileobj.read()
        assert len(data) == size
        self.objects[key] = data

    def presign_get(self, key: str, expires_seconds: int, download_filename: str) -> str:
        return f"https://archive.example/{key}?expires={expires_seconds}&name={download_filename}"


@pytest.fixture(autouse=True)
def storage():
    fake = FakeStorage()
    archive_storage.set_archive_storage(fake)
    yield fake
    archive_storage.set_archive_storage(None)


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "PAYROLL_ARCHIVE_ENABLED", True)
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_x")


@pytest.fixture
def alerts(monkeypatch):
    """Capture operator alerts (both kinds) without Resend."""
    sent: list[tuple] = []
    monkeypatch.setattr(
        "backend.services.operator_alerts.send_payroll_archive_write_failed_alert",
        lambda og_id, archive_id, error: sent.append(("write_failed", og_id, archive_id, error)),
    )
    monkeypatch.setattr(
        "backend.services.operator_alerts.send_payroll_archive_refund_alert",
        lambda og_id, session_id, pi, reason, refunded: sent.append(
            ("refund", og_id, session_id, pi, reason, refunded)),
    )
    return sent


@pytest.fixture
def refunds(monkeypatch):
    import stripe

    made: list[dict] = []
    monkeypatch.setattr(stripe.Refund, "create", lambda **kw: made.append(kw))
    return made


@pytest.fixture
def quiet_lifecycle(monkeypatch):
    """The day-76/day-90 notices are not what these tests are about."""
    async def _noop(og):
        return None
    monkeypatch.setattr("backend.services.billing.send_deletion_reminder_email", _noop)
    monkeypatch.setattr("backend.services.billing.send_data_deleted_email", _noop)


@pytest.fixture
def resend_outbox(monkeypatch):
    sent: list[dict] = []

    class FakeEmails:
        @staticmethod
        def send(payload):
            sent.append(payload)

    class FakeResend:
        api_key = None
        Emails = FakeEmails()

    monkeypatch.setitem(sys.modules, "resend", FakeResend)
    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test")
    monkeypatch.setattr(settings, "FROM_EMAIL", "noreply@wiz.test")
    return sent


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------


async def _company(db: AsyncSession, og_id: str, name: str) -> SimpleNamespace:
    company_id, region_id, location_id, role_id = _id(), _id(), _id(), _id()
    db.add(Company(id=company_id, name=name, slug=f"slug-{company_id}",
                   ownership_group_id=og_id))
    await db.flush()
    db.add(Region(id=region_id, company_id=company_id, name="R"))
    await db.flush()
    db.add(Location(id=location_id, company_id=company_id, region_id=region_id,
                    name="Flatbush Ave", timezone="America/New_York"))
    db.add(Role(id=role_id, company_id=company_id, name="Role A"))
    await db.flush()
    return SimpleNamespace(company_id=company_id, location_id=location_id, role_id=role_id)


async def _group(
    db: AsyncSession, *, canceled_days_ago: int | None = 10, companies: int = 1
) -> SimpleNamespace:
    og_id = _id()
    db.add(OwnershipGroup(
        id=og_id, name="Group", stripe_customer_id=f"cus_{og_id}",
        stripe_subscription_id="sub_x",
        canceled_at=(datetime.now(timezone.utc) - timedelta(days=canceled_days_ago))
        if canceled_days_ago is not None else None,
        notified_deletion_reminder_at=datetime.now(timezone.utc),
    ))
    await db.flush()
    cos = [await _company(db, og_id, f"Company {i}") for i in range(companies)]
    admin_id, unverified_admin_id, regular_id = _id(), _id(), _id()
    now = datetime.now(timezone.utc)
    db.add_all([
        User(id=admin_id, company_id=cos[0].company_id,
             email=f"Admin-{admin_id}@Example.com", hashed_password="x",
             full_name="Admin", user_role="manager", manager_type="admin",
             email_verified_at=now),
        User(id=unverified_admin_id, company_id=cos[0].company_id,
             email=f"unverified-{unverified_admin_id}@example.com", hashed_password="x",
             full_name="Unverified", user_role="manager"),
        User(id=regular_id, company_id=cos[0].company_id,
             email=f"regular-{regular_id}@example.com", hashed_password="x",
             full_name="Regular", user_role="manager", manager_type="regular",
             email_verified_at=now),
    ])
    await db.commit()
    return SimpleNamespace(
        og_id=og_id, customer=f"cus_{og_id}", companies=cos,
        admin_email=f"admin-{admin_id}@example.com",
        unverified_email=f"unverified-{unverified_admin_id}@example.com",
        headers={"Authorization":
                 f"Bearer {_make_token(admin_id, cos[0].company_id, 'manager')}"},
        regular_headers={"Authorization":
                         f"Bearer {_make_token(regular_id, cos[0].company_id, 'manager')}"},
    )


async def _entry(
    db: AsyncSession, co: SimpleNamespace, *, employee_name: str,
    archived: bool = False, erased: bool = False, pay_date: date | None = None,
) -> None:
    pay_date = pay_date or TODAY - timedelta(days=100)
    employee_id, schedule_id, shift_id = _id(), _id(), _id()
    now = datetime.now(timezone.utc)
    db.add(Employee(id=employee_id, company_id=co.company_id,
                    full_name="[erased]" if erased else employee_name,
                    archived_at=now if archived else None,
                    erased_at=now if erased else None))
    start = datetime.combine(pay_date, datetime.min.time(), tzinfo=timezone.utc).replace(hour=13)
    db.add(ShiftSchedule(id=schedule_id, company_id=co.company_id,
                         location_id=co.location_id, week_start_date=pay_date,
                         status="approved"))
    await db.flush()
    db.add(Shift(id=shift_id, company_id=co.company_id, shift_schedule_id=schedule_id,
                 location_id=co.location_id, employee_id=employee_id,
                 role_id=co.role_id, role_name="Role A", date=pay_date,
                 start_time=start, end_time=start + timedelta(hours=8)))
    await db.flush()
    db.add(TimeEntry(
        company_id=co.company_id, location_id=co.location_id, employee_id=employee_id,
        shift_id=shift_id, role_id=co.role_id, role_name="Role A",
        employee_name=employee_name, location_name="Flatbush Ave",
        location_timezone="America/New_York", pay_date=pay_date, start_time=start,
        end_time=start + timedelta(hours=8), paid_minutes=480, source="checked_in",
        checked_in_at=start, lateness_minutes=0, approved_at=now,
    ))
    await db.commit()


def _session(g: SimpleNamespace, **over) -> SimpleNamespace:
    fields = dict(
        id=f"cs_{_id()}", client_reference_id=g.og_id, customer=g.customer,
        payment_status="paid", payment_intent=f"pi_{_id()}",
        amount_total=1800, currency="usd",
        metadata={"kind": "payroll_archive", "ownership_group_id": g.og_id},
    )
    fields.update(over)
    return SimpleNamespace(**fields)


def _retrieve_returns(monkeypatch, session: SimpleNamespace) -> None:
    import stripe
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", lambda sid: session)


async def _buy(db: AsyncSession, g: SimpleNamespace) -> PayrollArchive:
    assert await record_archive_purchase(db, g.og_id, _session(g)) == "created"
    return (await db.execute(
        select(PayrollArchive).where(PayrollArchive.ownership_group_id == g.og_id)
    )).scalar_one()


async def _make_overdue(db: AsyncSession, og_id: str) -> None:
    og = await db.get(OwnershipGroup, og_id)
    og.canceled_at = datetime.now(timezone.utc) - timedelta(days=settings.SUBSCRIPTION_GRACE_DAYS + 1)
    await db.commit()


async def _archive(db: AsyncSession, og_id: str) -> PayrollArchive | None:
    db.expire_all()
    return (await db.execute(
        select(PayrollArchive).where(PayrollArchive.ownership_group_id == og_id)
    )).scalar_one_or_none()


async def _og_exists(db: AsyncSession, og_id: str) -> bool:
    db.expire_all()
    return (await db.execute(
        select(OwnershipGroup.id).where(OwnershipGroup.id == og_id)
    )).scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# Offer and gating
# ---------------------------------------------------------------------------


async def test_status_is_unavailable_while_the_flag_is_off(client: AsyncClient, db_session):
    g = await _group(db_session)
    resp = await client.get(STATUS_URL, headers=g.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is False and body["purchased"] is False
    assert body["price_usd"] == 18.0
    assert body["scheduled_deletion_at"] is not None


async def test_status_is_available_only_to_a_cancelled_group_in_grace(
    client: AsyncClient, db_session, enabled
):
    in_grace = await _group(db_session)
    active = await _group(db_session, canceled_days_ago=None)
    overdue = await _group(db_session, canceled_days_ago=settings.SUBSCRIPTION_GRACE_DAYS + 1)

    assert (await client.get(STATUS_URL, headers=in_grace.headers)).json()["available"] is True
    assert (await client.get(STATUS_URL, headers=active.headers)).json() == {
        "available": False, "purchased": False, "price_usd": 18.0,
        "scheduled_deletion_at": None,
    }
    assert (await client.get(STATUS_URL, headers=overdue.headers)).json()["available"] is False


async def test_status_and_checkout_are_admin_only(client: AsyncClient, db_session, enabled):
    g = await _group(db_session)
    assert (await client.get(STATUS_URL, headers=g.regular_headers)).status_code == 403
    assert (await client.post(CHECKOUT_URL, headers=g.regular_headers)).status_code == 403


async def test_checkout_is_404_while_the_flag_is_off(client: AsyncClient, db_session, monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_x")
    g = await _group(db_session)
    assert (await client.post(CHECKOUT_URL, headers=g.headers)).status_code == 404


async def test_checkout_is_403_for_an_active_group(client: AsyncClient, db_session, enabled):
    g = await _group(db_session, canceled_days_ago=None)
    assert (await client.post(CHECKOUT_URL, headers=g.headers)).status_code == 403


async def test_checkout_creates_a_one_off_payment_session(
    client: AsyncClient, db_session, enabled, monkeypatch
):
    import stripe

    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id="cs_new", url="https://checkout.stripe.test/cs_new")

    monkeypatch.setattr(stripe.checkout.Session, "create", fake_create)
    g = await _group(db_session)

    resp = await client.post(CHECKOUT_URL, headers=g.headers)

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"session_id": "cs_new", "url": "https://checkout.stripe.test/cs_new"}
    assert captured["mode"] == "payment"
    assert captured["customer"] == g.customer
    assert captured["client_reference_id"] == g.og_id
    assert captured["metadata"] == {"kind": "payroll_archive", "ownership_group_id": g.og_id}
    assert captured["line_items"] == [{
        "price_data": {
            "currency": "usd", "unit_amount": 1800,
            "product_data": {"name": "Payroll archive (7 years)"},
        },
        "quantity": 1,
    }]
    assert "/manager/payroll" in captured["success_url"]
    assert "payroll_archive_session_id={CHECKOUT_SESSION_ID}" in captured["success_url"]
    assert "/manager/payroll" in captured["cancel_url"]


async def test_checkout_is_409_once_purchased(client: AsyncClient, db_session, enabled):
    g = await _group(db_session)
    await _buy(db_session, g)
    assert (await client.post(CHECKOUT_URL, headers=g.headers)).status_code == 409


# ---------------------------------------------------------------------------
# Confirm, webhook, idempotency
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("override", [
    {"client_reference_id": "someone"},
    {"customer": "cus_other"},
    {"payment_status": "unpaid"},
    {"metadata": {"kind": "subscription"}},
])
async def test_confirm_rejects_a_session_that_is_not_this_groups_paid_archive(
    client: AsyncClient, db_session, enabled, monkeypatch, override
):
    g = await _group(db_session)
    _retrieve_returns(monkeypatch, _session(g, **override))

    resp = await client.post(CONFIRM_URL, json={"session_id": "cs_x"}, headers=g.headers)

    assert resp.status_code == 400, resp.text
    assert await _archive(db_session, g.og_id) is None


async def test_confirm_records_the_purchase_with_verified_admin_recipients(
    client: AsyncClient, db_session, enabled, monkeypatch
):
    g = await _group(db_session, companies=2)
    s = _session(g)
    _retrieve_returns(monkeypatch, s)

    resp = await client.post(CONFIRM_URL, json={"session_id": s.id}, headers=g.headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()["purchased"] is True and resp.json()["available"] is False
    a = await _archive(db_session, g.og_id)
    assert a.status == "paid"
    assert a.stripe_checkout_session_id == s.id
    assert a.stripe_payment_intent_id == s.payment_intent
    assert (a.amount_cents, a.currency) == (1800, "usd")
    # Verified admins only, lowercase; the unverified admin and the regular
    # manager are not recipients.
    assert a.recipient_emails == [g.admin_email]
    assert sorted(a.company_names) == ["Company 0", "Company 1"]


async def test_recipients_fall_back_to_every_admin_when_none_is_verified(db_session):
    g = await _group(db_session)
    for u in (await db_session.execute(select(User))).scalars():
        u.email_verified_at = None
    await db_session.commit()
    a = await _buy(db_session, g)
    assert sorted(a.recipient_emails) == sorted([g.admin_email, g.unverified_email])


async def test_confirm_then_webhook_store_one_row(
    client: AsyncClient, db_session, enabled, monkeypatch
):
    import stripe

    g = await _group(db_session)
    s = _session(g)
    _retrieve_returns(monkeypatch, s)
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test")
    event = {"type": "checkout.session.completed", "data": {"object": dict(vars(s))}}
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda p, sig, k: event)

    first = await client.post(CONFIRM_URL, json={"session_id": s.id}, headers=g.headers)
    hook = await client.post("/api/v1/webhooks/stripe", content=b"{}",
                             headers={"stripe-signature": "t=1,v1=x"})
    again = await client.post(CONFIRM_URL, json={"session_id": s.id}, headers=g.headers)

    assert first.status_code == 200 and again.status_code == 200
    assert hook.status_code == 200 and hook.json()["payroll_archive"] == "existing"
    rows = (await db_session.execute(select(PayrollArchive))).scalars().all()
    assert len(rows) == 1


async def test_concurrent_records_of_one_session_store_one_row(db_session):
    g = await _group(db_session)
    s = _session(g)
    factory = get_test_session_factory()

    async def record():
        async with factory() as db:
            return await record_archive_purchase(db, g.og_id, s)

    outcomes = await asyncio.gather(record(), record(), record())

    assert sorted(outcomes) == ["created", "existing", "existing"]
    assert len((await db_session.execute(select(PayrollArchive))).scalars().all()) == 1


async def test_a_second_paid_session_is_refunded_not_stored(
    client: AsyncClient, db_session, enabled, monkeypatch, refunds, alerts
):
    g = await _group(db_session)
    first = await _buy(db_session, g)
    dup = _session(g)
    _retrieve_returns(monkeypatch, dup)

    resp = await client.post(CONFIRM_URL, json={"session_id": dup.id}, headers=g.headers)

    assert resp.status_code == 409
    assert refunds == [{"payment_intent": dup.payment_intent,
                        "idempotency_key": f"payroll-archive-refund-{dup.id}"}]
    assert alerts == [("refund", g.og_id, dup.id, dup.payment_intent, "duplicate", True)]
    rows = (await db_session.execute(select(PayrollArchive))).scalars().all()
    assert [r.id for r in rows] == [first.id]


async def test_a_payment_landing_after_deletion_is_refunded(
    client: AsyncClient, db_session, monkeypatch, refunds, alerts
):
    import stripe

    g = await _group(db_session)
    s = _session(g)
    og = await db_session.get(OwnershipGroup, g.og_id)
    from backend.services.billing import delete_og_and_dependents
    await delete_og_and_dependents(db_session, og)
    await db_session.commit()

    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test")
    event = {"type": "checkout.session.completed", "data": {"object": dict(vars(s))}}
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda p, sig, k: event)

    hook = await client.post("/api/v1/webhooks/stripe", content=b"{}",
                             headers={"stripe-signature": "t=1,v1=x"})

    assert hook.json()["payroll_archive"] == "refunded"
    assert refunds and refunds[0]["payment_intent"] == s.payment_intent
    assert alerts[0][4] == "group_deleted"
    assert (await db_session.execute(select(PayrollArchive))).scalars().all() == []


async def test_webhook_ignores_other_checkout_sessions(
    client: AsyncClient, db_session, monkeypatch
):
    import stripe

    g = await _group(db_session)
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test")
    event = {"type": "checkout.session.completed", "data": {"object": {
        "id": "cs_sub", "customer": g.customer, "payment_status": "paid",
        "mode": "subscription", "metadata": {},
    }}}
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda p, sig, k: event)

    hook = await client.post("/api/v1/webhooks/stripe", content=b"{}",
                             headers={"stripe-signature": "t=1,v1=x"})

    assert hook.status_code == 200
    assert "payroll_archive" not in hook.json()
    assert (await db_session.execute(select(PayrollArchive))).scalars().all() == []


# ---------------------------------------------------------------------------
# The day-90 write
# ---------------------------------------------------------------------------


async def test_lifecycle_writes_the_zip_before_deleting_the_group(
    db_session, storage, quiet_lifecycle, resend_outbox
):
    from backend.services.billing import process_cancellation_lifecycle

    g = await _group(db_session, companies=2)
    co_a, co_b = g.companies
    await _entry(db_session, co_a, employee_name="Dana Okafor")
    await _entry(db_session, co_a, employee_name="Archie Archived", archived=True)
    await _entry(db_session, co_b, employee_name="Erin Erased", erased=True)
    archive = await _buy(db_session, g)
    await _make_overdue(db_session, g.og_id)

    result = await process_cancellation_lifecycle(db_session)

    assert result["deletions_done"] == 1 and result["failures"] == 0
    assert not await _og_exists(db_session, g.og_id)
    a = await _archive(db_session, g.og_id)  # survives the group's deletion
    assert a is not None and a.id == archive.id
    assert a.status == "written"
    assert a.s3_key == f"archives/{a.id}.zip"
    assert a.row_count == 3
    assert a.size_bytes == len(storage.objects[a.s3_key])
    assert abs((a.expires_at - a.written_at) - timedelta(days=2557)) < timedelta(seconds=1)

    with zipfile.ZipFile(io.BytesIO(storage.objects[a.s3_key])) as zf:
        names = sorted(zf.namelist())
        assert names == sorted([
            f"payroll-history-company-0-{co_a.company_id}.csv",
            f"payroll-history-company-1-{co_b.company_id}.csv",
        ])
        assert all(i.compress_type == zipfile.ZIP_DEFLATED for i in zf.infolist())
        files = {n: zf.read(n).decode("utf-8") for n in names}
    a_csv = files[f"payroll-history-company-0-{co_a.company_id}.csv"]
    b_csv = files[f"payroll-history-company-1-{co_b.company_id}.csv"]
    assert a_csv.startswith("﻿")
    rows_a = list(csv.reader(io.StringIO(a_csv.lstrip("﻿"))))
    assert rows_a[0][-1] == "approved"
    assert sorted(r[1] for r in rows_a[1:]) == ["Archie Archived", "Dana Okafor"]
    assert "Erin Erased" in b_csv and "Dana Okafor" not in b_csv

    # The recipients are told where it is.
    assert [m["to"] for m in resend_outbox] == [[g.admin_email]]
    assert "/payroll-archive" in resend_outbox[0]["html"]
    assert a.expires_at.date().isoformat() in resend_outbox[0]["html"]

    # Tokens and archives are not swept with the group.
    assert (await db_session.execute(select(PayrollArchiveToken))).scalars().all() == []


async def test_recipients_are_refreshed_to_the_current_admins_at_write(
    db_session, storage, quiet_lifecycle
):
    """An admin removed after purchase loses access; one added gains it."""
    from backend.services.billing import process_cancellation_lifecycle

    g = await _group(db_session)
    a = await _buy(db_session, g)
    assert a.recipient_emails == [g.admin_email]
    old_admin = (await db_session.execute(
        select(User).where(User.email.ilike(g.admin_email))
    )).scalar_one()
    old_admin.manager_type = "regular"  # demoted during the grace period
    new_id = _id()
    db_session.add(User(
        id=new_id, company_id=g.companies[0].company_id,
        email=f"New-{new_id}@example.com", hashed_password="x", full_name="New",
        user_role="manager", manager_type="admin",
        email_verified_at=datetime.now(timezone.utc),
    ))
    await db_session.commit()
    await _make_overdue(db_session, g.og_id)

    await process_cancellation_lifecycle(db_session)

    after = await _archive(db_session, g.og_id)
    assert after.status == "written"
    assert after.recipient_emails == [f"new-{new_id}@example.com"]


async def test_an_upload_failure_postpones_the_deletion_and_retries(
    db_session, storage, quiet_lifecycle, alerts
):
    from backend.services.billing import process_cancellation_lifecycle

    g = await _group(db_session)
    other = await _group(db_session)  # unpaid, also overdue: still deleted
    await _entry(db_session, g.companies[0], employee_name="Dana Okafor")
    archive = await _buy(db_session, g)
    await _make_overdue(db_session, g.og_id)
    await _make_overdue(db_session, other.og_id)
    storage.fail = RuntimeError("S3 is down")

    result = await process_cancellation_lifecycle(db_session)

    assert result["failures"] == 1 and result["deletions_done"] == 1
    assert await _og_exists(db_session, g.og_id)
    assert not await _og_exists(db_session, other.og_id)
    assert (await _archive(db_session, g.og_id)).status == "paid"
    assert storage.objects == {}
    assert alerts == [("write_failed", g.og_id, archive.id, "RuntimeError: S3 is down")]

    storage.fail = None
    again = await process_cancellation_lifecycle(db_session)

    assert again["deletions_done"] == 1 and again["failures"] == 0
    assert not await _og_exists(db_session, g.og_id)
    assert (await _archive(db_session, g.og_id)).status == "written"


async def test_no_bucket_configured_postpones_the_deletion(
    db_session, quiet_lifecycle, alerts, monkeypatch
):
    from backend.services.billing import process_cancellation_lifecycle

    archive_storage.set_archive_storage(None)
    monkeypatch.setattr(settings, "PAYROLL_ARCHIVE_BUCKET", "")
    g = await _group(db_session)
    await _buy(db_session, g)
    await _make_overdue(db_session, g.og_id)

    result = await process_cancellation_lifecycle(db_session)

    assert result["failures"] == 1
    assert await _og_exists(db_session, g.og_id)
    assert alerts and alerts[0][0] == "write_failed"


async def test_an_already_written_archive_is_not_rewritten(
    db_session, storage, quiet_lifecycle
):
    from backend.services.billing import process_cancellation_lifecycle

    g = await _group(db_session)
    a = await _buy(db_session, g)
    written_at = datetime.now(timezone.utc) - timedelta(days=1)
    a.status, a.s3_key, a.written_at = "written", f"archives/{a.id}.zip", written_at
    a.expires_at = written_at + timedelta(days=2557)
    await db_session.commit()
    await _make_overdue(db_session, g.og_id)

    result = await process_cancellation_lifecycle(db_session)

    assert result["deletions_done"] == 1
    assert storage.puts == 0
    after = await _archive(db_session, g.og_id)
    assert after.written_at == written_at


async def test_a_second_worker_does_not_write_twice(db_session, storage, quiet_lifecycle):
    """process_cancellation_lifecycle runs once per uvicorn worker."""
    from backend.services.billing import process_cancellation_lifecycle

    g = await _group(db_session)
    await _buy(db_session, g)
    await _make_overdue(db_session, g.og_id)
    factory = get_test_session_factory()

    async def worker():
        async with factory() as db:
            return await process_cancellation_lifecycle(db)

    results = await asyncio.gather(worker(), worker())

    assert sum(r["deletions_done"] for r in results) == 1
    assert sum(r["failures"] for r in results) == 0
    assert storage.puts == 1


async def test_reactivation_keeps_the_purchase_for_a_later_deletion(
    db_session, storage, quiet_lifecycle
):
    from backend.services.billing import process_cancellation_lifecycle

    g = await _group(db_session)
    await _buy(db_session, g)
    og = await db_session.get(OwnershipGroup, g.og_id)
    og.canceled_at = None  # reactivated
    await db_session.commit()

    await process_cancellation_lifecycle(db_session)
    assert (await _archive(db_session, g.og_id)).status == "paid"
    assert storage.puts == 0

    # Cancelled again, and this time the grace period runs out.
    await _make_overdue(db_session, g.og_id)
    result = await process_cancellation_lifecycle(db_session)

    assert result["deletions_done"] == 1
    assert (await _archive(db_session, g.og_id)).status == "written"
    assert storage.puts == 1


async def test_a_paid_archive_is_written_with_the_flag_off(
    db_session, storage, quiet_lifecycle, monkeypatch
):
    from backend.services.billing import process_cancellation_lifecycle

    monkeypatch.setattr(settings, "PAYROLL_ARCHIVE_ENABLED", False)
    g = await _group(db_session)
    await _buy(db_session, g)
    await _make_overdue(db_session, g.og_id)

    await process_cancellation_lifecycle(db_session)

    assert not await _og_exists(db_session, g.og_id)
    assert (await _archive(db_session, g.og_id)).status == "written"


async def test_a_group_without_a_purchase_is_deleted_without_an_archive(
    db_session, storage, quiet_lifecycle
):
    from backend.services.billing import process_cancellation_lifecycle

    g = await _group(db_session)
    await _make_overdue(db_session, g.og_id)

    await process_cancellation_lifecycle(db_session)

    assert not await _og_exists(db_session, g.og_id)
    assert storage.puts == 0


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


async def _written(db: AsyncSession, storage: FakeStorage) -> tuple[SimpleNamespace, PayrollArchive]:
    from backend.services.billing import process_cancellation_lifecycle

    g = await _group(db)
    await _entry(db, g.companies[0], employee_name="Dana Okafor")
    await _buy(db, g)
    await _make_overdue(db, g.og_id)
    await process_cancellation_lifecycle(db)
    a = await _archive(db, g.og_id)
    assert a.status == "written"
    return g, a


@pytest.fixture
def retrieval_mail(monkeypatch):
    """The background task opens its own session, as in production."""
    import backend.database as _db_module

    monkeypatch.setattr(_db_module, "async_session_factory", get_test_session_factory())
    sent: list[tuple[str, list]] = []
    monkeypatch.setattr(
        "backend.services.payroll_archive.send_retrieval_email",
        lambda email, links: sent.append((email, links)),
    )
    return sent


async def test_retrieve_does_no_match_dependent_work_in_the_request(
    client: AsyncClient, db_session, storage, quiet_lifecycle, monkeypatch
):
    """Timing oracle: the handler schedules the same background call for a
    hit and a miss and touches no archive or token itself."""
    g, _ = await _written(db_session, storage)
    scheduled: list[tuple] = []

    async def record(email, base_url):
        scheduled.append((email, base_url))

    monkeypatch.setattr("backend.routers.payroll_archive.process_retrieval_request", record)

    hit = await client.post(RETRIEVE_URL, json={"email": g.admin_email})
    miss = await client.post(RETRIEVE_URL, json={"email": "nobody@example.com"})

    assert hit.status_code == miss.status_code == 202
    assert hit.json() == miss.json()
    assert [b for _, b in scheduled] == [scheduled[0][1]] * 2
    assert [e for e, _ in scheduled] == [g.admin_email, "nobody@example.com"]
    assert (await db_session.execute(select(PayrollArchiveToken))).scalars().all() == []


async def test_retrieve_answers_the_same_for_a_match_and_a_miss(
    client: AsyncClient, db_session, storage, quiet_lifecycle, retrieval_mail
):
    g, a = await _written(db_session, storage)

    miss = await client.post(RETRIEVE_URL, json={"email": "nobody@example.com"})
    hit = await client.post(RETRIEVE_URL, json={"email": g.admin_email.upper()})
    junk = await client.post(RETRIEVE_URL, json={"email": "not an email"})

    assert miss.status_code == hit.status_code == junk.status_code == 202
    assert miss.json() == hit.json() == junk.json()
    assert len(retrieval_mail) == 1
    email, links = retrieval_mail[0]
    assert email == g.admin_email
    assert links[0][0] == ["Company 0"]
    assert "/api/v1/payroll-archive/download?token=" in links[0][1]
    tokens = (await db_session.execute(select(PayrollArchiveToken))).scalars().all()
    assert len(tokens) == 1
    raw = links[0][1].split("token=")[1]
    assert tokens[0].token_hash == hash_token(raw) and raw not in tokens[0].token_hash
    assert tokens[0].archive_id == a.id
    assert tokens[0].expires_at - tokens[0].created_at == timedelta(hours=24)


async def test_a_non_recipient_and_an_expired_archive_get_nothing(
    client: AsyncClient, db_session, storage, quiet_lifecycle, retrieval_mail
):
    g, a = await _written(db_session, storage)
    # The unverified admin was not a recipient (a verified admin existed).
    await client.post(RETRIEVE_URL, json={"email": g.unverified_email})
    a.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    await db_session.commit()
    await client.post(RETRIEVE_URL, json={"email": g.admin_email})
    assert retrieval_mail == []


async def test_retrieve_is_capped_per_email_silently_and_per_ip_with_429(
    client: AsyncClient, db_session, storage, quiet_lifecycle, retrieval_mail
):
    g, _ = await _written(db_session, storage)
    responses = [
        await client.post(RETRIEVE_URL, json={"email": g.admin_email},
                          headers={"x-forwarded-for": f"10.0.0.{i}"})
        for i in range(5)
    ]
    assert all(r.status_code == 202 for r in responses)
    assert len(retrieval_mail) == settings.PAYROLL_ARCHIVE_RETRIEVE_PER_EMAIL_PER_HOUR

    ip = {"x-forwarded-for": "10.9.9.9"}
    for _ in range(settings.PAYROLL_ARCHIVE_RETRIEVE_RATE_LIMIT_PER_HOUR):
        assert (await client.post(RETRIEVE_URL, json={"email": "x@example.com"},
                                  headers=ip)).status_code == 202
    assert (await client.post(RETRIEVE_URL, json={"email": "x@example.com"},
                              headers=ip)).status_code == 429


async def test_download_redirects_once_to_a_presigned_url(
    client: AsyncClient, db_session, storage, quiet_lifecycle, retrieval_mail
):
    g, a = await _written(db_session, storage)
    await client.post(RETRIEVE_URL, json={"email": g.admin_email})
    raw = retrieval_mail[0][1][0][1].split("token=")[1]

    first = await client.get(DOWNLOAD_URL, params={"token": raw})
    second = await client.get(DOWNLOAD_URL, params={"token": raw})

    assert first.status_code == 302
    assert first.headers["location"] == (
        f"https://archive.example/{a.s3_key}?expires=300"
        f"&name=payroll-archive-{a.written_at.date().isoformat()}.zip"
    )
    assert second.status_code == 410


async def test_download_refuses_expired_unknown_and_missing_tokens_alike(
    client: AsyncClient, db_session, storage, quiet_lifecycle, retrieval_mail
):
    g, a = await _written(db_session, storage)
    await client.post(RETRIEVE_URL, json={"email": g.admin_email})
    raw = retrieval_mail[0][1][0][1].split("token=")[1]
    token = (await db_session.execute(select(PayrollArchiveToken))).scalar_one()
    token.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db_session.commit()

    expired = await client.get(DOWNLOAD_URL, params={"token": raw})
    unknown = await client.get(DOWNLOAD_URL, params={"token": "nope"})
    missing = await client.get(DOWNLOAD_URL)

    assert expired.status_code == unknown.status_code == missing.status_code == 410
    assert expired.text == unknown.text == missing.text


# ---------------------------------------------------------------------------
# The S3 implementation (no network: a stand-in client, and local signing)
# ---------------------------------------------------------------------------


async def test_s3_put_uses_glacier_ir_sse_and_zip_content_type():
    calls: list[dict] = []

    class Client:
        def put_object(self, **kw):
            kw["Body"] = kw["Body"].read()
            calls.append(kw)

    s3 = archive_storage.S3ArchiveStorage("bucket-x")
    s3._client_obj = Client()
    await s3.put("archives/abc.zip", io.BytesIO(b"zipbytes"), 8)

    assert calls == [{
        "Bucket": "bucket-x", "Key": "archives/abc.zip", "Body": b"zipbytes",
        "ContentLength": 8, "ContentType": "application/zip",
        "StorageClass": "GLACIER_IR", "ServerSideEncryption": "AES256",
    }]


async def test_s3_presign_is_local_and_names_the_download(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    url = archive_storage.S3ArchiveStorage("bucket-x").presign_get(
        "archives/abc.zip", 300, "payroll-archive-2026-10-31.zip")
    assert "bucket-x" in url and "archives/abc.zip" in url
    assert "X-Amz-Expires=300" in url
    assert "payroll-archive-2026-10-31.zip" in url


async def test_no_bucket_means_no_storage(monkeypatch):
    archive_storage.set_archive_storage(None)
    monkeypatch.setattr(settings, "PAYROLL_ARCHIVE_BUCKET", "")
    with pytest.raises(archive_storage.ArchiveStorageNotConfigured):
        archive_storage.get_archive_storage()
