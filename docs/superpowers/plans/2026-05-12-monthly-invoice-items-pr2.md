# PR 2: Monthly InvoiceItems + Stripe Webhooks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add monthly InvoiceItem-based billing for storage and employee overages, plus a Stripe webhook endpoint that handles `invoice.upcoming`, `invoice.payment_failed`, and `payment_method.attached`.

**Architecture:** A weekly in-process background loop (modeled on the existing `record_storage_snapshots` daily loop in `backend/main.py:110-135`) calls `bill_monthly_overages_all`, which computes storage + employee overages per OG and creates/updates/deletes `stripe.InvoiceItem` rows on each OG's upcoming subscription invoice. Idempotency comes from the existing `billing_charges` audit table — every InvoiceItem maps 1:1 to a `BillingCharge` row keyed by `(ownership_group_id, kind, period)`. A new `POST /webhooks/stripe` endpoint verifies Stripe's signature and dispatches three event handlers: `invoice.upcoming` (recompute monthly InvoiceItems for that OG, defense-in-depth), `invoice.payment_failed` (set `autoreload_failed_at` to block generation), `payment_method.attached` (refresh cached PM ID).

**Tech Stack:** FastAPI · SQLAlchemy 2.x async · Alembic · `stripe` Python SDK · React 18 · TypeScript · Vite · pytest-asyncio · httpx

**Spec:** `docs/superpowers/specs/2026-05-11-usage-overage-billing-design.md` (Monthly Track + Failure Modes sections)

**Depends on:** PR 1 (auto-reload billing) — merged at commit `f774918` on `main`. PR 2 branches off `main`.

---

## Task 1: `compute_monthly_overage` pure helper

**Files:**
- Modify: `backend/services/billing.py` (append below `calculate_employee_charge`)
- Modify: `tests/test_billing.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_billing.py`:

```python
async def test_compute_monthly_overage_storage_zero(db_session: AsyncSession, seed_og):
    """No storage snapshot for the period → returns 0."""
    from backend.services.billing import compute_monthly_overage
    charge = await compute_monthly_overage(db_session, OG_ID, "invoice_item_storage", "2026-05")
    assert charge == 0.0


async def test_compute_monthly_overage_storage_within_free_tier(db_session: AsyncSession, seed_og):
    """Storage snapshot under 0.5 GB → returns 0."""
    from backend.models import StorageSnapshot
    from backend.services.billing import compute_monthly_overage
    from datetime import date
    db_session.add(StorageSnapshot(
        ownership_group_id=OG_ID,
        snapshot_date=date(2026, 5, 15),
        measured_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
        storage_gb=0.3,
        charged_usd=0.0,
    ))
    await db_session.commit()
    charge = await compute_monthly_overage(db_session, OG_ID, "invoice_item_storage", "2026-05")
    assert charge == 0.0


async def test_compute_monthly_overage_storage_over_free_tier(db_session: AsyncSession, seed_og):
    """Storage snapshot of 1.5 GB → $0.50 (1.0 GB billable × $0.50)."""
    from backend.models import StorageSnapshot
    from backend.services.billing import compute_monthly_overage
    from datetime import date
    db_session.add(StorageSnapshot(
        ownership_group_id=OG_ID,
        snapshot_date=date(2026, 5, 15),
        measured_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
        storage_gb=1.5,
        charged_usd=0.5,
    ))
    await db_session.commit()
    charge = await compute_monthly_overage(db_session, OG_ID, "invoice_item_storage", "2026-05")
    assert charge == 0.5


async def test_compute_monthly_overage_storage_picks_latest_in_period(db_session: AsyncSession, seed_og):
    """When multiple snapshots exist in period, use the most recent."""
    from backend.models import StorageSnapshot
    from backend.services.billing import compute_monthly_overage
    from datetime import date
    db_session.add(StorageSnapshot(
        ownership_group_id=OG_ID,
        snapshot_date=date(2026, 5, 1),
        measured_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        storage_gb=0.6,
        charged_usd=0.05,
    ))
    db_session.add(StorageSnapshot(
        ownership_group_id=OG_ID,
        snapshot_date=date(2026, 5, 28),
        measured_at=datetime(2026, 5, 28, tzinfo=timezone.utc),
        storage_gb=2.5,
        charged_usd=1.0,
    ))
    await db_session.commit()
    charge = await compute_monthly_overage(db_session, OG_ID, "invoice_item_storage", "2026-05")
    # 2.5 GB measured, 0.5 GB free → 2.0 billable GB × $0.50 = $1.00
    assert charge == 1.0


async def test_compute_monthly_overage_employees_zero(db_session: AsyncSession, seed_og):
    """No employees → returns 0."""
    from backend.services.billing import compute_monthly_overage
    charge = await compute_monthly_overage(db_session, OG_ID, "invoice_item_employees", "2026-05")
    assert charge == 0.0


async def test_compute_monthly_overage_employees_over_free_tier(db_session: AsyncSession, seed_og):
    """1500 employees → 1 block × $1.00 (500 over free tier of 1000)."""
    from backend.services.billing import compute_monthly_overage
    for _ in range(1500):
        db_session.add(Employee(id=_id(), company_id=COMPANY_ID, full_name="X"))
    await db_session.commit()
    charge = await compute_monthly_overage(db_session, OG_ID, "invoice_item_employees", "2026-05")
    assert charge == 1.0


async def test_compute_monthly_overage_unknown_kind_raises(db_session: AsyncSession, seed_og):
    """Unknown kind raises ValueError."""
    from backend.services.billing import compute_monthly_overage
    with pytest.raises(ValueError):
        await compute_monthly_overage(db_session, OG_ID, "invoice_item_nonsense", "2026-05")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_billing.py -k compute_monthly_overage -v`
Expected: 7 errors with `ImportError: cannot import name 'compute_monthly_overage'`

- [ ] **Step 3: Implement the helper**

Append to `backend/services/billing.py`:

```python
async def compute_monthly_overage(
    db: AsyncSession,
    og_id: str,
    kind: str,
    period: str,
) -> float:
    """Compute the dollar overage charge for a given kind+period.

    `kind` must be 'invoice_item_storage' or 'invoice_item_employees'.
    `period` is 'YYYY-MM'. Storage uses the most recent snapshot within
    that calendar month; employees uses the current point-in-time count
    (employee billing is anniversary-period based, not historical).

    Returns 0.0 if usage is within the free tier.
    """
    from datetime import date

    if kind == "invoice_item_storage":
        year, month = int(period[:4]), int(period[5:7])
        result = await db.execute(
            select(StorageSnapshot)
            .where(
                StorageSnapshot.ownership_group_id == og_id,
                StorageSnapshot.snapshot_date >= date(year, month, 1),
                StorageSnapshot.snapshot_date < (
                    date(year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 1)
                ),
            )
            .order_by(StorageSnapshot.snapshot_date.desc())
            .limit(1)
        )
        snap = result.scalar_one_or_none()
        if not snap:
            return 0.0
        return calculate_storage_charge(float(snap.storage_gb))

    if kind == "invoice_item_employees":
        count = await count_employees_for_group(db, og_id)
        return calculate_employee_charge(count)

    raise ValueError(f"Unknown overage kind: {kind!r}")
```

