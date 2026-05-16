# Subscription Cancellation PR β — Deletion Cron + Lifecycle Emails

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a daily background loop that walks canceled OGs, sends a 14-day reminder email at day 76, and hard-deletes the OG + all dependents at day 90 (also emailing the deletion notice immediately before).

**Architecture:** A single daily `asyncio.create_task`-style loop in `backend/main.py` (modeled on `_daily_storage_snapshot_loop` lines 112–136). The loop opens a fresh `async_session_factory()` session and calls `process_cancellation_lifecycle(db)`. That orchestrator queries OGs with `canceled_at IS NOT NULL` and, per OG: sends the day-76 reminder once, then at day 90 sends the deletion-notice email and synchronously deletes the OG via an explicit deletion helper that walks the FK graph in dependency order. The notification flags on the OG row provide idempotency for the reminder; the OG row's own absence provides idempotency for deletion. No new tables, no new migration.

**Tech Stack:** Python 3.11 · FastAPI · SQLAlchemy 2.x async · asyncio · Resend · pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-05-14-subscription-cancellation-design.md` (PR β section, lines 234–239)

**Depends on PR α:** Migration 0022 columns (`canceled_at`, `notified_deletion_reminder_at`, `notified_data_deleted_at`), `SUBSCRIPTION_GRACE_DAYS = 90`, `SUBSCRIPTION_REMINDER_DAYS_BEFORE_DELETE = 14`, the `send_subscription_ended_email` helper (Task 6 of PR α) is the design pattern for the two new email helpers.

---

## Task 1: `send_deletion_reminder_email` helper (the 14-day warning)

**Files:**
- Modify: `backend/services/billing.py`
- Modify: `tests/test_billing.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_billing.py` (use the same pattern as `test_send_subscription_ended_email_calls_resend` from PR α):

```python
async def test_send_deletion_reminder_email_calls_resend(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    """Day-76 reminder includes the scheduled deletion date and reactivation CTA."""
    from contextlib import asynccontextmanager
    import backend.database as _db_module
    from backend.services.billing import send_deletion_reminder_email
    from backend.models import User

    og_with_card.canceled_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db_session.add(User(
        id=_id(),
        company_id=COMPANY_ID,
        email="manager@acme.test",
        hashed_password="$2b$12$dummy",
        full_name="Acme Manager",
        user_role="manager",
    ))
    await db_session.commit()

    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(settings, "FROM_EMAIL", "noreply@wiz.test")

    @asynccontextmanager
    async def _fake_session_factory():
        yield db_session
    monkeypatch.setattr(_db_module, "async_session_factory", _fake_session_factory)

    sent = []
    class FakeEmails:
        @staticmethod
        def send(payload):
            sent.append(payload)
    class FakeResend:
        api_key = None
        Emails = FakeEmails()
    monkeypatch.setitem(__import__("sys").modules, "resend", FakeResend)

    await send_deletion_reminder_email(og_with_card)

    assert len(sent) == 1
    body = sent[0]
    assert "manager@acme.test" in body["to"]
    assert "14" in body["subject"] or "14" in body["html"]  # 14-day warning
    assert "reactivate" in body["html"].lower()
    # Scheduled deletion date = 2026-01-01 + 90 days = 2026-04-01
    assert "2026-04-01" in body["html"] or "April" in body["html"]


async def test_send_deletion_reminder_email_noop_without_resend_key(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    from backend.services.billing import send_deletion_reminder_email
    og_with_card.canceled_at = datetime.now(timezone.utc)
    monkeypatch.setattr(settings, "RESEND_API_KEY", "")
    await send_deletion_reminder_email(og_with_card)  # must not raise


async def test_send_deletion_reminder_email_escapes_og_name(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    """XSS regression: og.name must be html-escaped."""
    from contextlib import asynccontextmanager
    import backend.database as _db_module
    from backend.services.billing import send_deletion_reminder_email
    from backend.models import User

    og_with_card.name = "<script>alert(1)</script>"
    og_with_card.canceled_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db_session.add(User(
        id=_id(),
        company_id=COMPANY_ID,
        email="manager@acme.test",
        hashed_password="$2b$12$dummy",
        full_name="Acme Manager",
        user_role="manager",
    ))
    await db_session.commit()

    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(settings, "FROM_EMAIL", "noreply@wiz.test")

    @asynccontextmanager
    async def _fake_session_factory():
        yield db_session
    monkeypatch.setattr(_db_module, "async_session_factory", _fake_session_factory)

    sent = []
    class FakeEmails:
        @staticmethod
        def send(payload):
            sent.append(payload)
    class FakeResend:
        api_key = None
        Emails = FakeEmails()
    monkeypatch.setitem(__import__("sys").modules, "resend", FakeResend)

    await send_deletion_reminder_email(og_with_card)

    assert len(sent) == 1
    assert "<script>alert(1)</script>" not in sent[0]["html"]
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in sent[0]["html"]
```

- [ ] **Step 2: Run, verify FAIL**

Run: `pytest tests/test_billing.py -k "send_deletion_reminder_email" --no-header -q`
Expected: 3 FAIL (function doesn't exist).

- [ ] **Step 3: Implement the helper**

Append to `backend/services/billing.py`:

```python
async def send_deletion_reminder_email(og: OwnershipGroup) -> None:
    """Day-76 reminder: 14 days until the OG's data is permanently deleted.

    Caller (the cron) is responsible for idempotency via
    notified_deletion_reminder_at. Resend errors are swallowed.
    """
    if not settings.RESEND_API_KEY:
        logger.info("Skipping deletion-reminder email for og=%s (RESEND_API_KEY unset)", og.id)
        return

    from sqlalchemy import select
    from backend.models import Company, User
    from backend.database import async_session_factory
    import html as _html
    from datetime import timedelta

    async with async_session_factory() as db:
        company_ids = (await db.execute(
            select(Company.id).where(Company.ownership_group_id == og.id)
        )).scalars().all()
        if not company_ids:
            return
        manager_emails = (await db.execute(
            select(User.email).where(
                User.company_id.in_(company_ids),
                User.user_role == "manager",
            )
        )).scalars().all()
        manager_emails = [e for e in manager_emails if e]

    if not manager_emails:
        logger.info("No manager emails for og=%s; deletion-reminder skipped", og.id)
        return

    if og.canceled_at is None:
        # Defensive: caller should have filtered, but don't send a reminder
        # to an active account.
        logger.warning("send_deletion_reminder_email called for og=%s with canceled_at=None", og.id)
        return

    deletion_date = (og.canceled_at + timedelta(days=settings.SUBSCRIPTION_GRACE_DAYS)).date()

    try:
        import resend
        resend.api_key = settings.RESEND_API_KEY
        resend.Emails.send({
            "from": settings.FROM_EMAIL,
            "to": list(manager_emails),
            "subject": f"Your WizScheduler data will be deleted in {settings.SUBSCRIPTION_REMINDER_DAYS_BEFORE_DELETE} days",
            "html": (
                f'<div style="font-family:sans-serif;max-width:600px;margin:0 auto;">'
                f"<p>Hi {_html.escape(og.name)},</p>"
                f"<p><strong>Your WizScheduler data is scheduled for permanent "
                f"deletion on {deletion_date.isoformat()}</strong> — "
                f"{settings.SUBSCRIPTION_REMINDER_DAYS_BEFORE_DELETE} days from today.</p>"
                f"<p>If you want to keep your data, log in and click "
                f"<em>Reactivate Subscription</em> before that date.</p>"
                f"<p>If you do nothing, all of your employees, schedules, "
                f"locations, and historical data will be permanently and "
                f"irreversibly deleted.</p>"
                f"</div>"
            ),
        })
    except Exception:
        logger.error(
            "Failed to send deletion-reminder email for og=%s",
            og.id,
            exc_info=True,
        )
```

- [ ] **Step 4: Run, verify PASS**

Run: `pytest tests/test_billing.py -k "send_deletion_reminder_email" --no-header -q`
Expected: 3 PASS.

Run: `pytest tests/test_billing.py --no-header -q`
Expected: 87 prior + 3 new = 90 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/billing.py tests/test_billing.py
git commit -m "feat(billing): 14-day deletion reminder email helper"
```

---

## Task 2: `send_data_deleted_email` helper (the final notification)

**Files:**
- Modify: `backend/services/billing.py`
- Modify: `tests/test_billing.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_billing.py`:

```python
async def test_send_data_deleted_email_calls_resend(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    """Final notice: data has been deleted. No reactivation possible."""
    from contextlib import asynccontextmanager
    import backend.database as _db_module
    from backend.services.billing import send_data_deleted_email
    from backend.models import User

    og_with_card.canceled_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db_session.add(User(
        id=_id(),
        company_id=COMPANY_ID,
        email="manager@acme.test",
        hashed_password="$2b$12$dummy",
        full_name="Acme Manager",
        user_role="manager",
    ))
    await db_session.commit()

    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(settings, "FROM_EMAIL", "noreply@wiz.test")

    @asynccontextmanager
    async def _fake_session_factory():
        yield db_session
    monkeypatch.setattr(_db_module, "async_session_factory", _fake_session_factory)

    sent = []
    class FakeEmails:
        @staticmethod
        def send(payload):
            sent.append(payload)
    class FakeResend:
        api_key = None
        Emails = FakeEmails()
    monkeypatch.setitem(__import__("sys").modules, "resend", FakeResend)

    await send_data_deleted_email(og_with_card)

    assert len(sent) == 1
    body = sent[0]
    assert "manager@acme.test" in body["to"]
    assert "deleted" in body["subject"].lower()
    assert "permanently" in body["html"].lower() or "irreversibl" in body["html"].lower()


async def test_send_data_deleted_email_noop_without_resend_key(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    from backend.services.billing import send_data_deleted_email
    monkeypatch.setattr(settings, "RESEND_API_KEY", "")
    await send_data_deleted_email(og_with_card)  # must not raise
```

- [ ] **Step 2: Run, verify FAIL**

Run: `pytest tests/test_billing.py -k "send_data_deleted_email" --no-header -q`
Expected: 2 FAIL.

- [ ] **Step 3: Implement the helper**

Append to `backend/services/billing.py`:

```python
async def send_data_deleted_email(og: OwnershipGroup) -> None:
    """Final notice: the OG's data has just been (or is about to be) deleted.

    Must be called BEFORE delete_og_and_dependents so we still have access
    to the manager email addresses. Resend errors are swallowed.
    """
    if not settings.RESEND_API_KEY:
        logger.info("Skipping data-deleted email for og=%s (RESEND_API_KEY unset)", og.id)
        return

    from sqlalchemy import select
    from backend.models import Company, User
    from backend.database import async_session_factory
    import html as _html

    async with async_session_factory() as db:
        company_ids = (await db.execute(
            select(Company.id).where(Company.ownership_group_id == og.id)
        )).scalars().all()
        if not company_ids:
            return
        manager_emails = (await db.execute(
            select(User.email).where(
                User.company_id.in_(company_ids),
                User.user_role == "manager",
            )
        )).scalars().all()
        manager_emails = [e for e in manager_emails if e]

    if not manager_emails:
        logger.info("No manager emails for og=%s; data-deleted email skipped", og.id)
        return

    try:
        import resend
        resend.api_key = settings.RESEND_API_KEY
        resend.Emails.send({
            "from": settings.FROM_EMAIL,
            "to": list(manager_emails),
            "subject": "Your WizScheduler data has been deleted",
            "html": (
                f'<div style="font-family:sans-serif;max-width:600px;margin:0 auto;">'
                f"<p>Hi {_html.escape(og.name)},</p>"
                f"<p>As scheduled, your WizScheduler account and all "
                f"associated data have been <strong>permanently and "
                f"irreversibly deleted</strong>.</p>"
                f"<p>This includes all employees, schedules, locations, "
                f"and historical records.</p>"
                f"<p>If you'd like to use WizScheduler again, you can "
                f"sign up for a new account from scratch.</p>"
                f"<p>Thank you for using WizScheduler.</p>"
                f"</div>"
            ),
        })
    except Exception:
        logger.error(
            "Failed to send data-deleted email for og=%s",
            og.id,
            exc_info=True,
        )
```

- [ ] **Step 4: Run, verify PASS**

Run: `pytest tests/test_billing.py -k "send_data_deleted_email" --no-header -q`
Expected: 2 PASS.

Run: `pytest tests/test_billing.py --no-header -q`
Expected: 90 prior + 2 new = 92 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/billing.py tests/test_billing.py
git commit -m "feat(billing): data-deleted final notice email helper"
```

---

## Task 3: `delete_og_and_dependents` — the hard-delete helper

**Files:**
- Modify: `backend/services/billing.py`
- Modify: `tests/test_billing.py`

This is the highest-risk task. The helper must delete the OG plus everything that references it (directly or transitively) without leaving FK orphans. We delete in dependency order via bulk `delete()` statements.

**Dependency graph (from `backend/models/`):**

```
ownership_groups
  ├── billing_charges       (ondelete=CASCADE — already auto-handled)
  ├── storage_snapshots     (ondelete=CASCADE — already auto-handled)
  ├── token_usage
  └── companies
        ├── users
        ├── failure_logs
        ├── user_consents
        ├── regions
        ├── locations
        │     ├── departments
        │     └── shift_templates
        ├── roles
        ├── condensed_roles   (self-ref)
        ├── employees         (self-ref via "buddy" / supervisor)
        │     └── employee_role_minutes
        └── shift_schedules   (self-ref + ref to employees, locations, roles, companies)
```

**Deletion order (children first):**
1. `shift_schedules` (depends on companies, employees, locations, roles; has self-FK)
2. `employee_role_minutes` (depends on companies, employees, roles)
3. `departments` (depends on companies, locations)
4. `shift_templates` (depends on companies, locations)
5. `employees` (depends on companies, roles, users; has self-FK)
6. `locations` (depends on companies, regions)
7. `condensed_roles` (depends on companies, roles; has self-FK)
8. `roles` (depends on companies)
9. `regions` (depends on companies)
10. `user_consents` (depends on companies, users)
11. `failure_logs` (depends on companies)
12. `users` (depends on companies)
13. `companies` (depends on ownership_groups)
14. `token_usage` (depends on ownership_groups)
15. (storage_snapshots and billing_charges cascade automatically)
16. `ownership_groups`

PostgreSQL bulk `DELETE` statements check FK constraints at end-of-statement, so self-references within the deleted set are OK.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_billing.py`:

```python
async def test_delete_og_and_dependents_removes_full_subtree(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    """Verify the helper removes the OG and every dependent row, leaving no
    FK orphans. Seed a representative slice: 1 user, 1 region, 1 location,
    1 role, 1 employee, 1 shift_template, 1 shift_schedule, 1 token_usage row.
    """
    from sqlalchemy import select, func
    from backend.services.billing import delete_og_and_dependents
    from backend.models import (
        Company, Employee, Location, Region, Role, ShiftTemplate, User,
    )
    from backend.models.condensed_role import CondensedRole
    from backend.models.schedule import ShiftSchedule
    from backend.models.token_usage import TokenUsage

    # Seed dependent rows under the existing og_with_card's company.
    user = User(
        id=_id(),
        company_id=COMPANY_ID,
        email="del@acme.test",
        hashed_password="$2b$12$dummy",
        full_name="To Delete",
        user_role="manager",
    )
    region = Region(id=_id(), company_id=COMPANY_ID, name="R1")
    db_session.add_all([user, region])
    await db_session.flush()
    location = Location(id=_id(), company_id=COMPANY_ID, region_id=region.id, name="L1", timezone="UTC")
    role = Role(id=_id(), company_id=COMPANY_ID, name="Server")
    db_session.add_all([location, role])
    await db_session.flush()
    cr = CondensedRole(id=_id(), company_id=COMPANY_ID, name="Server", parent_id=None)
    employee = Employee(
        id=_id(), company_id=COMPANY_ID, name="Alice",
        user_id=None, role_id=role.id, skill_level=3,
    )
    template = ShiftTemplate(
        id=_id(), company_id=COMPANY_ID, location_id=location.id,
        name="Morning", start_time="09:00", end_time="17:00",
    )
    db_session.add_all([cr, employee, template])
    await db_session.flush()
    schedule = ShiftSchedule(
        id=_id(), company_id=COMPANY_ID, location_id=location.id,
        employee_id=employee.id, role_id=role.id,
        start_time=datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 5, 1, 17, 0, tzinfo=timezone.utc),
    )
    usage = TokenUsage(
        id=_id(), ownership_group_id=OG_ID, year=2026, month=5,
        input_tokens=1000, output_tokens=500, cost_usd=0.05,
    )
    db_session.add_all([schedule, usage])
    await db_session.commit()

    # Sanity check: everything seeded.
    assert (await db_session.execute(select(func.count()).select_from(Employee))).scalar() >= 1
    assert (await db_session.execute(select(func.count()).select_from(ShiftSchedule))).scalar() >= 1

    # Act
    await delete_og_and_dependents(db_session, og_with_card)
    await db_session.commit()

    # Assert — every table that previously had rows for OG_ID/COMPANY_ID is empty.
    from backend.models.ownership_group import OwnershipGroup
    assert (await db_session.execute(select(OwnershipGroup).where(OwnershipGroup.id == OG_ID))).scalar_one_or_none() is None
    assert (await db_session.execute(select(Company).where(Company.id == COMPANY_ID))).scalar_one_or_none() is None
    assert (await db_session.execute(select(User).where(User.id == user.id))).scalar_one_or_none() is None
    assert (await db_session.execute(select(Employee).where(Employee.id == employee.id))).scalar_one_or_none() is None
    assert (await db_session.execute(select(ShiftSchedule).where(ShiftSchedule.id == schedule.id))).scalar_one_or_none() is None
    assert (await db_session.execute(select(TokenUsage).where(TokenUsage.id == usage.id))).scalar_one_or_none() is None


async def test_delete_og_and_dependents_handles_empty_og(
    db_session: AsyncSession, monkeypatch
):
    """An OG with no companies or dependents still deletes cleanly."""
    from backend.services.billing import delete_og_and_dependents
    from backend.models.ownership_group import OwnershipGroup
    from sqlalchemy import select

    bare_og = OwnershipGroup(id=_id(), name="Empty OG", stripe_customer_id="cus_empty")
    db_session.add(bare_og)
    await db_session.commit()

    await delete_og_and_dependents(db_session, bare_og)
    await db_session.commit()

    assert (await db_session.execute(select(OwnershipGroup).where(OwnershipGroup.id == bare_og.id))).scalar_one_or_none() is None
```

- [ ] **Step 2: Run, verify FAIL**

Run: `pytest tests/test_billing.py -k "delete_og_and_dependents" --no-header -q`
Expected: 2 FAIL.

- [ ] **Step 3: Implement the helper**

Append to `backend/services/billing.py`:

```python
async def delete_og_and_dependents(db: AsyncSession, og: OwnershipGroup) -> None:
    """Permanently delete an ownership group and every row that references it.

    Called by the day-90 cron after send_data_deleted_email. Caller must commit.

    The deletion walks the FK dependency graph in child-first order. Tables
    not listed here either have ondelete=CASCADE (billing_charges, storage_snapshots)
    or don't reference ownership_groups / companies.
    """
    from sqlalchemy import delete, select
    from backend.models import (
        Company, Employee, Location, Region, Role, ShiftTemplate, User,
    )
    from backend.models.condensed_role import CondensedRole
    from backend.models.consent import UserConsent
    from backend.models.department import Department
    from backend.models.employee_role_minutes import EmployeeRoleMinutes
    from backend.models.failure_log import FailureLog
    from backend.models.schedule import ShiftSchedule
    from backend.models.token_usage import TokenUsage

    company_ids = (await db.execute(
        select(Company.id).where(Company.ownership_group_id == og.id)
    )).scalars().all()

    if company_ids:
        # 1. shift_schedules (self-FK; safe to bulk-delete in one statement)
        await db.execute(delete(ShiftSchedule).where(ShiftSchedule.company_id.in_(company_ids)))
        # 2. employee_role_minutes (refs employees + roles)
        await db.execute(delete(EmployeeRoleMinutes).where(EmployeeRoleMinutes.company_id.in_(company_ids)))
        # 3. departments
        await db.execute(delete(Department).where(Department.company_id.in_(company_ids)))
        # 4. shift_templates
        await db.execute(delete(ShiftTemplate).where(ShiftTemplate.company_id.in_(company_ids)))
        # 5. employees (self-FK)
        await db.execute(delete(Employee).where(Employee.company_id.in_(company_ids)))
        # 6. locations
        await db.execute(delete(Location).where(Location.company_id.in_(company_ids)))
        # 7. condensed_roles (self-FK)
        await db.execute(delete(CondensedRole).where(CondensedRole.company_id.in_(company_ids)))
        # 8. roles
        await db.execute(delete(Role).where(Role.company_id.in_(company_ids)))
        # 9. regions
        await db.execute(delete(Region).where(Region.company_id.in_(company_ids)))
        # 10. user_consents
        await db.execute(delete(UserConsent).where(UserConsent.company_id.in_(company_ids)))
        # 11. failure_logs
        await db.execute(delete(FailureLog).where(FailureLog.company_id.in_(company_ids)))
        # 12. users
        await db.execute(delete(User).where(User.company_id.in_(company_ids)))
        # 13. companies
        await db.execute(delete(Company).where(Company.id.in_(company_ids)))

    # 14. token_usage (OG-level, no FK cascade configured)
    await db.execute(delete(TokenUsage).where(TokenUsage.ownership_group_id == og.id))

    # 15 + 16. storage_snapshots + billing_charges cascade automatically when OG goes.

    # 17. The OG row itself.
    await db.delete(og)
```

(Imports inside the function body — pattern matches `send_subscription_ended_email`. The list of `from backend.models...` imports is verbose; verify each import path against the actual file paths in `backend/models/`.)

- [ ] **Step 4: Run tests, verify PASS**

Run: `pytest tests/test_billing.py -k "delete_og_and_dependents" --no-header -q`
Expected: 2 PASS.

Run: `pytest tests/test_billing.py --no-header -q`
Expected: 92 prior + 2 new = 94 PASS.

If any test fails with FK-violation errors, the deletion order is wrong — investigate which table is being deleted before its dependents.

- [ ] **Step 5: Commit**

```bash
git add backend/services/billing.py tests/test_billing.py
git commit -m "feat(billing): delete_og_and_dependents helper for hard-delete cron"
```

---

## Task 4: `process_cancellation_lifecycle` orchestrator

**Files:**
- Modify: `backend/services/billing.py`
- Modify: `tests/test_billing.py`

The orchestrator runs once per day. It:
- Loads all OGs with `canceled_at IS NOT NULL`
- For each: computes `age_days = (now - canceled_at).days`
- If `age_days >= 76` and `notified_deletion_reminder_at is None`: sends reminder email + stamps the flag
- If `age_days >= 90` and `notified_data_deleted_at is None`: sends final email then deletes the OG (the row goes away, so the flag is incidentally permanent)

Day-76 = `SUBSCRIPTION_GRACE_DAYS - SUBSCRIPTION_REMINDER_DAYS_BEFORE_DELETE` = 90 - 14.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_billing.py`:

```python
async def test_lifecycle_skips_active_ogs(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    """OGs with canceled_at IS NULL must be ignored entirely."""
    from backend.services.billing import process_cancellation_lifecycle

    reminders, deletions = [], []
    async def fake_reminder(og): reminders.append(og.id)
    async def fake_deleted(og): deletions.append(og.id)
    monkeypatch.setattr("backend.services.billing.send_deletion_reminder_email", fake_reminder)
    monkeypatch.setattr("backend.services.billing.send_data_deleted_email", fake_deleted)

    # og_with_card.canceled_at is None (default fixture state)
    await process_cancellation_lifecycle(db_session)
    assert reminders == []
    assert deletions == []


async def test_lifecycle_sends_reminder_at_day_76(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    """At exactly 76 days post-cancel, the reminder fires and the flag stamps."""
    from backend.services.billing import process_cancellation_lifecycle

    og_with_card.canceled_at = datetime.now(timezone.utc) - timedelta(days=76)
    await db_session.commit()

    reminders, deletions = [], []
    async def fake_reminder(og): reminders.append(og.id)
    async def fake_deleted(og): deletions.append(og.id)
    monkeypatch.setattr("backend.services.billing.send_deletion_reminder_email", fake_reminder)
    monkeypatch.setattr("backend.services.billing.send_data_deleted_email", fake_deleted)

    await process_cancellation_lifecycle(db_session)
    assert reminders == [OG_ID]
    assert deletions == []

    await db_session.refresh(og_with_card)
    assert og_with_card.notified_deletion_reminder_at is not None


async def test_lifecycle_reminder_is_idempotent(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    """Second run after reminder already sent doesn't re-fire."""
    from backend.services.billing import process_cancellation_lifecycle

    og_with_card.canceled_at = datetime.now(timezone.utc) - timedelta(days=80)
    og_with_card.notified_deletion_reminder_at = datetime.now(timezone.utc) - timedelta(days=3)
    await db_session.commit()

    reminders = []
    async def fake_reminder(og): reminders.append(og.id)
    async def fake_deleted(og): pass
    monkeypatch.setattr("backend.services.billing.send_deletion_reminder_email", fake_reminder)
    monkeypatch.setattr("backend.services.billing.send_data_deleted_email", fake_deleted)

    await process_cancellation_lifecycle(db_session)
    assert reminders == []


async def test_lifecycle_deletes_at_day_90(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    """At day 90+, send the final email AND delete the OG row."""
    from sqlalchemy import select
    from backend.models.ownership_group import OwnershipGroup
    from backend.services.billing import process_cancellation_lifecycle

    og_with_card.canceled_at = datetime.now(timezone.utc) - timedelta(days=91)
    await db_session.commit()

    deletions, reminders = [], []
    async def fake_reminder(og): reminders.append(og.id)
    async def fake_deleted(og): deletions.append(og.id)
    monkeypatch.setattr("backend.services.billing.send_deletion_reminder_email", fake_reminder)
    monkeypatch.setattr("backend.services.billing.send_data_deleted_email", fake_deleted)

    await process_cancellation_lifecycle(db_session)
    assert deletions == [OG_ID]

    # OG row must be gone after commit
    assert (await db_session.execute(
        select(OwnershipGroup).where(OwnershipGroup.id == OG_ID)
    )).scalar_one_or_none() is None


async def test_lifecycle_reminder_and_deletion_both_overdue(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    """If the cron never ran for an OG that's now day 91 and never got the
    reminder, the deletion should still happen — reminder is best-effort."""
    from backend.services.billing import process_cancellation_lifecycle

    og_with_card.canceled_at = datetime.now(timezone.utc) - timedelta(days=91)
    # notified_deletion_reminder_at left None
    await db_session.commit()

    reminders, deletions = [], []
    async def fake_reminder(og): reminders.append(og.id)
    async def fake_deleted(og): deletions.append(og.id)
    monkeypatch.setattr("backend.services.billing.send_deletion_reminder_email", fake_reminder)
    monkeypatch.setattr("backend.services.billing.send_data_deleted_email", fake_deleted)

    await process_cancellation_lifecycle(db_session)
    # Day 91 → send reminder + delete in the same pass. Order doesn't matter
    # since both happen before the row is gone.
    assert reminders == [OG_ID]
    assert deletions == [OG_ID]
```

Add `from datetime import timedelta` to test_billing.py's imports if not already present.

- [ ] **Step 2: Run, verify FAIL**

Run: `pytest tests/test_billing.py -k "lifecycle" --no-header -q`
Expected: 5 FAIL (function doesn't exist).

- [ ] **Step 3: Implement the orchestrator**

Append to `backend/services/billing.py`:

```python
async def process_cancellation_lifecycle(db: AsyncSession) -> dict:
    """Daily cron driver.

    Walks all OGs with canceled_at set; sends day-76 reminder once, performs
    day-90 deletion once. Returns a small summary dict for logging.
    """
    from sqlalchemy import select
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    reminder_threshold_days = (
        settings.SUBSCRIPTION_GRACE_DAYS
        - settings.SUBSCRIPTION_REMINDER_DAYS_BEFORE_DELETE
    )

    canceled_ogs = (await db.execute(
        select(OwnershipGroup).where(OwnershipGroup.canceled_at.is_not(None))
    )).scalars().all()

    reminders_sent = 0
    deletions_done = 0

    for og in canceled_ogs:
        # canceled_at column is timezone-aware UTC in PG; SQLite drops tz.
        canceled_at = og.canceled_at
        if canceled_at.tzinfo is None:
            canceled_at = canceled_at.replace(tzinfo=timezone.utc)
        age_days = (now - canceled_at).days

        # Day 76: send reminder (once)
        if age_days >= reminder_threshold_days and og.notified_deletion_reminder_at is None:
            await send_deletion_reminder_email(og)
            og.notified_deletion_reminder_at = now
            reminders_sent += 1

        # Day 90: delete (once — the row goes away so this is naturally idempotent)
        if age_days >= settings.SUBSCRIPTION_GRACE_DAYS:
            await send_data_deleted_email(og)  # must happen BEFORE delete
            await delete_og_and_dependents(db, og)
            deletions_done += 1

    await db.commit()
    return {"reminders_sent": reminders_sent, "deletions_done": deletions_done}
```

Note: `send_deletion_reminder_email`, `send_data_deleted_email`, and `delete_og_and_dependents` are referenced by name — they live in the same module, so no import needed.

- [ ] **Step 4: Run tests, verify PASS**

Run: `pytest tests/test_billing.py -k "lifecycle" --no-header -q`
Expected: 5 PASS.

Run: `pytest tests/test_billing.py --no-header -q`
Expected: 94 prior + 5 new = 99 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/billing.py tests/test_billing.py
git commit -m "feat(billing): process_cancellation_lifecycle orchestrator"
```

---

## Task 5: Wire the daily background loop in `main.py`

**Files:**
- Modify: `backend/main.py`

- [ ] **Step 1: Add the loop function and start task**

In `backend/main.py`, after `_weekly_monthly_billing_loop` (around line 164) and BEFORE the `@app.on_event("startup")` block (around line 166), add:

```python
    # ── Daily background task: cancellation lifecycle (PR β) ──
    async def _daily_cancellation_lifecycle_loop() -> None:
        """Walk canceled OGs daily. Send reminder at day 76, hard-delete at day 90."""
        await asyncio.sleep(120)  # let app settle past startup
        while True:
            try:
                async with async_session_factory() as db:
                    from backend.services.billing import process_cancellation_lifecycle
                    result = await process_cancellation_lifecycle(db)
                    log.info(
                        "Cancellation lifecycle pass: reminders=%s deletions=%s",
                        result.get("reminders_sent"),
                        result.get("deletions_done"),
                    )
            except Exception as e:
                log.error("Cancellation lifecycle failed: %s", e, exc_info=True)
            await asyncio.sleep(24 * 60 * 60)
```

Then in the `_start_background_tasks` function (currently around line 170), add the new task creation alongside the existing two:

```python
    @app.on_event("startup")
    async def _start_background_tasks() -> None:
        log.info("Starting background tasks...")
        asyncio.create_task(_daily_storage_snapshot_loop())
        asyncio.create_task(_weekly_monthly_billing_loop())
        asyncio.create_task(_daily_cancellation_lifecycle_loop())
```

- [ ] **Step 2: Verify imports**

Confirm at the top of `main.py`:
- `asyncio` is imported
- `async_session_factory` is imported (it's already used by the other loops)
- `log` is the existing logger name

Do NOT add a top-level `from backend.services.billing import process_cancellation_lifecycle` — the import is deferred inside the loop body to keep startup fast and avoid circular import surprises.

- [ ] **Step 3: Smoke-test the import wiring**

Run: `python -c "from backend.main import app; print('app loaded')"`
Expected: prints `app loaded`. If it errors with ImportError, fix the issue.

- [ ] **Step 4: Run the full backend tests (this should also import main without issue)**

Run: `pytest tests/test_billing.py --no-header -q`
Expected: 99 PASS (no change vs Task 4 — the loop doesn't run during tests because tests don't wait 120s).

- [ ] **Step 5: Commit**

```bash
git add backend/main.py
git commit -m "feat(billing): daily cancellation lifecycle background task"
```

---

## Task 6: Final verification + push + PR

- [ ] **Step 1: Run the full billing test suite one more time**

Run: `pytest tests/test_billing.py --no-header -q`
Expected: 99 PASS.

- [ ] **Step 2: Run the frontend build to confirm no drift**

```bash
cd frontend && ./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/vite build
```

Expected: clean (PR β is backend-only; nothing should have changed on the frontend).

- [ ] **Step 3: Push the branch**

If PR α merged to main first, branch from main; otherwise stack PR β on top of PR α's branch. The dev convention is fresh branch from main:

```bash
git checkout main
git pull
git checkout -b feat-cancellation-beta
# cherry-pick the PR β commits if branched after PR α merged, or
# branch directly off PR α's branch if α isn't merged yet
git push -u origin feat-cancellation-beta
```

(If PR α is still open, instead `git checkout -b feat-cancellation-beta spec-cancellation-design` to stack β on α.)

- [ ] **Step 4: Open the PR**

```bash
gh pr create --title "Subscription cancellation lifecycle (PR β: deletion cron + lifecycle emails)" --body "$(cat <<'EOF'
## Summary
Completes the subscription-cancellation lifecycle started in PR α. Adds the daily background loop that warns customers 14 days before deletion and hard-deletes their data at day 90.

- `send_deletion_reminder_email(og)` — sent at day 76, mentions exact deletion date + reactivate CTA, XSS-safe og.name
- `send_data_deleted_email(og)` — sent immediately before the actual delete, so we still have manager addresses
- `delete_og_and_dependents(db, og)` — explicit-order DELETE walking the FK graph (shift_schedules → ... → companies → token_usage → ownership_group)
- `process_cancellation_lifecycle(db)` — the orchestrator: skips active OGs, fires reminder once at day 76, performs final email + delete once at day 90, idempotent across reruns
- `_daily_cancellation_lifecycle_loop` background task in main.py, modeled on the existing storage-snapshot and monthly-billing loops

## Deploy steps
1. Merge → CI deploys (no migration; PR α already added the schema)
2. The loop starts 120s after startup. First useful pass: day after the first OG hits day 76 post-cancel.

## Test plan
- [x] 99 billing tests pass (5 lifecycle, 2 deletion, 5 email helpers — all TDD)
- [x] Smoke: `python -c "from backend.main import app"` succeeds (loop wires in cleanly)
- [ ] After deploy: tail logs for "Cancellation lifecycle pass: reminders=0 deletions=0" daily — confirms the loop is running and finding nothing yet
- [ ] After first real cancellation hits day 76: verify the reminder email landed; confirm `notified_deletion_reminder_at` stamped
- [ ] After first cancellation hits day 90: confirm `data_deleted` email landed, OG + dependents gone from DB

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: Confirm PR is open**

Note the PR URL in your final report.

---

## Self-Review

**Spec coverage** (against `docs/superpowers/specs/2026-05-14-subscription-cancellation-design.md` PR β section):
- ✅ `process_cancellation_lifecycle` service function — Task 4
- ✅ Daily background loop in main.py — Task 5
- ✅ 14-day reminder email template + helper — Task 1
- ✅ Final "data deleted" email template + helper — Task 2
- ✅ Hard-delete helper that walks CASCADE relationships — Task 3
- ✅ Tests: cron skips active OGs (Task 4 test 1), sends reminder at day 76 (test 2), deletes at day 90 (test 4), idempotency on repeated runs (test 3)

**Type consistency:** `send_deletion_reminder_email`, `send_data_deleted_email`, `delete_og_and_dependents`, `process_cancellation_lifecycle` all reference each other by exact names; verified.

**No placeholders.** Every code block is runnable.

**Risk callouts (deferred — not part of this plan but worth noting):**
- Race window: if a customer reactivates between minute X and X+24h while the cron runs, there's a small chance of deletion happening just after reactivation. Mitigation deferred — the spec says it's an edge case rare enough to skip; if it ever bites, add a row lock in the cron loop or recheck `canceled_at IS NOT NULL` immediately before deletion.
- Manual deletion vs DB CASCADE: chose manual to avoid a 20+ table migration. If the FK graph grows, this helper needs maintenance — a CI check that asserts no orphan tables would catch drift.