You also need to ensure `StorageSnapshot` is importable in that file. Add to the imports at the top of `backend/services/billing.py` (with the other model imports):

```python
from backend.models import Company, Employee, StorageSnapshot, TokenUsage
```

(If `StorageSnapshot` is already there, leave it.)

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_billing.py -k compute_monthly_overage -v`
Expected: 7 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/billing.py tests/test_billing.py
git commit -m "feat(billing): compute_monthly_overage pure helper"
```

---

## Task 2: `bill_monthly_overages_for_og` per-OG runner

**Files:**
- Modify: `backend/services/billing.py` (append)
- Modify: `tests/test_billing.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_billing.py`:

```python
async def test_bill_monthly_overages_creates_invoice_item_when_charge_positive(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    """When overage > 0 and no existing BillingCharge exists, create Stripe
    InvoiceItem + audit row."""
    import stripe
    from backend.services.billing import bill_monthly_overages_for_og
    from backend.models import StorageSnapshot
    from backend.models.billing_charge import BillingCharge
    from sqlalchemy import select as _sel
    from datetime import date

    # Seed storage usage over free tier
    db_session.add(StorageSnapshot(
        ownership_group_id=OG_ID,
        snapshot_date=date(2026, 5, 15),
        measured_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
        storage_gb=1.5,
        charged_usd=0.5,
    ))
    await db_session.commit()

    # Stub the Stripe subscription retrieval and InvoiceItem creation
    fake_sub = MagicMock(status="active", current_period_start=int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp()))
    monkeypatch.setattr(stripe.Subscription, "retrieve", lambda sid: fake_sub)
    created = []
    def fake_create(**kwargs):
        created.append(kwargs)
        return MagicMock(id=f"ii_{len(created)}")
    monkeypatch.setattr(stripe.InvoiceItem, "create", fake_create)

    await bill_monthly_overages_for_og(db_session, og_with_card, period="2026-05")

    # One InvoiceItem created for storage; nothing for employees (no employees seeded)
    assert len(created) == 1
    storage_ii = created[0]
    assert storage_ii["customer"] == "cus_test_abc"
    assert storage_ii["amount"] == 50  # $0.50 in cents
    assert storage_ii["metadata"]["kind"] == "invoice_item_storage"
    assert storage_ii["metadata"]["period"] == "2026-05"

    # BillingCharge row recorded
    rows = list((await db_session.execute(
        _sel(BillingCharge).where(BillingCharge.ownership_group_id == OG_ID)
    )).scalars())
    assert len(rows) == 1
    assert rows[0].kind == "invoice_item_storage"
    assert rows[0].period == "2026-05"
    assert rows[0].status == "pending"
    assert rows[0].stripe_object_id == "ii_1"


async def test_bill_monthly_overages_idempotent_same_amount(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    """Re-running with same usage and same period should not create a duplicate."""
    import stripe
    from backend.services.billing import bill_monthly_overages_for_og
    from backend.models import StorageSnapshot
    from backend.models.billing_charge import BillingCharge
    from sqlalchemy import select as _sel
    from datetime import date

    db_session.add(StorageSnapshot(
        ownership_group_id=OG_ID,
        snapshot_date=date(2026, 5, 15),
        measured_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
        storage_gb=1.5,
        charged_usd=0.5,
    ))
    await db_session.commit()

    fake_sub = MagicMock(status="active", current_period_start=int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp()))
    monkeypatch.setattr(stripe.Subscription, "retrieve", lambda sid: fake_sub)
    create_calls = []
    monkeypatch.setattr(stripe.InvoiceItem, "create",
                        lambda **kw: (create_calls.append(kw), MagicMock(id=f"ii_{len(create_calls)}"))[1])
    monkeypatch.setattr(stripe.InvoiceItem, "delete", lambda iid: MagicMock())

    await bill_monthly_overages_for_og(db_session, og_with_card, period="2026-05")
    await bill_monthly_overages_for_og(db_session, og_with_card, period="2026-05")

    # Only one creation
    assert len(create_calls) == 1
    # Only one BillingCharge row
    rows = list((await db_session.execute(
        _sel(BillingCharge).where(BillingCharge.ownership_group_id == OG_ID)
    )).scalars())
    assert len(rows) == 1


async def test_bill_monthly_overages_updates_when_amount_changes(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    """When the recomputed amount differs from the existing pending charge,
    delete the old InvoiceItem and create a new one."""
    import stripe
    from backend.services.billing import bill_monthly_overages_for_og
    from backend.models import StorageSnapshot
    from backend.models.billing_charge import BillingCharge
    from sqlalchemy import select as _sel
    from datetime import date

    db_session.add(StorageSnapshot(
        ownership_group_id=OG_ID,
        snapshot_date=date(2026, 5, 15),
        measured_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
        storage_gb=1.5,
        charged_usd=0.5,
    ))
    await db_session.commit()

    fake_sub = MagicMock(status="active", current_period_start=int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp()))
    monkeypatch.setattr(stripe.Subscription, "retrieve", lambda sid: fake_sub)
    create_calls = []
    delete_calls = []
    monkeypatch.setattr(stripe.InvoiceItem, "create",
                        lambda **kw: (create_calls.append(kw), MagicMock(id=f"ii_{len(create_calls)}"))[1])
    monkeypatch.setattr(stripe.InvoiceItem, "delete",
                        lambda iid: (delete_calls.append(iid), MagicMock())[1])

    await bill_monthly_overages_for_og(db_session, og_with_card, period="2026-05")
    # bump storage
    db_session.add(StorageSnapshot(
        ownership_group_id=OG_ID,
        snapshot_date=date(2026, 5, 28),
        measured_at=datetime(2026, 5, 28, tzinfo=timezone.utc),
        storage_gb=3.0,
        charged_usd=1.25,
    ))
    await db_session.commit()
    await bill_monthly_overages_for_og(db_session, og_with_card, period="2026-05")

    # First call creates ii_1; second call deletes ii_1 and creates ii_2
    assert len(create_calls) == 2
    assert delete_calls == ["ii_1"]
    rows = list((await db_session.execute(
        _sel(BillingCharge).where(BillingCharge.ownership_group_id == OG_ID)
                           .order_by(BillingCharge.created_at)
    )).scalars())
    # Old pending row deleted, new pending row in its place
    assert len(rows) == 1
    assert rows[0].stripe_object_id == "ii_2"
    assert float(rows[0].amount_usd) == 1.25


async def test_bill_monthly_overages_deletes_when_charge_drops_to_zero(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    """When recomputed amount is zero, the existing pending InvoiceItem is deleted."""
    import stripe
    from backend.services.billing import bill_monthly_overages_for_og
    from backend.models import StorageSnapshot
    from backend.models.billing_charge import BillingCharge
    from sqlalchemy import select as _sel
    from datetime import date

    db_session.add(StorageSnapshot(
        ownership_group_id=OG_ID,
        snapshot_date=date(2026, 5, 15),
        measured_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
        storage_gb=1.5,
        charged_usd=0.5,
    ))
    await db_session.commit()

    fake_sub = MagicMock(status="active", current_period_start=int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp()))
    monkeypatch.setattr(stripe.Subscription, "retrieve", lambda sid: fake_sub)
    monkeypatch.setattr(stripe.InvoiceItem, "create",
                        lambda **kw: MagicMock(id="ii_first"))
    delete_calls = []
    monkeypatch.setattr(stripe.InvoiceItem, "delete",
                        lambda iid: (delete_calls.append(iid), MagicMock())[1])

    await bill_monthly_overages_for_og(db_session, og_with_card, period="2026-05")

    # Now overwrite snapshot with usage UNDER the free tier
    db_session.add(StorageSnapshot(
        ownership_group_id=OG_ID,
        snapshot_date=date(2026, 5, 28),
        measured_at=datetime(2026, 5, 28, tzinfo=timezone.utc),
        storage_gb=0.2,
        charged_usd=0.0,
    ))
    await db_session.commit()

    await bill_monthly_overages_for_og(db_session, og_with_card, period="2026-05")

    assert delete_calls == ["ii_first"]
    rows = list((await db_session.execute(
        _sel(BillingCharge).where(BillingCharge.ownership_group_id == OG_ID)
    )).scalars())
    assert rows == []


async def test_bill_monthly_overages_skips_inactive_subscription(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    """When the subscription isn't active or trialing, do nothing."""
    import stripe
    from backend.services.billing import bill_monthly_overages_for_og
    from backend.models import StorageSnapshot
    from backend.models.billing_charge import BillingCharge
    from sqlalchemy import select as _sel
    from datetime import date

    db_session.add(StorageSnapshot(
        ownership_group_id=OG_ID,
        snapshot_date=date(2026, 5, 15),
        measured_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
        storage_gb=1.5,
        charged_usd=0.5,
    ))
    await db_session.commit()

    fake_sub = MagicMock(status="past_due", current_period_start=0)
    monkeypatch.setattr(stripe.Subscription, "retrieve", lambda sid: fake_sub)
    create_calls = []
    monkeypatch.setattr(stripe.InvoiceItem, "create",
                        lambda **kw: (create_calls.append(kw), MagicMock())[1])

    await bill_monthly_overages_for_og(db_session, og_with_card, period="2026-05")

    assert create_calls == []
    rows = list((await db_session.execute(
        _sel(BillingCharge).where(BillingCharge.ownership_group_id == OG_ID)
    )).scalars())
    assert rows == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_billing.py -k bill_monthly_overages_for_og -v`
Expected: errors importing `bill_monthly_overages_for_og`

- [ ] **Step 3: Implement the runner**

Append to `backend/services/billing.py`:

```python
async def bill_monthly_overages_for_og(
    db: AsyncSession,
    og: OwnershipGroup,
    period: str | None = None,
) -> dict:
    """Reconcile storage + employee overage InvoiceItems for one OG.

    For each kind in ('invoice_item_storage', 'invoice_item_employees'):
        - Compute the current charge from live usage data.
        - Look up the existing pending BillingCharge for (og, period, kind).
        - Create, update (delete + recreate), or delete the Stripe InvoiceItem
          and matching BillingCharge row so the final state matches the computed amount.

    Subscriptions not in {active, trialing} are skipped — Stripe will not
    bill anyway, and we don't want to dirty the audit log.

    `period` defaults to the calendar month of the subscription's current
    period_start (i.e. the period the upcoming invoice covers).
    """
    import stripe
    from backend.config import settings
    from backend.models.billing_charge import BillingCharge

    if not og.stripe_subscription_id or not og.stripe_customer_id:
        return {"og_id": og.id, "skipped": "no_subscription"}

    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        sub = stripe.Subscription.retrieve(og.stripe_subscription_id)
    except stripe.StripeError as e:
        return {"og_id": og.id, "error": str(e)}

    if sub.status not in ("active", "trialing"):
        return {"og_id": og.id, "skipped": f"subscription_status={sub.status}"}

    if period is None:
        from datetime import datetime as _dt, timezone as _tz
        start = _dt.fromtimestamp(int(sub.current_period_start), _tz.utc)
        period = f"{start.year:04d}-{start.month:02d}"

    summary = {"og_id": og.id, "period": period, "actions": []}

    for kind in ("invoice_item_storage", "invoice_item_employees"):
        new_charge = await compute_monthly_overage(db, og.id, kind, period)
        new_amount_cents = int(round(new_charge * 100))

        existing = (await db.execute(
            select(BillingCharge).where(
                BillingCharge.ownership_group_id == og.id,
                BillingCharge.kind == kind,
                BillingCharge.period == period,
                BillingCharge.status == "pending",
            )
        )).scalar_one_or_none()

        if existing and int(round(float(existing.amount_usd) * 100)) == new_amount_cents and new_amount_cents > 0:
            summary["actions"].append({"kind": kind, "action": "noop"})
            continue

        if existing:
            try:
                stripe.InvoiceItem.delete(existing.stripe_object_id)
            except stripe.StripeError:
                pass  # invoice item already finalized or gone; proceed
            await db.delete(existing)
            await db.flush()

        if new_amount_cents == 0:
            summary["actions"].append({"kind": kind, "action": "deleted" if existing else "skip_zero"})
            continue

        item = stripe.InvoiceItem.create(
            customer=og.stripe_customer_id,
            subscription=og.stripe_subscription_id,
            amount=new_amount_cents,
            currency="usd",
            description=f"{kind.replace('invoice_item_', '').title()} overage — {period}",
            metadata={"og_id": og.id, "period": period, "kind": kind},
        )
        db.add(BillingCharge(
            ownership_group_id=og.id,
            kind=kind,
            amount_usd=new_charge,
            stripe_object_id=item.id,
            period=period,
            status="pending",
        ))
        await db.flush()
        summary["actions"].append({"kind": kind, "action": "created" if not existing else "updated", "amount_usd": new_charge})

    await db.commit()
    return summary
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_billing.py -k bill_monthly_overages_for_og -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/billing.py tests/test_billing.py
git commit -m "feat(billing): bill_monthly_overages_for_og runner with idempotency via BillingCharge"
```

---

## Task 3: `bill_monthly_overages_all` iterator

**Files:**
- Modify: `backend/services/billing.py` (append)
- Modify: `tests/test_billing.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_billing.py`:

```python
async def test_bill_monthly_overages_all_iterates_subscribed_ogs(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    """bill_monthly_overages_all walks every OG with a subscription and
    invokes the per-OG runner."""
    import stripe
    from backend.services.billing import bill_monthly_overages_all

    fake_sub = MagicMock(
        status="active",
        current_period_start=int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp()),
    )
    monkeypatch.setattr(stripe.Subscription, "retrieve", lambda sid: fake_sub)
    monkeypatch.setattr(stripe.InvoiceItem, "create", lambda **kw: MagicMock(id="ii_x"))

    result = await bill_monthly_overages_all(db_session)
    assert result["total_ogs"] == 1
    assert any(r["og_id"] == OG_ID for r in result["results"])


async def test_bill_monthly_overages_all_skips_unsubscribed(
    db_session: AsyncSession, seed_og
):
    """OGs without a subscription are not in the result list."""
    from backend.services.billing import bill_monthly_overages_all
    result = await bill_monthly_overages_all(db_session)
    assert result["total_ogs"] == 0
    assert result["results"] == []
```

- [ ] **Step 2: Run, verify failure**

Run: `pytest tests/test_billing.py -k bill_monthly_overages_all -v`
Expected: ImportError

- [ ] **Step 3: Implement the iterator**

Append to `backend/services/billing.py`:

```python
async def bill_monthly_overages_all(db: AsyncSession) -> dict:
    """Run bill_monthly_overages_for_og across every OG with a Stripe subscription.

    Designed to be invoked weekly by a background loop and on-demand by the
    `invoice.upcoming` Stripe webhook handler (defense in depth).
    """
    import logging
    log = logging.getLogger("wizscheduler.monthly_billing")

    result = await db.execute(
        select(OwnershipGroup).where(OwnershipGroup.stripe_subscription_id.is_not(None))
    )
    ogs = list(result.scalars())

    results = []
    for og in ogs:
        try:
            summary = await bill_monthly_overages_for_og(db, og)
            results.append(summary)
        except Exception as e:  # noqa: BLE001
            log.error("monthly billing failed for og=%s: %s", og.id, e)
            results.append({"og_id": og.id, "error": str(e)})

    return {"total_ogs": len(ogs), "results": results}
```

- [ ] **Step 4: Run, verify pass**

Run: `pytest tests/test_billing.py -k bill_monthly_overages_all -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/billing.py tests/test_billing.py
git commit -m "feat(billing): bill_monthly_overages_all iterator across subscribed OGs"
```

---

## Task 4: Weekly background loop in main.py

**Files:**
- Modify: `backend/main.py`

- [ ] **Step 1: Add the weekly loop alongside the existing daily storage loop**

Locate the existing `_daily_storage_snapshot_loop` block (around lines 110-135). Below it, add a sibling `_weekly_monthly_billing_loop` and register it on startup.

Find the existing block (use grep to confirm line numbers in the current main.py):

```
    async def _daily_storage_snapshot_loop() -> None:
        ...
```

After the existing daily-snapshot loop is defined, append:

```python
    async def _weekly_monthly_billing_loop() -> None:
        """Reconcile monthly InvoiceItems for storage + employee overages.

        Runs every 7 days. Idempotent — re-runs in the same period either
        no-op or update the InvoiceItem amount to match latest usage.
        """
        import asyncio
        import logging

        from backend.database import async_session_factory
        from backend.services.billing import bill_monthly_overages_all

        log = logging.getLogger("wizscheduler.monthly_billing")

        # Wait 60 seconds after startup, then 7 days between runs.
        await asyncio.sleep(60)

        while True:
            try:
                async with async_session_factory() as db:
                    summary = await bill_monthly_overages_all(db)
                log.info("Weekly monthly billing run: %s", summary)
            except Exception as e:
                log.error("Weekly monthly billing failed: %s", e)

            # Sleep 7 days
            await asyncio.sleep(7 * 24 * 60 * 60)
```

Find the line that schedules the daily loop on startup (it's `asyncio.create_task(_daily_storage_snapshot_loop())` or equivalent inside the startup event). Add immediately below it:

```python
        asyncio.create_task(_weekly_monthly_billing_loop())
```

- [ ] **Step 2: Verify backend boots cleanly**

Run: `python -c "import asyncio; from backend.main import app; print('app loaded')"`
Expected: `app loaded` (no exception)

- [ ] **Step 3: Commit**

```bash
git add backend/main.py
git commit -m "feat(billing): weekly background loop for monthly InvoiceItem reconciliation"
```

---

## Task 5: Webhook router skeleton + signature verification

**Files:**
- Create: `backend/routers/webhooks.py`
- Modify: `backend/routers/__init__.py` (currently empty — leave as is)
- Modify: `backend/main.py` (add `include_router` line)
- Modify: `backend/config.py` (add `STRIPE_WEBHOOK_SECRET`)
- Modify: `tests/test_billing.py`

- [ ] **Step 1: Add the config setting**

In `backend/config.py`, add right after `STRIPE_BILLING_PORTAL_RETURN_URL`:

```python
    STRIPE_WEBHOOK_SECRET: str = ""  # whsec_... from Stripe webhook config
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_billing.py`:

```python
async def test_webhook_rejects_unsigned_request(client: AsyncClient):
    response = await client.post(
        "/api/v1/webhooks/stripe",
        content=b'{"type":"invoice.upcoming"}',
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 400
    assert "signature" in response.json()["detail"].lower()


async def test_webhook_rejects_bad_signature(client: AsyncClient, monkeypatch):
    import stripe
    def boom(payload, sig_header, secret):
        raise stripe.SignatureVerificationError("bad sig", sig_header)
    monkeypatch.setattr(stripe.Webhook, "construct_event", boom)
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test")

    response = await client.post(
        "/api/v1/webhooks/stripe",
        content=b'{"type":"invoice.upcoming"}',
        headers={"stripe-signature": "t=1,v1=bogus", "content-type": "application/json"},
    )
    assert response.status_code == 400


async def test_webhook_accepts_unhandled_event(client: AsyncClient, monkeypatch):
    """Events we don't handle return 200 (Stripe expects 2xx for "received")."""
    import stripe
    fake_event = {"type": "customer.created", "data": {"object": {}}}
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda p, s, k: fake_event)
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test")

    response = await client.post(
        "/api/v1/webhooks/stripe",
        content=b'{}',
        headers={"stripe-signature": "t=1,v1=x", "content-type": "application/json"},
    )
    assert response.status_code == 200
```

- [ ] **Step 3: Run tests to verify failure**

Run: `pytest tests/test_billing.py -k webhook -v`
Expected: 404 (route not registered)

- [ ] **Step 4: Implement the webhook router**

Create `backend/routers/webhooks.py`:

```python
"""Stripe webhook receiver.

This endpoint is intentionally unauthenticated — Stripe verifies the request
via the `Stripe-Signature` header instead. The signing secret comes from
settings.STRIPE_WEBHOOK_SECRET (set per-environment via Secrets Manager).
"""
import logging

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.dependencies import get_db

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = logging.getLogger("wizscheduler.stripe_webhook")


@router.post("/stripe", status_code=200)
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    sig_header = request.headers.get("stripe-signature")
    if not sig_header:
        raise HTTPException(status_code=400, detail="Missing Stripe-Signature header")

    if not settings.STRIPE_WEBHOOK_SECRET:
        # Misconfiguration — reject loudly rather than silently accept anything.
        raise HTTPException(status_code=503, detail="Webhook secret is not configured")

    payload = await request.body()
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except (ValueError, stripe.SignatureVerificationError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid signature: {e}")

    event_type = event["type"] if isinstance(event, dict) else event.type
    logger.info("Stripe webhook received: %s", event_type)

    # Handlers added in Tasks 6, 7, 8
    return {"received": True, "type": event_type}
```

- [ ] **Step 5: Register the router**

In `backend/main.py`, add `webhooks` to the existing import block and to the `include_router` calls:

```python
from backend.routers import (
    affinities,
    auth,
    billing,
    company,
    condensed_roles,
    employees,
    export_schedules,
    failure_logs,
    gdpr,
    import_7shifts,
    import_deputy,
    invites,
    locations,
    ownership_group,
    regions,
    roles,
    schedules,
    shift_templates,
    webhooks,
)
```

And at the bottom of the include_router block (after `billing`):

```python
    app.include_router(webhooks.router, prefix=api_prefix)
```

- [ ] **Step 6: Run tests to verify pass**

Run: `pytest tests/test_billing.py -k webhook -v`
Expected: 3 PASS

- [ ] **Step 7: Commit**

```bash
git add backend/routers/webhooks.py backend/main.py backend/config.py tests/test_billing.py
git commit -m "feat(billing): /webhooks/stripe with signature verification + unhandled-event ack"
```

---

## Task 6: Handle `invoice.upcoming`

**Files:**
- Modify: `backend/routers/webhooks.py`
- Modify: `tests/test_billing.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_billing.py`:

```python
async def test_webhook_invoice_upcoming_triggers_recompute(
    client: AsyncClient, db_session, og_with_card, monkeypatch
):
    """invoice.upcoming for an OG's customer fires bill_monthly_overages_for_og."""
    import stripe
    calls = []
    async def fake_runner(db, og, period=None):
        calls.append({"og_id": og.id, "period": period})
        return {"og_id": og.id}
    monkeypatch.setattr(
        "backend.routers.webhooks.bill_monthly_overages_for_og", fake_runner
    )

    fake_event = {
        "type": "invoice.upcoming",
        "data": {"object": {"customer": "cus_test_abc"}},
    }
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda p, s, k: fake_event)
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test")

    response = await client.post(
        "/api/v1/webhooks/stripe",
        content=b'{}',
        headers={"stripe-signature": "t=1,v1=x", "content-type": "application/json"},
    )
    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0]["og_id"] == OG_ID


async def test_webhook_invoice_upcoming_unknown_customer_acks(
    client: AsyncClient, monkeypatch
):
    """invoice.upcoming for a customer we don't know returns 200 (acked, no work)."""
    import stripe
    fake_event = {
        "type": "invoice.upcoming",
        "data": {"object": {"customer": "cus_unknown_xyz"}},
    }
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda p, s, k: fake_event)
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test")

    response = await client.post(
        "/api/v1/webhooks/stripe",
        content=b'{}',
        headers={"stripe-signature": "t=1,v1=x", "content-type": "application/json"},
    )
    assert response.status_code == 200
```

- [ ] **Step 2: Run, verify failure**

Run: `pytest tests/test_billing.py -k "invoice_upcoming" -v`
Expected: AssertionError (handler not yet implemented; `calls` stays empty)

- [ ] **Step 3: Implement the handler**

In `backend/routers/webhooks.py`, add imports at the top:

```python
from sqlalchemy import select

from backend.models.ownership_group import OwnershipGroup
from backend.services.billing import bill_monthly_overages_for_og
```

Replace the bottom of `stripe_webhook` (`return {"received": True, "type": event_type}`) with a dispatch table:

```python
    obj = event["data"]["object"] if isinstance(event, dict) else event.data.object

    if event_type == "invoice.upcoming":
        customer_id = obj.get("customer") if isinstance(obj, dict) else obj.customer
        og = (await db.execute(
            select(OwnershipGroup).where(OwnershipGroup.stripe_customer_id == customer_id)
        )).scalar_one_or_none()
        if og:
            await bill_monthly_overages_for_og(db, og)
        return {"received": True, "type": event_type, "og_id": og.id if og else None}

    return {"received": True, "type": event_type}
```

- [ ] **Step 4: Run, verify pass**

Run: `pytest tests/test_billing.py -k "invoice_upcoming" -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/routers/webhooks.py tests/test_billing.py
git commit -m "feat(billing): invoice.upcoming webhook handler triggers monthly recompute"
```

---

## Task 7: Handle `invoice.payment_failed`

**Files:**
- Modify: `backend/routers/webhooks.py`
- Modify: `tests/test_billing.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_billing.py`:

```python
async def test_webhook_invoice_payment_failed_sets_autoreload_failed_at(
    client: AsyncClient, db_session, og_with_card, monkeypatch
):
    """invoice.payment_failed for our customer sets autoreload_failed_at,
    blocking AI/schedule generation until manual retry."""
    import stripe
    fake_event = {
        "type": "invoice.payment_failed",
        "data": {"object": {"customer": "cus_test_abc"}},
    }
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda p, s, k: fake_event)
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test")

    assert og_with_card.autoreload_failed_at is None

    response = await client.post(
        "/api/v1/webhooks/stripe",
        content=b'{}',
        headers={"stripe-signature": "t=1,v1=x", "content-type": "application/json"},
    )
    assert response.status_code == 200

    await db_session.refresh(og_with_card)
    assert og_with_card.autoreload_failed_at is not None
```

- [ ] **Step 2: Run, verify failure**

Run: `pytest tests/test_billing.py -k payment_failed -v`
Expected: AssertionError on `autoreload_failed_at` being None

- [ ] **Step 3: Implement the handler**

In `backend/routers/webhooks.py`, add to the imports:

```python
from datetime import datetime, timezone
```

In the dispatch logic (after the `invoice.upcoming` branch, before the fallthrough), add:

```python
    if event_type == "invoice.payment_failed":
        customer_id = obj.get("customer") if isinstance(obj, dict) else obj.customer
        og = (await db.execute(
            select(OwnershipGroup).where(OwnershipGroup.stripe_customer_id == customer_id)
        )).scalar_one_or_none()
        if og and og.autoreload_failed_at is None:
            og.autoreload_failed_at = datetime.now(timezone.utc)
            await db.commit()
        return {"received": True, "type": event_type, "og_id": og.id if og else None}
```

- [ ] **Step 4: Run, verify pass**

Run: `pytest tests/test_billing.py -k payment_failed -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/routers/webhooks.py tests/test_billing.py
git commit -m "feat(billing): invoice.payment_failed webhook sets autoreload_failed_at"
```

---

## Task 8: Handle `payment_method.attached`

**Files:**
- Modify: `backend/routers/webhooks.py`
- Modify: `tests/test_billing.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_billing.py`:

```python
async def test_webhook_payment_method_attached_refreshes_cached_pm(
    client: AsyncClient, db_session, og_with_card, monkeypatch
):
    """payment_method.attached for our customer updates default_payment_method_id."""
    import stripe

    fake_event = {
        "type": "payment_method.attached",
        "data": {"object": {"id": "pm_new_card", "customer": "cus_test_abc"}},
    }
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda p, s, k: fake_event)
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test")

    # Stub Subscription.retrieve so cache_default_payment_method finds the new PM.
    fake_sub = MagicMock(default_payment_method="pm_new_card")
    monkeypatch.setattr(stripe.Subscription, "retrieve", lambda sid: fake_sub)

    response = await client.post(
        "/api/v1/webhooks/stripe",
        content=b'{}',
        headers={"stripe-signature": "t=1,v1=x", "content-type": "application/json"},
    )
    assert response.status_code == 200

    await db_session.refresh(og_with_card)
    assert og_with_card.default_payment_method_id == "pm_new_card"
```

- [ ] **Step 2: Run, verify failure**

Run: `pytest tests/test_billing.py::test_webhook_payment_method_attached_refreshes_cached_pm -v`
Expected: AssertionError

- [ ] **Step 3: Implement the handler**

In `backend/routers/webhooks.py`, add the import:

```python
from backend.services.billing import cache_default_payment_method
```

In the dispatch logic (after the `invoice.payment_failed` branch), add:

```python
    if event_type == "payment_method.attached":
        customer_id = obj.get("customer") if isinstance(obj, dict) else obj.customer
        og = (await db.execute(
            select(OwnershipGroup).where(OwnershipGroup.stripe_customer_id == customer_id)
        )).scalar_one_or_none()
        if og:
            await cache_default_payment_method(db, og)
            await db.commit()
        return {"received": True, "type": event_type, "og_id": og.id if og else None}
```

- [ ] **Step 4: Run, verify pass**

Run: `pytest tests/test_billing.py::test_webhook_payment_method_attached_refreshes_cached_pm -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/routers/webhooks.py tests/test_billing.py
git commit -m "feat(billing): payment_method.attached webhook refreshes cached PM ID"
```

---

## Task 9: Augment `/billing/usage` with `pending_invoice_items`

**Files:**
- Modify: `backend/routers/billing.py`
- Modify: `tests/test_billing.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_billing.py`:

```python
async def test_get_usage_includes_pending_invoice_items(
    client: AsyncClient, manager_token, db_session, og_with_card
):
    """GET /billing/usage returns pending_invoice_items derived from BillingCharge."""
    db_session.add(BillingCharge(
        ownership_group_id=OG_ID,
        kind="invoice_item_storage",
        amount_usd=0.5,
        stripe_object_id="ii_1",
        period="2026-05",
        status="pending",
    ))
    db_session.add(BillingCharge(
        ownership_group_id=OG_ID,
        kind="invoice_item_employees",
        amount_usd=1.0,
        stripe_object_id="ii_2",
        period="2026-05",
        status="pending",
    ))
    db_session.add(BillingCharge(
        ownership_group_id=OG_ID,
        kind="autoreload",
        amount_usd=10.0,
        stripe_object_id="pi_1",
        period=None,
        status="succeeded",
    ))
    await db_session.commit()

    response = await client.get(
        "/api/v1/billing/usage",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    items = body.get("pending_invoice_items", [])
    kinds = sorted(it["kind"] for it in items)
    assert kinds == ["invoice_item_employees", "invoice_item_storage"]
    storage = next(it for it in items if it["kind"] == "invoice_item_storage")
    assert storage["amount_usd"] == 0.5
    assert storage["period"] == "2026-05"
```

- [ ] **Step 2: Run, verify failure**

Run: `pytest tests/test_billing.py::test_get_usage_includes_pending_invoice_items -v`
Expected: AssertionError — `pending_invoice_items` not in response

- [ ] **Step 3: Modify `get_usage`**

In `backend/routers/billing.py`, locate the existing `get_usage` function (around line 60 after Task 13 of PR 1 removed the purchase-credits endpoints). After the `summary = await get_full_billing_summary(db, og_id)` call (or after the no-og default return), enrich the response with pending invoice items:

```python
@router.get("/usage")
async def get_usage(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get the full billing summary for the current ownership group.

    Includes LLM usage, storage, employee count charges, and any pending
    InvoiceItems queued for the upcoming subscription invoice.
    """
    og_id = await get_ownership_group_id(db, current_user.company_id)
    if not og_id:
        return {
            "base": {"monthly_usd": settings.BASE_MONTHLY_USD},
            "llm": {"input_tokens": 0, "output_tokens": 0, "raw_cost_usd": 0, "charged_usd": 0,
                     "free_tier_usd": settings.LLM_FREE_TIER_USD, "free_remaining_usd": settings.LLM_FREE_TIER_USD,
                     "is_over_free_tier": False, "overage_markup": settings.LLM_OVERAGE_MARKUP},
            "storage": {"used_gb": 0, "free_gb": settings.STORAGE_FREE_GB, "billable_gb": 0,
                        "cost_per_gb": settings.STORAGE_COST_PER_GB, "charged_usd": 0},
            "employees": {"count": 0, "free_tier": settings.EMPLOYEE_FREE_TIER, "billable": 0,
                          "block_size": settings.EMPLOYEE_BLOCK_SIZE, "cost_per_block": settings.EMPLOYEE_COST_PER_BLOCK, "charged_usd": 0},
            "schedules": {"count": 0, "free_tier": settings.SCHEDULE_FREE_TIER, "billable": 0,
                          "block_size": settings.SCHEDULE_BLOCK_SIZE, "cost_per_block": settings.SCHEDULE_COST_PER_BLOCK, "charged_usd": 0},
            "total_monthly_charge_usd": settings.BASE_MONTHLY_USD,
            "pending_invoice_items": [],
        }

    summary = await get_full_billing_summary(db, og_id)

    rows = (await db.execute(
        select(BillingCharge)
        .where(
            BillingCharge.ownership_group_id == og_id,
            BillingCharge.kind.in_(("invoice_item_storage", "invoice_item_employees")),
            BillingCharge.status == "pending",
        )
        .order_by(BillingCharge.created_at.desc())
    )).scalars()
    summary["pending_invoice_items"] = [
        {"kind": r.kind, "amount_usd": float(r.amount_usd), "period": r.period}
        for r in rows
    ]
    return summary
```

- [ ] **Step 4: Run, verify pass**

Run: `pytest tests/test_billing.py::test_get_usage_includes_pending_invoice_items -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/routers/billing.py tests/test_billing.py
git commit -m "feat(billing): augment /billing/usage with pending_invoice_items"
```

---

## Task 10: Frontend — Pending Monthly Charges panel

**Files:**
- Modify: `frontend/src/api/billing.ts`
- Modify: `frontend/src/pages/manager/Schedule.tsx`
- Modify: `frontend/src/i18n/en.ts` and all 18 other locales

- [ ] **Step 1: Extend the BillingUsage TypeScript type**

In `frontend/src/api/billing.ts`, find the existing usage-related types. Add a new `BillingUsage` interface (if it doesn't already exist) and a `getUsage` function:

```typescript
export interface PendingInvoiceItem {
  kind: "invoice_item_storage" | "invoice_item_employees";
  amount_usd: number;
  period: string;
}

export interface BillingUsage {
  base: { monthly_usd: number };
  llm: {
    input_tokens: number;
    output_tokens: number;
    raw_cost_usd: number;
    charged_usd: number;
    free_tier_usd: number;
    free_remaining_usd: number;
    is_over_free_tier: boolean;
    overage_markup: number;
  };
  storage: {
    used_gb: number;
    free_gb: number;
    billable_gb: number;
    cost_per_gb: number;
    charged_usd: number;
  };
  employees: {
    count: number;
    free_tier: number;
    billable: number;
    block_size: number;
    cost_per_block: number;
    charged_usd: number;
  };
  schedules: {
    count: number;
    free_tier: number;
    billable: number;
    block_size: number;
    cost_per_block: number;
    charged_usd: number;
  };
  total_monthly_charge_usd: number;
  pending_invoice_items: PendingInvoiceItem[];
}

export function getUsage(): Promise<BillingUsage> {
  return apiFetch<BillingUsage>("/billing/usage");
}
```

- [ ] **Step 2: Add new i18n strings to en.ts**

In `frontend/src/i18n/en.ts`, in the `schedule:` block, add:

```typescript
    pendingChargesTitle: "Pending Monthly Charges",
    pendingChargesEmpty: "No additional charges projected this cycle.",
    pendingChargeStorage: "Storage overage",
    pendingChargeEmployees: "Employee overage",
```

- [ ] **Step 3: Add the same keys (English values) to all 18 other locale files**

Run the following from `/Users/robran/IdeaProjects/wiz_scheduler/frontend/src/i18n`:

```bash
python3 - <<'PYEOF'
import re, pathlib
LOCALES = ["ar.ts","bn.ts","de.ts","es.ts","fr.ts","hi.ts","id.ts","ja.ts","mr.ts","pcm.ts","pt.ts","ru.ts","ta.ts","te.ts","tr.ts","ur.ts","vi.ts","zh.ts"]
INSERT = """\
    pendingChargesTitle: "Pending Monthly Charges",
    pendingChargesEmpty: "No additional charges projected this cycle.",
    pendingChargeStorage: "Storage overage",
    pendingChargeEmployees: "Employee overage",
"""
for fname in LOCALES:
    p = pathlib.Path(fname)
    text = p.read_text()
    text = re.sub(r'(    updateCard: "[^"]+",\n)', r'\1' + INSERT, text, count=1)
    p.write_text(text)
    print(f"updated {fname}")
PYEOF
```

(The `updateCard` anchor was added in PR 1. If a future PR removes that key, this anchor needs adjusting before running.)

- [ ] **Step 4: Render the panel in Schedule.tsx**

In `frontend/src/pages/manager/Schedule.tsx`, add a state slot for the usage payload near the other `useState` declarations:

```typescript
import type { BillingUsage } from "../../api/billing";
// ...
const [billingUsage, setBillingUsage] = useState<BillingUsage | null>(null);
```

Add a fetch effect (next to the existing `getAutoReload` effect):

```typescript
useEffect(() => {
  billingApi.getUsage().then(setBillingUsage).catch(() => {});
}, []);
```

In the render tree, immediately after the Auto-Reload Settings Modal block (around the end of the JSX), insert a Pending Charges card. The location: after the closing `</>` of the modal block and before the final `</div>` that wraps the page:

```tsx
{billingUsage && (
  <div className="bg-white shadow rounded-lg p-6 mt-6">
    <h3 className={`text-lg font-semibold mb-3 ${text.heading}`}>
      {t.schedule.pendingChargesTitle}
    </h3>
    {billingUsage.pending_invoice_items.length === 0 ? (
      <p className={`text-sm ${text.muted}`}>{t.schedule.pendingChargesEmpty}</p>
    ) : (
      <ul className="space-y-2">
        {billingUsage.pending_invoice_items.map((it) => (
          <li key={`${it.kind}-${it.period}`} className="flex justify-between text-sm">
            <span>
              {it.kind === "invoice_item_storage"
                ? t.schedule.pendingChargeStorage
                : t.schedule.pendingChargeEmployees}{" "}
              <span className={text.muted}>({it.period})</span>
            </span>
            <span className="font-medium">${it.amount_usd.toFixed(2)}</span>
          </li>
        ))}
      </ul>
    )}
  </div>
)}
```

- [ ] **Step 5: Verify TypeScript compiles**

Run: `cd frontend && ./node_modules/.bin/tsc --noEmit`
Expected: exit 0.

- [ ] **Step 6: Visual smoke test (optional)**

Run: `cd frontend && npm run dev`
Open the Manager Schedule page. Expected to see a "Pending Monthly Charges" card with "No additional charges projected this cycle." (since the test DB has no pending BillingCharge rows).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/billing.ts frontend/src/pages/manager/Schedule.tsx frontend/src/i18n/
git commit -m "feat(billing-fe): Pending Monthly Charges panel + getUsage typed client"
```

---

## Task 11: Final verification + Stripe dashboard wiring + PR

- [ ] **Step 1: Run full pytest**

Run: `pytest tests/ --no-header -q`
Expected: all PASS. If regressions appear, fix without altering production code unless the regression is real.

- [ ] **Step 2: Run full frontend build**

Run: `cd frontend && ./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/vite build`
Expected: clean build with no errors.

- [ ] **Step 3: Document Stripe webhook setup in the PR body**

After deploy, the operator must register the webhook in the Stripe Dashboard:

1. Stripe Dashboard → Developers → Webhooks → **Add endpoint**
2. URL: `https://wizscheduler.com/api/v1/webhooks/stripe`
3. Events to send:
   - `invoice.upcoming`
   - `invoice.payment_failed`
   - `payment_method.attached`
4. Copy the **Signing secret** (`whsec_...`)
5. Set it in AWS Secrets Manager:
   ```bash
   aws secretsmanager create-secret \
     --name wizscheduler/prod/STRIPE_WEBHOOK_SECRET \
     --secret-string 'whsec_...'
   ```
6. (Skipped if you wire it through terraform — see step 4 below.)

- [ ] **Step 4: Wire STRIPE_WEBHOOK_SECRET through terraform**

In `terraform/secrets.tf`, add a secret + version (mirroring `STRIPE_SECRET_KEY`):

```hcl
resource "aws_secretsmanager_secret" "stripe_webhook_secret" {
  name                    = "${var.app_name}/${var.environment}/STRIPE_WEBHOOK_SECRET"
  description             = "Stripe webhook signing secret"
  recovery_window_in_days = 7

  tags = { Name = "${var.app_name}-stripe-webhook-secret" }
}

resource "aws_secretsmanager_secret_version" "stripe_webhook_secret" {
  secret_id     = aws_secretsmanager_secret.stripe_webhook_secret.id
  secret_string = "CHANGE_ME_AFTER_DEPLOY"

  lifecycle {
    ignore_changes = [secret_string]
  }
}
```

In `terraform/ecs.tf`, add the new secret to:
1. The `secrets_access` IAM policy `resources` list (add `aws_secretsmanager_secret.stripe_webhook_secret.arn`).
2. The container `secrets` block (add `{ name = "STRIPE_WEBHOOK_SECRET", valueFrom = aws_secretsmanager_secret.stripe_webhook_secret.arn }`).

- [ ] **Step 5: Final commit (terraform changes only)**

```bash
git add terraform/secrets.tf terraform/ecs.tf
git commit -m "infra(billing): wire STRIPE_WEBHOOK_SECRET through Secrets Manager"
```

- [ ] **Step 6: Push and open the PR**

```bash
git push -u origin <branch>
gh pr create --title "Monthly InvoiceItem billing + Stripe webhooks (PR 2)" --body "$(cat <<'EOF'
## Summary
- Weekly in-process background loop reconciles storage + employee overages as `stripe.InvoiceItem` rows attached to each subscription's upcoming invoice
- Idempotency tracked via the existing `billing_charges` audit table (1:1 with Stripe InvoiceItems, keyed by `(og_id, kind, period)`)
- New `POST /api/v1/webhooks/stripe` endpoint with HMAC signature verification, dispatching:
  - `invoice.upcoming` → recompute that OG's monthly InvoiceItems (defense in depth before the invoice finalizes)
  - `invoice.payment_failed` → set `autoreload_failed_at` to block AI/schedule generation
  - `payment_method.attached` → refresh cached `default_payment_method_id`
- `GET /billing/usage` augmented with `pending_invoice_items` for the frontend Pending Charges panel
- Terraform: new `STRIPE_WEBHOOK_SECRET` in Secrets Manager, wired into the ECS task definition

Design spec: `docs/superpowers/specs/2026-05-11-usage-overage-billing-design.md`
Plan: `docs/superpowers/plans/2026-05-12-monthly-invoice-items-pr2.md`

## Deploy steps
1. Merge → CI deploys new image + applies terraform (creates the new secret with placeholder value)
2. **Add webhook in Stripe Dashboard** — see PR body header in commit `infra(billing)` for exact event list + endpoint URL
3. Set the real signing secret in Secrets Manager:
   ```
   aws secretsmanager put-secret-value \
     --secret-id wizscheduler/prod/STRIPE_WEBHOOK_SECRET \
     --secret-string 'whsec_...'
   ```
4. Force ECS redeploy: `aws ecs update-service --cluster wizscheduler-cluster --service wizscheduler-service --force-new-deployment`
5. Trigger a test event from Stripe Dashboard ("Send test webhook" → `payment_method.attached`) to verify the endpoint is reachable

## Test plan
- [x] N new pytest tests covering the helpers and webhook handlers
- [x] Frontend tsc + vite build pass
- [ ] Post-deploy: verify a Stripe test event lands in CloudWatch logs as `Stripe webhook received: <type>`
- [ ] Post-deploy: wait for the next subscription period; verify monthly InvoiceItem appears on the upcoming invoice when storage/employees exceed free tier

## Notable choices
- Weekly background loop modeled on the existing `record_storage_snapshots` daily loop (in-process, no EventBridge dependency). Webhook serves as the safeguard before invoice close.
- `BillingCharge.status = 'pending'` for invoice items until the parent invoice finalizes. PR 3 (if needed) can transition them to `succeeded` on `invoice.payment_succeeded`.
- Inactive subscriptions are skipped — we don't dirty the audit log for `past_due` / `canceled` / etc.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review Notes

This plan covers every PR 2 requirement listed in `docs/superpowers/specs/2026-05-11-usage-overage-billing-design.md`:
- Monthly track compute helper: Task 1
- Per-OG runner with idempotency via `BillingCharge`: Task 2
- All-OG iterator: Task 3
- Weekly background loop: Task 4
- Webhook router + signature verification: Task 5
- `invoice.upcoming` handler: Task 6
- `invoice.payment_failed` handler: Task 7
- `payment_method.attached` handler: Task 8
- Augmented `/billing/usage` for frontend: Task 9
- Frontend Pending Charges panel + i18n: Task 10
- Stripe dashboard wiring + terraform plumbing: Task 11

Out of scope (matches spec):
- Refunds / disputes (manual via Stripe dashboard)
- `invoice.payment_succeeded` handler transitioning BillingCharge status (left as a future enhancement)
- ECS EventBridge scheduled task (chose in-process loop for consistency with existing storage-snapshot pattern)
- Customer-facing spend caps / hard-block on monthly spend ceiling
