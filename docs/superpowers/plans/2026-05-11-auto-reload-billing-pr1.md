# PR 1: Auto-Reload Billing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the manual "Purchase AI Credits" flow with an auto-reloading prepaid balance that charges the customer's saved Stripe card in real-time when AI/schedule overage drops their `ai_credits_usd` below a threshold.

**Architecture:** Reuses the existing `OwnershipGroup.ai_credits_usd` field as a real-time buffer. Two new columns on `ownership_groups` (autoreload settings + failure state), one new `billing_charges` audit table. A new `auto_reload_if_needed()` helper in `services/billing.py` is wrapped around the existing `deduct_credits_*` debit paths. Off-session Stripe `PaymentIntent` API is used to charge the card synchronously before each overage debit. Failed charges block AI/schedule generation until manually retried.

**Tech Stack:** FastAPI · SQLAlchemy 2.x async · Alembic · `stripe` Python SDK · React 18 · TypeScript · Vite · pytest-asyncio · httpx

**Spec:** `docs/superpowers/specs/2026-05-11-usage-overage-billing-design.md`

**Out of scope for this PR:** Monthly InvoiceItems for storage/employees, Stripe webhooks, manual top-up endpoint, refunds/disputes. Those land in PR 2.

---

## Task 1: Database migration

**Files:**
- Create: `backend/alembic/versions/0021_add_billing_overage_columns.py`

- [ ] **Step 1: Write the migration**

Create `backend/alembic/versions/0021_add_billing_overage_columns.py` with:

```python
"""Add auto-reload columns to ownership_groups and create billing_charges table.

Adds the columns needed for the auto-reload prepaid balance flow:
- autoreload_enabled, autoreload_threshold_usd, autoreload_amount_usd: per-OG settings
- autoreload_failed_at: timestamp set when an auto-reload PaymentIntent fails;
  blocks further AI/schedule generation until cleared by a successful retry
- default_payment_method_id: cached from the customer's Stripe subscription
  so off-session PaymentIntents don't need to round-trip Stripe each time

The billing_charges table is an audit log + idempotency record for every charge
or invoice item the backend produces. PR 1 only writes `autoreload` rows; PR 2
adds `invoice_item_*` rows.

Revision ID: 0021
Revises: 0020
Create Date: 2026-05-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ownership_groups",
        sa.Column("autoreload_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "ownership_groups",
        sa.Column("autoreload_threshold_usd", sa.Numeric(10, 4), nullable=False, server_default=sa.text("2.0")),
    )
    op.add_column(
        "ownership_groups",
        sa.Column("autoreload_amount_usd", sa.Numeric(10, 4), nullable=False, server_default=sa.text("10.0")),
    )
    op.add_column(
        "ownership_groups",
        sa.Column("autoreload_failed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ownership_groups",
        sa.Column("default_payment_method_id", sa.String(), nullable=True),
    )

    op.create_table(
        "billing_charges",
        sa.Column("id", sa.String(8), primary_key=True),
        sa.Column("ownership_group_id", sa.String(8), sa.ForeignKey("ownership_groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("amount_usd", sa.Numeric(10, 4), nullable=False),
        sa.Column("stripe_object_id", sa.String(), nullable=True),
        sa.Column("period", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "kind IN ('autoreload', 'invoice_item_storage', 'invoice_item_employees')",
            name="billing_charges_kind_check",
        ),
        sa.CheckConstraint(
            "status IN ('succeeded', 'failed', 'pending')",
            name="billing_charges_status_check",
        ),
    )
    op.create_index(
        "ix_billing_charges_og_kind_period",
        "billing_charges",
        ["ownership_group_id", "kind", "period"],
    )
    op.create_index(
        "ix_billing_charges_og_created",
        "billing_charges",
        ["ownership_group_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_billing_charges_og_created", table_name="billing_charges")
    op.drop_index("ix_billing_charges_og_kind_period", table_name="billing_charges")
    op.drop_table("billing_charges")
    op.drop_column("ownership_groups", "default_payment_method_id")
    op.drop_column("ownership_groups", "autoreload_failed_at")
    op.drop_column("ownership_groups", "autoreload_amount_usd")
    op.drop_column("ownership_groups", "autoreload_threshold_usd")
    op.drop_column("ownership_groups", "autoreload_enabled")
```

- [ ] **Step 2: Apply migration locally**

Run: `cd backend && alembic upgrade head`
Expected: `Running upgrade 0020 -> 0021, Add auto-reload columns ...`

- [ ] **Step 3: Verify schema**

Run: `cd backend && psql $DATABASE_URL -c "\d ownership_groups" -c "\d billing_charges"` (or equivalent on your dev DB).
Expected: see the five new columns on `ownership_groups`, and the `billing_charges` table with the two indexes.

- [ ] **Step 4: Test downgrade is clean**

Run: `cd backend && alembic downgrade 0020 && alembic upgrade head`
Expected: both succeed without error.

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/0021_add_billing_overage_columns.py
git commit -m "feat(billing): add auto-reload columns + billing_charges audit table"
```

---

## Task 2: BillingCharge model + OwnershipGroup columns

**Files:**
- Create: `backend/models/billing_charge.py`
- Modify: `backend/models/__init__.py`
- Modify: `backend/models/ownership_group.py`

- [ ] **Step 1: Create the BillingCharge model**

Create `backend/models/billing_charge.py`:

```python
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class BillingCharge(Base):
    __tablename__ = "billing_charges"

    id: Mapped[str] = mapped_column(String(8), primary_key=True, default=generate_short_id)
    ownership_group_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("ownership_groups.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String, nullable=False)
    amount_usd: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    stripe_object_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    period: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
```

- [ ] **Step 2: Register the new model**

In `backend/models/__init__.py`, add the import below the existing `StorageSnapshot` import and append to `__all__`:

```python
from backend.models.billing_charge import BillingCharge
```

Append `"BillingCharge"` to the `__all__` list.

- [ ] **Step 3: Add new columns to OwnershipGroup**

In `backend/models/ownership_group.py`, change the imports at the top to include `Boolean` and `Numeric`:

```python
from sqlalchemy import Boolean, DateTime, Float, Numeric, String, text
```

Add these mappings inside `class OwnershipGroup(Base):`, between `ai_credits_usd` and `api_integration`:

```python
    autoreload_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    autoreload_threshold_usd: Mapped[float] = mapped_column(
        Numeric(10, 4), nullable=False, server_default=text("2.0")
    )
    autoreload_amount_usd: Mapped[float] = mapped_column(
        Numeric(10, 4), nullable=False, server_default=text("10.0")
    )
    autoreload_failed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    default_payment_method_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
```

- [ ] **Step 4: Write a smoke test**

Append to `tests/test_billing.py` (before the existing `# Helpers` section):

```python
async def test_billing_charge_model_round_trips(db_session: AsyncSession, seed_og):
    """BillingCharge inserts and reads back via SQLAlchemy."""
    from backend.models.billing_charge import BillingCharge

    charge = BillingCharge(
        ownership_group_id=OG_ID,
        kind="autoreload",
        amount_usd=10.0,
        stripe_object_id="pi_test_123",
        status="succeeded",
    )
    db_session.add(charge)
    await db_session.commit()

    from sqlalchemy import select
    result = await db_session.execute(
        select(BillingCharge).where(BillingCharge.ownership_group_id == OG_ID)
    )
    rows = list(result.scalars())
    assert len(rows) == 1
    assert rows[0].kind == "autoreload"
    assert float(rows[0].amount_usd) == 10.0
    assert rows[0].status == "succeeded"
```

- [ ] **Step 5: Run the test**

Run: `pytest tests/test_billing.py::test_billing_charge_model_round_trips -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/models/billing_charge.py backend/models/__init__.py backend/models/ownership_group.py tests/test_billing.py
git commit -m "feat(billing): add BillingCharge model + auto-reload columns on OwnershipGroup"
```

---

## Task 3: Auto-reload config defaults

**Files:**
- Modify: `backend/config.py`

- [ ] **Step 1: Add three settings**

In `backend/config.py`, after the `# Base subscription` block (line ~62, after `BASE_MONTHLY_USD`), add:

```python
    # Auto-reload (real-time billing for AI + schedules)
    AUTORELOAD_DEFAULT_ENABLED: bool = True
    AUTORELOAD_DEFAULT_THRESHOLD_USD: float = 2.0
    AUTORELOAD_DEFAULT_AMOUNT_USD: float = 10.0
```

- [ ] **Step 2: Verify settings load**

Run: `cd backend && python -c "from backend.config import settings; print(settings.AUTORELOAD_DEFAULT_THRESHOLD_USD, settings.AUTORELOAD_DEFAULT_AMOUNT_USD)"`
Expected: `2.0 10.0`

- [ ] **Step 3: Commit**

```bash
git add backend/config.py
git commit -m "feat(billing): add auto-reload default settings"
```

---

## Task 4: cache_default_payment_method helper

**Files:**
- Modify: `backend/services/billing.py`
- Modify: `tests/test_billing.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_billing.py`:

```python
async def test_cache_default_payment_method_writes_pm_id(
    db_session: AsyncSession, seed_og, monkeypatch
):
    """cache_default_payment_method retrieves the subscription's default PM and stores it on the OG."""
    from unittest.mock import MagicMock
    import stripe

    # Mock the OG having a Stripe subscription_id
    seed_og.stripe_subscription_id = "sub_test_123"
    await db_session.commit()

    fake_sub = MagicMock()
    fake_sub.default_payment_method = "pm_test_card_456"
    monkeypatch.setattr(stripe.Subscription, "retrieve", lambda sid: fake_sub)

    from backend.services.billing import cache_default_payment_method
    pm_id = await cache_default_payment_method(db_session, seed_og)

    assert pm_id == "pm_test_card_456"
    await db_session.refresh(seed_og)
    assert seed_og.default_payment_method_id == "pm_test_card_456"


async def test_cache_default_payment_method_no_subscription_returns_none(
    db_session: AsyncSession, seed_og
):
    """If the OG has no stripe_subscription_id, return None without calling Stripe."""
    from backend.services.billing import cache_default_payment_method
    result = await cache_default_payment_method(db_session, seed_og)
    assert result is None
```

- [ ] **Step 2: Run tests, verify failure**

Run: `pytest tests/test_billing.py::test_cache_default_payment_method_writes_pm_id tests/test_billing.py::test_cache_default_payment_method_no_subscription_returns_none -v`
Expected: FAIL with `ImportError: cannot import name 'cache_default_payment_method' from 'backend.services.billing'`

- [ ] **Step 3: Implement the helper**

Append to `backend/services/billing.py` (after the existing `_get_company_ids_for_group` helper):

```python
# ---------------------------------------------------------------------------
# Stripe payment-method caching
# ---------------------------------------------------------------------------

async def cache_default_payment_method(
    db: AsyncSession,
    og: OwnershipGroup,
) -> str | None:
    """Fetch the subscription's default payment method and cache it on the OG.

    Returns the payment method ID, or None if the OG has no subscription.
    Idempotent: re-runs are safe and refresh the cached value.
    """
    if not og.stripe_subscription_id:
        return None

    import stripe
    from backend.config import settings
    stripe.api_key = settings.STRIPE_SECRET_KEY

    sub = stripe.Subscription.retrieve(og.stripe_subscription_id)
    pm_id = sub.default_payment_method
    if pm_id:
        og.default_payment_method_id = pm_id
        await db.flush()
    return pm_id
```

Add the import at the top of `backend/services/billing.py` (near the other model imports):

```python
from backend.models.ownership_group import OwnershipGroup
```

(If it's already imported there, leave it.)

- [ ] **Step 4: Run tests, verify passing**

Run: `pytest tests/test_billing.py::test_cache_default_payment_method_writes_pm_id tests/test_billing.py::test_cache_default_payment_method_no_subscription_returns_none -v`
Expected: both PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/billing.py tests/test_billing.py
git commit -m "feat(billing): cache_default_payment_method helper"
```

---

## Task 5: auto_reload_if_needed core helper

**Files:**
- Modify: `backend/services/billing.py`
- Modify: `tests/test_billing.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_billing.py`:

```python
from unittest.mock import MagicMock, patch


@pytest_asyncio.fixture
async def og_with_card(db_session: AsyncSession, seed_og):
    """OG with stripe_customer_id and a cached payment method."""
    seed_og.stripe_customer_id = "cus_test_abc"
    seed_og.stripe_subscription_id = "sub_test_123"
    seed_og.default_payment_method_id = "pm_test_card_456"
    await db_session.commit()
    return seed_og


async def test_auto_reload_charges_card_and_adds_to_balance(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    """Successful PaymentIntent should add autoreload_amount_usd to ai_credits_usd
    and record a BillingCharge row with status='succeeded'."""
    import stripe
    from backend.services.billing import auto_reload_if_needed
    from backend.models.billing_charge import BillingCharge
    from sqlalchemy import select

    fake_intent = MagicMock(status="succeeded", id="pi_test_999")
    monkeypatch.setattr(stripe.PaymentIntent, "create", lambda **kwargs: fake_intent)

    og_with_card.ai_credits_usd = 0.0
    og_with_card.autoreload_amount_usd = 10.0
    og_with_card.autoreload_threshold_usd = 2.0
    await db_session.commit()

    # cost > balance, so reload should fire
    await auto_reload_if_needed(db_session, og_with_card, cost_usd=5.0)

    await db_session.refresh(og_with_card)
    assert og_with_card.ai_credits_usd == 10.0
    assert og_with_card.autoreload_failed_at is None

    result = await db_session.execute(select(BillingCharge).where(BillingCharge.ownership_group_id == OG_ID))
    charges = list(result.scalars())
    assert len(charges) == 1
    assert charges[0].kind == "autoreload"
    assert charges[0].status == "succeeded"
    assert charges[0].stripe_object_id == "pi_test_999"


async def test_auto_reload_skipped_when_balance_sufficient(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    """If balance > cost + threshold, no Stripe call and no charge row."""
    import stripe
    from backend.services.billing import auto_reload_if_needed
    from backend.models.billing_charge import BillingCharge
    from sqlalchemy import select

    called = {"count": 0}
    def fake_create(**kwargs):
        called["count"] += 1
        return MagicMock(status="succeeded", id="pi_x")
    monkeypatch.setattr(stripe.PaymentIntent, "create", fake_create)

    og_with_card.ai_credits_usd = 20.0
    og_with_card.autoreload_threshold_usd = 2.0
    await db_session.commit()

    await auto_reload_if_needed(db_session, og_with_card, cost_usd=1.0)
    assert called["count"] == 0

    result = await db_session.execute(select(BillingCharge).where(BillingCharge.ownership_group_id == OG_ID))
    assert list(result.scalars()) == []


async def test_auto_reload_failure_sets_failed_at_and_raises(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    """A Stripe CardError on PaymentIntent.create should set autoreload_failed_at,
    record a 'failed' BillingCharge, and raise."""
    import stripe
    from backend.services.billing import auto_reload_if_needed, AutoReloadError
    from backend.models.billing_charge import BillingCharge
    from sqlalchemy import select

    def fake_create(**kwargs):
        raise stripe.CardError("card declined", "card_declined", "card_declined")
    monkeypatch.setattr(stripe.PaymentIntent, "create", fake_create)

    og_with_card.ai_credits_usd = 0.0
    await db_session.commit()

    with pytest.raises(AutoReloadError):
        await auto_reload_if_needed(db_session, og_with_card, cost_usd=5.0)

    await db_session.refresh(og_with_card)
    assert og_with_card.autoreload_failed_at is not None
    assert og_with_card.ai_credits_usd == 0.0  # nothing added

    result = await db_session.execute(select(BillingCharge).where(BillingCharge.ownership_group_id == OG_ID))
    charges = list(result.scalars())
    assert len(charges) == 1
    assert charges[0].status == "failed"


async def test_auto_reload_disabled_raises_blocked_error(
    db_session: AsyncSession, og_with_card
):
    """When autoreload_enabled=False and a reload is needed, raise AutoReloadDisabled."""
    from backend.services.billing import auto_reload_if_needed, AutoReloadDisabled

    og_with_card.autoreload_enabled = False
    og_with_card.ai_credits_usd = 0.0
    await db_session.commit()

    with pytest.raises(AutoReloadDisabled):
        await auto_reload_if_needed(db_session, og_with_card, cost_usd=5.0)


async def test_auto_reload_failed_state_raises_blocked_error(
    db_session: AsyncSession, og_with_card
):
    """When autoreload_failed_at is set, every call raises AutoReloadBlocked."""
    from backend.services.billing import auto_reload_if_needed, AutoReloadBlocked

    og_with_card.autoreload_failed_at = datetime.now(timezone.utc)
    og_with_card.ai_credits_usd = 0.0
    await db_session.commit()

    with pytest.raises(AutoReloadBlocked):
        await auto_reload_if_needed(db_session, og_with_card, cost_usd=5.0)
```

- [ ] **Step 2: Run tests, verify failure**

Run: `pytest tests/test_billing.py -k auto_reload -v`
Expected: 5 errors with `ImportError: cannot import name 'auto_reload_if_needed' ...`

- [ ] **Step 3: Implement the helper**

Append to `backend/services/billing.py`:

```python
# ---------------------------------------------------------------------------
# Auto-reload (real-time billing buffer for AI + schedules)
# ---------------------------------------------------------------------------

class AutoReloadError(Exception):
    """Charge attempt against the saved card failed (declined, SCA, etc.)."""


class AutoReloadDisabled(Exception):
    """OG has autoreload_enabled=False and balance is insufficient."""


class AutoReloadBlocked(Exception):
    """OG has a sticky autoreload_failed_at and must be manually retried."""


async def auto_reload_if_needed(
    db: AsyncSession,
    og: OwnershipGroup,
    cost_usd: float,
) -> None:
    """Charge the customer's saved card if needed to keep balance above threshold.

    Called by debit paths (AI overage, schedule overage) BEFORE the debit is applied.
    Raises if the customer is in a non-chargeable state — callers should propagate
    the error as HTTP 402 to block the operation.

    Side effects:
        - On success: increments og.ai_credits_usd by autoreload_amount_usd,
          writes a 'succeeded' BillingCharge row.
        - On Stripe failure: sets og.autoreload_failed_at = now(),
          writes a 'failed' BillingCharge row, then raises AutoReloadError.
    """
    from datetime import datetime, timezone
    import stripe
    from backend.config import settings
    from backend.models.billing_charge import BillingCharge

    if og.autoreload_failed_at is not None:
        raise AutoReloadBlocked(
            f"Billing on hold since {og.autoreload_failed_at.isoformat()}; "
            "retry payment in the Billing UI."
        )

    threshold = float(og.autoreload_threshold_usd)
    if float(og.ai_credits_usd) - cost_usd >= threshold:
        return  # balance after debit will still be above threshold

    if not og.autoreload_enabled:
        raise AutoReloadDisabled(
            "Auto-reload is disabled and the included AI/schedule quota is exhausted."
        )

    if not og.stripe_customer_id or not og.default_payment_method_id:
        raise AutoReloadError(
            "Customer has no payment method on file. Add a card to enable auto-reload."
        )

    stripe.api_key = settings.STRIPE_SECRET_KEY
    reload_amount_usd = float(og.autoreload_amount_usd)

    try:
        intent = stripe.PaymentIntent.create(
            customer=og.stripe_customer_id,
            amount=int(reload_amount_usd * 100),
            currency="usd",
            payment_method=og.default_payment_method_id,
            off_session=True,
            confirm=True,
            metadata={"og_id": og.id, "kind": "autoreload"},
        )
    except stripe.StripeError as e:
        og.autoreload_failed_at = datetime.now(timezone.utc)
        db.add(BillingCharge(
            ownership_group_id=og.id,
            kind="autoreload",
            amount_usd=reload_amount_usd,
            stripe_object_id=None,
            status="failed",
            error_message=str(e),
        ))
        await db.flush()
        raise AutoReloadError(str(e))

    if intent.status != "succeeded":
        og.autoreload_failed_at = datetime.now(timezone.utc)
        db.add(BillingCharge(
            ownership_group_id=og.id,
            kind="autoreload",
            amount_usd=reload_amount_usd,
            stripe_object_id=intent.id,
            status="failed",
            error_message=f"PaymentIntent status={intent.status}",
        ))
        await db.flush()
        raise AutoReloadError(f"PaymentIntent status: {intent.status}")

    og.ai_credits_usd = round(float(og.ai_credits_usd) + reload_amount_usd, 4)
    db.add(BillingCharge(
        ownership_group_id=og.id,
        kind="autoreload",
        amount_usd=reload_amount_usd,
        stripe_object_id=intent.id,
        status="succeeded",
    ))
    await db.flush()
```

- [ ] **Step 4: Run tests, verify pass**

Run: `pytest tests/test_billing.py -k auto_reload -v`
Expected: 5 passes

- [ ] **Step 5: Commit**

```bash
git add backend/services/billing.py tests/test_billing.py
git commit -m "feat(billing): auto_reload_if_needed core helper"
```

---

## Task 6: Wire auto_reload into check_and_record_usage

**Files:**
- Modify: `backend/services/billing.py`
- Modify: `tests/test_billing.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_billing.py`:

```python
async def test_check_and_record_usage_triggers_reload_when_over_free_tier(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    """When LLM usage takes balance below threshold, auto_reload_if_needed is called
    BEFORE the debit, and the debit succeeds against the topped-up balance."""
    import stripe
    from backend.services.billing import check_and_record_usage
    from backend.models.billing_charge import BillingCharge
    from sqlalchemy import select

    fake_intent = MagicMock(status="succeeded", id="pi_reload_1")
    monkeypatch.setattr(stripe.PaymentIntent, "create", lambda **kw: fake_intent)

    # Pre-exhaust the free tier so any usage triggers a charge
    og_with_card.ai_credits_usd = 0.0
    await db_session.commit()
    usage = TokenUsage(
        ownership_group_id=OG_ID,
        year=datetime.now(timezone.utc).year,
        month=datetime.now(timezone.utc).month,
        input_tokens=1_000_000_000,  # absurd to ensure cost > free tier
        output_tokens=0,
        total_tokens=1_000_000_000,
        cost_usd=settings.LLM_FREE_TIER_USD + 5.0,
        charged_usd=5.0,
    )
    db_session.add(usage)
    await db_session.commit()

    # This call should trigger auto-reload before debit
    result = await check_and_record_usage(db_session, str(COMPANY_ID), 1_000_000, 0)

    await db_session.refresh(og_with_card)
    assert og_with_card.ai_credits_usd > 0  # reload happened, then debit applied
    charges = list((await db_session.execute(select(BillingCharge))).scalars())
    assert any(c.kind == "autoreload" and c.status == "succeeded" for c in charges)
```

- [ ] **Step 2: Run test, verify failure**

Run: `pytest tests/test_billing.py::test_check_and_record_usage_triggers_reload_when_over_free_tier -v`
Expected: FAIL — likely because the existing function deducts without calling `auto_reload_if_needed`.

- [ ] **Step 3: Modify check_and_record_usage**

In `backend/services/billing.py`, locate the existing `check_and_record_usage` function. After the `this_charge` is computed but BEFORE the `usage.charged_usd += this_charge` line (or before `db.add(usage)` in the else branch), add:

```python
    # If this usage will be charged AND the OG has insufficient balance,
    # auto-reload the prepaid buffer before recording the debit.
    if this_charge > 0:
        from sqlalchemy import select
        og_result = await db.execute(
            select(OwnershipGroup).where(OwnershipGroup.id == og_id).with_for_update()
        )
        og = og_result.scalar_one()
        await auto_reload_if_needed(db, og, cost_usd=this_charge)
```

(Place this immediately after the `if usage:` / `else:` branches converge — pick a single shared insertion point near the top of the function before any mutation. The exact location: in the version reflected at `backend/services/billing.py` lines 90-135, add the block right before `await db.flush()` near line 133.)

- [ ] **Step 4: Run test, verify pass**

Run: `pytest tests/test_billing.py::test_check_and_record_usage_triggers_reload_when_over_free_tier -v`
Expected: PASS

- [ ] **Step 5: Verify no regression in existing usage tests**

Run: `pytest tests/test_billing.py -v`
Expected: all pre-existing usage tests still pass (some may now require a fixture with a payment method — adjust those fixtures if needed; the existing `seed_og` has no card, so those tests cover the "no card" path, which produces `AutoReloadError`).

If existing tests like `test_check_and_record_usage_over_free_tier` now fail because they hit the new code path, set `og.autoreload_enabled = False` in the test's setup so the legacy flow (no reload) is preserved for that test.

- [ ] **Step 6: Commit**

```bash
git add backend/services/billing.py tests/test_billing.py
git commit -m "feat(billing): wire auto-reload into check_and_record_usage"
```

---

## Task 7: Wire auto_reload into deduct_credits_for_schedule_overage

**Files:**
- Modify: `backend/services/billing.py`
- Modify: `tests/test_billing.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_billing.py`:

```python
async def test_deduct_credits_for_schedule_triggers_reload(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    """Schedule overage debit triggers auto-reload when balance is insufficient."""
    import stripe
    from backend.services.billing import deduct_credits_for_schedule_overage
    from backend.models import ShiftSchedule
    from sqlalchemy import select

    fake_intent = MagicMock(status="succeeded", id="pi_sched_reload")
    monkeypatch.setattr(stripe.PaymentIntent, "create", lambda **kw: fake_intent)

    og_with_card.ai_credits_usd = 0.0
    await db_session.commit()

    # Create > free tier schedules so overage applies
    now = datetime.now(timezone.utc)
    for i in range(settings.SCHEDULE_FREE_TIER + 1):
        db_session.add(ShiftSchedule(
            company_id=COMPANY_ID,
            location_id=_id(),
            week_start_date=now.date(),
            status="DRAFT",
            created_at=now,
        ))
    await db_session.commit()

    await deduct_credits_for_schedule_overage(db_session, str(COMPANY_ID))

    await db_session.refresh(og_with_card)
    assert og_with_card.ai_credits_usd > 0  # reload happened
```

- [ ] **Step 2: Run test, verify failure**

Run: `pytest tests/test_billing.py::test_deduct_credits_for_schedule_triggers_reload -v`
Expected: FAIL — reload not yet wired.

- [ ] **Step 3: Modify deduct_credits_for_schedule_overage**

In `backend/services/billing.py`, locate `deduct_credits_for_schedule_overage`. Replace its body with:

```python
async def deduct_credits_for_schedule_overage(
    db: AsyncSession,
    company_id: str,
) -> None:
    """Deduct purchased credits for schedule overage after generation."""
    og_id = await get_ownership_group_id(db, company_id)
    if not og_id:
        return

    schedule_count = await count_schedules_this_month(db, og_id)
    if schedule_count <= settings.SCHEDULE_FREE_TIER:
        return

    per_schedule_cost = settings.SCHEDULE_COST_PER_BLOCK / settings.SCHEDULE_BLOCK_SIZE

    from sqlalchemy import select
    og_result = await db.execute(
        select(OwnershipGroup).where(OwnershipGroup.id == og_id).with_for_update()
    )
    og = og_result.scalar_one_or_none()
    if not og:
        return

    await auto_reload_if_needed(db, og, cost_usd=per_schedule_cost)
    og.ai_credits_usd = round(float(og.ai_credits_usd) - per_schedule_cost, 4)
    await db.flush()
```

- [ ] **Step 4: Run test, verify pass**

Run: `pytest tests/test_billing.py::test_deduct_credits_for_schedule_triggers_reload -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/billing.py tests/test_billing.py
git commit -m "feat(billing): wire auto-reload into deduct_credits_for_schedule_overage"
```

---

## Task 8: Block generation when autoreload_failed_at is set

**Files:**
- Modify: `backend/services/billing.py`
- Modify: `tests/test_billing.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_billing.py`:

```python
async def test_check_ai_credits_blocked_when_failed_at_set(
    db_session: AsyncSession, og_with_card
):
    from backend.services.billing import check_ai_credits

    og_with_card.autoreload_failed_at = datetime.now(timezone.utc)
    await db_session.commit()

    status = await check_ai_credits(db_session, str(COMPANY_ID))
    assert status["can_generate"] is False
    assert status.get("autoreload_failed") is True


async def test_check_schedule_quota_blocked_when_failed_at_set(
    db_session: AsyncSession, og_with_card
):
    from backend.services.billing import check_schedule_quota

    og_with_card.autoreload_failed_at = datetime.now(timezone.utc)
    await db_session.commit()

    status = await check_schedule_quota(db_session, str(COMPANY_ID))
    assert status["can_generate"] is False
    assert status.get("autoreload_failed") is True
```

- [ ] **Step 2: Run, verify failure**

Run: `pytest tests/test_billing.py -k "blocked_when_failed_at_set" -v`
Expected: FAIL with assertion errors.

- [ ] **Step 3: Modify check_ai_credits and check_schedule_quota**

In `backend/services/billing.py`, modify `check_ai_credits`. After resolving `og_id` and fetching the `OwnershipGroup` row, check `autoreload_failed_at` and short-circuit:

```python
    # Load OG to check autoreload state
    from sqlalchemy import select
    og_result = await db.execute(select(OwnershipGroup).where(OwnershipGroup.id == og_id))
    og = og_result.scalar_one_or_none()
    if og and og.autoreload_failed_at is not None:
        return {
            "can_generate": False,
            "free_remaining_usd": 0.0,
            "purchased_credits_usd": float(og.ai_credits_usd),
            "is_over_free_tier": True,
            "monthly_cost_usd": 0.0,
            "autoreload_failed": True,
        }
```

Apply the same pattern to `check_schedule_quota` near the existing `og_result = await db.execute(...)` block — add the `autoreload_failed` short-circuit return.

- [ ] **Step 4: Run, verify pass**

Run: `pytest tests/test_billing.py -k "blocked_when_failed_at_set" -v`
Expected: 2 PASS

- [ ] **Step 5: Run all billing tests for regressions**

Run: `pytest tests/test_billing.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/services/billing.py tests/test_billing.py
git commit -m "feat(billing): block AI/schedule generation when autoreload_failed_at is set"
```

---

## Task 9: API — GET/PUT /billing/autoreload

**Files:**
- Modify: `backend/routers/billing.py`
- Modify: `tests/test_billing.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_billing.py`:

```python
async def test_get_autoreload_returns_settings_and_balance(
    client: AsyncClient, manager_token, db_session, og_with_card
):
    response = await client.get(
        "/api/v1/billing/autoreload",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["threshold_usd"] == 2.0
    assert body["amount_usd"] == 10.0
    assert "current_balance_usd" in body
    assert body["failed_at"] is None


async def test_put_autoreload_updates_settings(
    client: AsyncClient, manager_token, db_session, og_with_card
):
    response = await client.put(
        "/api/v1/billing/autoreload",
        json={"enabled": True, "threshold_usd": 5.0, "amount_usd": 25.0},
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["threshold_usd"] == 5.0
    assert body["amount_usd"] == 25.0

    await db_session.refresh(og_with_card)
    assert float(og_with_card.autoreload_threshold_usd) == 5.0
    assert float(og_with_card.autoreload_amount_usd) == 25.0


async def test_put_autoreload_rejects_invalid_values(
    client: AsyncClient, manager_token, og_with_card
):
    response = await client.put(
        "/api/v1/billing/autoreload",
        json={"amount_usd": 0.25},  # below Stripe's $0.50 minimum
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert response.status_code == 422
```

- [ ] **Step 2: Run, verify failure**

Run: `pytest tests/test_billing.py -k autoreload -v`
Expected: 404 / route-not-found errors.

- [ ] **Step 3: Add the endpoints**

Append to `backend/routers/billing.py`:

```python
from pydantic import Field
from sqlalchemy import select
from backend.models.ownership_group import OwnershipGroup
from backend.services.billing import get_ownership_group_id


class AutoReloadStatus(BaseModel):
    enabled: bool
    threshold_usd: float
    amount_usd: float
    current_balance_usd: float
    failed_at: str | None


class AutoReloadUpdate(BaseModel):
    enabled: bool | None = None
    threshold_usd: float | None = Field(default=None, ge=0.5)
    amount_usd: float | None = Field(default=None, ge=0.5)


async def _load_og(db: AsyncSession, current_user: User) -> OwnershipGroup:
    og_id = await get_ownership_group_id(db, str(current_user.company_id))
    if not og_id:
        raise HTTPException(status_code=404, detail="No ownership group found")
    result = await db.execute(select(OwnershipGroup).where(OwnershipGroup.id == og_id))
    og = result.scalar_one_or_none()
    if not og:
        raise HTTPException(status_code=404, detail="Ownership group not found")
    return og


@router.get("/autoreload", response_model=AutoReloadStatus)
async def get_autoreload(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> AutoReloadStatus:
    og = await _load_og(db, current_user)
    return AutoReloadStatus(
        enabled=og.autoreload_enabled,
        threshold_usd=float(og.autoreload_threshold_usd),
        amount_usd=float(og.autoreload_amount_usd),
        current_balance_usd=float(og.ai_credits_usd),
        failed_at=og.autoreload_failed_at.isoformat() if og.autoreload_failed_at else None,
    )


@router.put("/autoreload", response_model=AutoReloadStatus)
async def update_autoreload(
    body: AutoReloadUpdate,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> AutoReloadStatus:
    og = await _load_og(db, current_user)
    if body.enabled is not None:
        og.autoreload_enabled = body.enabled
    if body.threshold_usd is not None:
        og.autoreload_threshold_usd = body.threshold_usd
    if body.amount_usd is not None:
        og.autoreload_amount_usd = body.amount_usd
    await db.commit()
    return AutoReloadStatus(
        enabled=og.autoreload_enabled,
        threshold_usd=float(og.autoreload_threshold_usd),
        amount_usd=float(og.autoreload_amount_usd),
        current_balance_usd=float(og.ai_credits_usd),
        failed_at=og.autoreload_failed_at.isoformat() if og.autoreload_failed_at else None,
    )
```

- [ ] **Step 4: Run, verify pass**

Run: `pytest tests/test_billing.py -k autoreload -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/routers/billing.py tests/test_billing.py
git commit -m "feat(billing): GET/PUT /billing/autoreload endpoints"
```

---

## Task 10: API — POST /billing/autoreload/retry

**Files:**
- Modify: `backend/routers/billing.py`
- Modify: `tests/test_billing.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_billing.py`:

```python
async def test_post_autoreload_retry_succeeds(
    client: AsyncClient, manager_token, db_session, og_with_card, monkeypatch
):
    import stripe
    fake_intent = MagicMock(status="succeeded", id="pi_retry_ok")
    monkeypatch.setattr(stripe.PaymentIntent, "create", lambda **kw: fake_intent)

    og_with_card.autoreload_failed_at = datetime.now(timezone.utc)
    og_with_card.ai_credits_usd = 0.0
    await db_session.commit()

    response = await client.post(
        "/api/v1/billing/autoreload/retry",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert response.status_code == 200
    assert response.json()["failed_at"] is None

    await db_session.refresh(og_with_card)
    assert og_with_card.autoreload_failed_at is None
    assert og_with_card.ai_credits_usd > 0


async def test_post_autoreload_retry_declined_keeps_failed_state(
    client: AsyncClient, manager_token, db_session, og_with_card, monkeypatch
):
    import stripe
    def boom(**kw):
        raise stripe.CardError("declined", "card_declined", "card_declined")
    monkeypatch.setattr(stripe.PaymentIntent, "create", boom)

    og_with_card.autoreload_failed_at = datetime.now(timezone.utc)
    await db_session.commit()

    response = await client.post(
        "/api/v1/billing/autoreload/retry",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert response.status_code == 402
    await db_session.refresh(og_with_card)
    assert og_with_card.autoreload_failed_at is not None
```

- [ ] **Step 2: Run, verify failure**

Run: `pytest tests/test_billing.py -k retry -v`
Expected: 404.

- [ ] **Step 3: Implement the endpoint**

Append to `backend/routers/billing.py`:

```python
from backend.services.billing import auto_reload_if_needed, AutoReloadError, AutoReloadBlocked


@router.post("/autoreload/retry", response_model=AutoReloadStatus)
async def retry_autoreload(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> AutoReloadStatus:
    og = await _load_og(db, current_user)
    if og.autoreload_failed_at is None:
        raise HTTPException(status_code=400, detail="Auto-reload is not in a failed state")

    # Clear the failed flag, then attempt the reload. If it fails, the helper
    # will re-set autoreload_failed_at and raise.
    og.autoreload_failed_at = None
    await db.flush()

    try:
        await auto_reload_if_needed(db, og, cost_usd=float(og.autoreload_threshold_usd))
    except AutoReloadError as e:
        await db.commit()  # persist the new failed_at the helper set
        raise HTTPException(status_code=402, detail=f"Retry failed: {e}")
    except AutoReloadBlocked:
        # shouldn't happen since we cleared failed_at, but defensive
        raise HTTPException(status_code=409, detail="Billing on hold")

    await db.commit()
    return AutoReloadStatus(
        enabled=og.autoreload_enabled,
        threshold_usd=float(og.autoreload_threshold_usd),
        amount_usd=float(og.autoreload_amount_usd),
        current_balance_usd=float(og.ai_credits_usd),
        failed_at=None,
    )
```

- [ ] **Step 4: Run, verify pass**

Run: `pytest tests/test_billing.py -k retry -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/routers/billing.py tests/test_billing.py
git commit -m "feat(billing): POST /billing/autoreload/retry endpoint"
```

---

## Task 11: API — GET /billing/charges

**Files:**
- Modify: `backend/routers/billing.py`
- Modify: `tests/test_billing.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_billing.py`:

```python
async def test_get_billing_charges_returns_recent_rows(
    client: AsyncClient, manager_token, db_session, og_with_card
):
    from backend.models.billing_charge import BillingCharge

    for i in range(3):
        db_session.add(BillingCharge(
            ownership_group_id=OG_ID,
            kind="autoreload",
            amount_usd=10.0,
            stripe_object_id=f"pi_{i}",
            status="succeeded",
        ))
    await db_session.commit()

    response = await client.get(
        "/api/v1/billing/charges?limit=10",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["charges"]) == 3
    assert all(c["kind"] == "autoreload" for c in body["charges"])
```

- [ ] **Step 2: Run, verify failure**

Run: `pytest tests/test_billing.py::test_get_billing_charges_returns_recent_rows -v`
Expected: 404.

- [ ] **Step 3: Implement the endpoint**

Append to `backend/routers/billing.py`:

```python
from backend.models.billing_charge import BillingCharge


class BillingChargeRow(BaseModel):
    id: str
    kind: str
    amount_usd: float
    stripe_object_id: str | None
    period: str | None
    status: str
    error_message: str | None
    created_at: str


class BillingChargesResponse(BaseModel):
    charges: list[BillingChargeRow]


@router.get("/charges", response_model=BillingChargesResponse)
async def get_billing_charges(
    limit: int = 50,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> BillingChargesResponse:
    og_id = await get_ownership_group_id(db, str(current_user.company_id))
    if not og_id:
        return BillingChargesResponse(charges=[])

    result = await db.execute(
        select(BillingCharge)
        .where(BillingCharge.ownership_group_id == og_id)
        .order_by(BillingCharge.created_at.desc())
        .limit(min(limit, 200))
    )
    rows = list(result.scalars())
    return BillingChargesResponse(charges=[
        BillingChargeRow(
            id=r.id,
            kind=r.kind,
            amount_usd=float(r.amount_usd),
            stripe_object_id=r.stripe_object_id,
            period=r.period,
            status=r.status,
            error_message=r.error_message,
            created_at=r.created_at.isoformat(),
        ) for r in rows
    ])
```

- [ ] **Step 4: Run, verify pass**

Run: `pytest tests/test_billing.py::test_get_billing_charges_returns_recent_rows -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/routers/billing.py tests/test_billing.py
git commit -m "feat(billing): GET /billing/charges endpoint"
```

---

## Task 12: API — GET /billing/portal-link

**Files:**
- Modify: `backend/config.py`
- Modify: `backend/routers/billing.py`
- Modify: `tests/test_billing.py`

- [ ] **Step 1: Add return URL setting**

In `backend/config.py`, after the existing `STRIPE_CANCEL_URL` line, add:

```python
    STRIPE_BILLING_PORTAL_RETURN_URL: str = "http://localhost:5173/manager/schedule"
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_billing.py`:

```python
async def test_get_portal_link_returns_url(
    client: AsyncClient, manager_token, db_session, og_with_card, monkeypatch
):
    import stripe

    fake_session = MagicMock(url="https://billing.stripe.com/session_abc")
    monkeypatch.setattr(stripe.billing_portal.Session, "create", lambda **kw: fake_session)

    response = await client.get(
        "/api/v1/billing/portal-link",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert response.status_code == 200
    assert response.json()["url"].startswith("https://billing.stripe.com/")


async def test_get_portal_link_404_without_stripe_customer(
    client: AsyncClient, manager_token, db_session, seed_og
):
    # seed_og has no stripe_customer_id
    response = await client.get(
        "/api/v1/billing/portal-link",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert response.status_code == 400
```

- [ ] **Step 3: Run, verify failure**

Run: `pytest tests/test_billing.py -k portal_link -v`
Expected: 404 / route not found.

- [ ] **Step 4: Implement the endpoint**

Append to `backend/routers/billing.py`:

```python
class PortalLinkResponse(BaseModel):
    url: str


@router.get("/portal-link", response_model=PortalLinkResponse)
async def get_portal_link(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> PortalLinkResponse:
    og = await _load_og(db, current_user)
    if not og.stripe_customer_id:
        raise HTTPException(status_code=400, detail="No Stripe customer associated with this account")

    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Stripe billing is not configured")
    stripe.api_key = settings.STRIPE_SECRET_KEY

    try:
        session = stripe.billing_portal.Session.create(
            customer=og.stripe_customer_id,
            return_url=settings.STRIPE_BILLING_PORTAL_RETURN_URL,
        )
    except stripe.StripeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return PortalLinkResponse(url=session.url)
```

- [ ] **Step 5: Run, verify pass**

Run: `pytest tests/test_billing.py -k portal_link -v`
Expected: 2 PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/config.py backend/routers/billing.py tests/test_billing.py
git commit -m "feat(billing): GET /billing/portal-link Stripe Customer Portal endpoint"
```

---

## Task 13: Remove /billing/purchase-credits and /billing/confirm-credits

**Files:**
- Modify: `backend/routers/billing.py`
- Modify: `backend/services/billing.py`
- Modify: `tests/test_billing.py`

- [ ] **Step 1: Remove the router endpoints**

In `backend/routers/billing.py`, delete:
- The `PurchaseCreditsRequest` and `PurchaseCreditsResponse` classes (~lines 49-55)
- The `purchase_credits` function and its decorator (~lines 58-101)
- The `confirm_credits` function and its decorator (~lines 104-145)

Keep `create_checkout_session` (the initial signup flow) intact.

- [ ] **Step 2: Remove the obsolete service helper**

In `backend/services/billing.py`, delete `add_purchased_credits` (the function used only by `confirm_credits`). Keep `deduct_credits_for_overage` and `deduct_credits_for_schedule_overage` — those are now part of the auto-reload integration.

- [ ] **Step 3: Remove obsolete tests**

In `tests/test_billing.py`, delete:
- `test_add_purchased_credits` and any related `add_purchased_credits` tests
- `test_purchase_credits` / `test_confirm_credits` if present (they live in the router section)

Remove the `add_purchased_credits` import from the top of the file.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_billing.py -v`
Expected: all PASS (no missing-import errors, no broken assertions).

- [ ] **Step 5: Commit**

```bash
git add backend/routers/billing.py backend/services/billing.py tests/test_billing.py
git commit -m "refactor(billing): remove purchase-credits flow superseded by auto-reload"
```

---

## Task 14: Frontend — update billing API client

**Files:**
- Modify: `frontend/src/api/billing.ts`

- [ ] **Step 1: Replace the file contents**

Overwrite `frontend/src/api/billing.ts` with:

```typescript
import { apiFetch } from "./client";

export interface AiCreditStatus {
  can_generate: boolean;
  free_remaining_usd: number;
  purchased_credits_usd: number;
  is_over_free_tier: boolean;
  monthly_cost_usd: number;
  autoreload_failed?: boolean;
}

export interface ScheduleQuota {
  can_generate: boolean;
  schedules_used: number;
  schedules_free_tier: number;
  is_over_free_tier: boolean;
  purchased_credits_usd: number;
  next_block_cost_usd: number;
  autoreload_failed?: boolean;
}

export interface AutoReloadStatus {
  enabled: boolean;
  threshold_usd: number;
  amount_usd: number;
  current_balance_usd: number;
  failed_at: string | null;
}

export interface BillingChargeRow {
  id: string;
  kind: "autoreload" | "invoice_item_storage" | "invoice_item_employees";
  amount_usd: number;
  stripe_object_id: string | null;
  period: string | null;
  status: "succeeded" | "failed" | "pending";
  error_message: string | null;
  created_at: string;
}

export function getAiCredits(): Promise<AiCreditStatus> {
  return apiFetch<AiCreditStatus>("/schedules/ai-credits");
}

export function getScheduleQuota(): Promise<ScheduleQuota> {
  return apiFetch<ScheduleQuota>("/schedules/schedule-quota");
}

export function getAutoReload(): Promise<AutoReloadStatus> {
  return apiFetch<AutoReloadStatus>("/billing/autoreload");
}

export function updateAutoReload(
  body: Partial<{ enabled: boolean; threshold_usd: number; amount_usd: number }>
): Promise<AutoReloadStatus> {
  return apiFetch<AutoReloadStatus>("/billing/autoreload", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function retryAutoReload(): Promise<AutoReloadStatus> {
  return apiFetch<AutoReloadStatus>("/billing/autoreload/retry", {
    method: "POST",
  });
}

export function getBillingCharges(limit = 50): Promise<{ charges: BillingChargeRow[] }> {
  return apiFetch<{ charges: BillingChargeRow[] }>(`/billing/charges?limit=${limit}`);
}

export function getPortalLink(): Promise<{ url: string }> {
  return apiFetch<{ url: string }>("/billing/portal-link");
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npm run build`
Expected: build succeeds. If any file still imports `purchaseCredits` or `confirmCredits`, the build will fail — those are addressed in Task 15.

If the build fails because `Schedule.tsx` still imports the removed functions, that's expected — move on to Task 15.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/billing.ts
git commit -m "feat(billing-fe): swap purchase-credits client for auto-reload client"
```

---

## Task 15: Frontend — replace Purchase Credits modal with Auto-Reload card

**Files:**
- Modify: `frontend/src/pages/manager/Schedule.tsx`

- [ ] **Step 1: Locate the Purchase Credits modal**

Open `frontend/src/pages/manager/Schedule.tsx` and find:
- The `purchaseAmount` / `purchaseLoading` state (around line 372)
- The `handlePurchaseCredits` handler (around line 444)
- The `confirmCredits` useEffect that handles the post-Checkout redirect (around line 434)
- The Purchase Credits Modal JSX block (around line 1206)

- [ ] **Step 2: Remove obsolete state and handlers**

Delete:
- The `purchaseAmount`, `purchaseLoading`, `creditsSessionId` related state
- The `handlePurchaseCredits` function
- The `confirmCredits` useEffect (the `?credits_session_id=` redirect path is no longer used)
- The Purchase Credits Modal JSX block

Remove `purchaseCredits, confirmCredits` from the `billingApi` import.

- [ ] **Step 3: Add Auto-Reload state and fetch**

Near the existing state declarations in `Schedule.tsx`, add:

```typescript
const [autoReload, setAutoReload] = useState<AutoReloadStatus | null>(null);
const [autoReloadEditing, setAutoReloadEditing] = useState(false);
const [autoReloadDraft, setAutoReloadDraft] = useState<{
  enabled: boolean;
  threshold_usd: number;
  amount_usd: number;
}>({ enabled: true, threshold_usd: 2, amount_usd: 10 });
```

Add `AutoReloadStatus` to the `billingApi` import block:

```typescript
import * as billingApi from "../../api/billing";
import type { AutoReloadStatus } from "../../api/billing";
```

After the existing AI-credits / quota fetch effect, add:

```typescript
useEffect(() => {
  billingApi.getAutoReload().then((s) => {
    setAutoReload(s);
    setAutoReloadDraft({
      enabled: s.enabled,
      threshold_usd: s.threshold_usd,
      amount_usd: s.amount_usd,
    });
  }).catch(() => {});
}, []);
```

- [ ] **Step 4: Add save and retry handlers**

```typescript
const handleSaveAutoReload = async () => {
  const updated = await billingApi.updateAutoReload(autoReloadDraft);
  setAutoReload(updated);
  setAutoReloadEditing(false);
};

const handleRetryAutoReload = async () => {
  try {
    const updated = await billingApi.retryAutoReload();
    setAutoReload(updated);
  } catch (e) {
    alert(t.schedule.retryFailed ?? "Retry failed — update your card and try again.");
  }
};
```

- [ ] **Step 5: Render the Auto-Reload card**

Where the Purchase Credits Modal used to be, render an inline card (not a modal). The card shows:
- Current balance
- Toggle, threshold input, amount input (read-only by default; "Edit" button toggles editing)
- "Save" / "Cancel" buttons when editing
- Last failed-at red banner (handled in Task 16)

```tsx
{autoReload && (
  <div className="bg-white shadow rounded-lg p-6 mb-6">
    <h3 className="text-lg font-semibold mb-2">{t.schedule.autoReloadTitle}</h3>
    <p className="text-sm text-gray-600 mb-4">
      {t.schedule.autoReloadDescription}
    </p>
    <div className="grid grid-cols-3 gap-4 mb-4">
      <Stat label={t.schedule.balance} value={`$${autoReload.current_balance_usd.toFixed(2)}`} />
      <Stat label={t.schedule.threshold} value={`$${autoReload.threshold_usd.toFixed(2)}`} />
      <Stat label={t.schedule.refillAmount} value={`$${autoReload.amount_usd.toFixed(2)}`} />
    </div>
    {!autoReloadEditing ? (
      <button onClick={() => setAutoReloadEditing(true)}
              className="px-4 py-2 bg-blue-600 text-white rounded">
        {t.common.edit}
      </button>
    ) : (
      <div className="space-y-3">
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={autoReloadDraft.enabled}
                 onChange={(e) => setAutoReloadDraft({ ...autoReloadDraft, enabled: e.target.checked })} />
          {t.schedule.autoReloadEnabled}
        </label>
        <label className="block">{t.schedule.threshold}: $
          <input type="number" min="0.5" step="0.5"
                 value={autoReloadDraft.threshold_usd}
                 onChange={(e) => setAutoReloadDraft({ ...autoReloadDraft, threshold_usd: parseFloat(e.target.value) })}
                 className="ml-2 border rounded px-2 py-1 w-20" />
        </label>
        <label className="block">{t.schedule.refillAmount}: $
          <input type="number" min="0.5" step="1"
                 value={autoReloadDraft.amount_usd}
                 onChange={(e) => setAutoReloadDraft({ ...autoReloadDraft, amount_usd: parseFloat(e.target.value) })}
                 className="ml-2 border rounded px-2 py-1 w-20" />
        </label>
        <div className="flex gap-2">
          <button onClick={handleSaveAutoReload} className="px-4 py-2 bg-blue-600 text-white rounded">
            {t.common.save}
          </button>
          <button onClick={() => setAutoReloadEditing(false)} className="px-4 py-2 bg-gray-200 rounded">
            {t.common.cancel}
          </button>
        </div>
      </div>
    )}
  </div>
)}
```

(`Stat` is a tiny inline helper component — define it at the top of the file if not already present:)

```tsx
const Stat = ({ label, value }: { label: string; value: string }) => (
  <div>
    <div className="text-xs text-gray-500">{label}</div>
    <div className="text-lg font-semibold">{value}</div>
  </div>
);
```

- [ ] **Step 6: Verify UI in browser**

Run: `cd frontend && npm run dev`
Open `http://localhost:5173/manager/schedule` after logging in as a manager.
Expected:
- The Purchase Credits modal is gone.
- A "Auto-Reload" card appears showing balance, threshold, refill amount.
- Clicking "Edit" reveals input fields; "Save" persists; reloading the page shows the saved values.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/manager/Schedule.tsx
git commit -m "feat(billing-fe): replace Purchase Credits modal with Auto-Reload card"
```

---

## Task 16: Frontend — failed-state banner with retry

**Files:**
- Modify: `frontend/src/pages/manager/Schedule.tsx`

- [ ] **Step 1: Add the banner**

In `Schedule.tsx`, near the top of the rendered Manager Schedule page (above the existing schedule UI), add:

```tsx
{autoReload?.failed_at && (
  <div className="bg-red-50 border border-red-300 rounded-lg p-4 mb-6 flex items-center justify-between">
    <div>
      <div className="font-semibold text-red-900">{t.schedule.billingOnHoldTitle}</div>
      <div className="text-sm text-red-800">
        {t.schedule.billingOnHoldBody.replace("{date}", new Date(autoReload.failed_at).toLocaleString())}
      </div>
    </div>
    <div className="flex gap-2">
      <button onClick={handleRetryAutoReload} className="px-3 py-1 bg-red-600 text-white rounded">
        {t.schedule.retryPayment}
      </button>
      <button
        onClick={async () => {
          const { url } = await billingApi.getPortalLink();
          window.location.href = url;
        }}
        className="px-3 py-1 bg-white border border-red-300 text-red-800 rounded"
      >
        {t.schedule.updateCard}
      </button>
    </div>
  </div>
)}
```

- [ ] **Step 2: Verify the banner**

While the dev server is running:
- Manually set `autoreload_failed_at` to `now()` for your OG in the database (or via a debug endpoint).
- Reload the page; expected: red banner appears with Retry and Update Card buttons.
- Click Retry; expected: with a valid card, banner disappears and balance refills.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/manager/Schedule.tsx
git commit -m "feat(billing-fe): failed-state banner with retry + update-card actions"
```

---

## Task 17: Frontend — i18n updates

**Files:**
- Modify: `frontend/src/i18n/en.ts`
- Modify: `frontend/src/i18n/types.ts`

- [ ] **Step 1: Add new English strings**

In `frontend/src/i18n/en.ts`, inside the `schedule:` object, add:

```typescript
    autoReloadTitle: "Auto-Reload",
    autoReloadDescription: "When your AI/schedule balance falls below the threshold, we'll automatically charge your card to refill it. Disable to pause AI generation when the balance is exhausted.",
    autoReloadEnabled: "Enabled",
    balance: "Balance",
    threshold: "Threshold",
    refillAmount: "Refill amount",
    billingOnHoldTitle: "Billing on hold",
    billingOnHoldBody: "Your last automatic payment failed on {date}. Update your card or retry payment to resume.",
    retryPayment: "Retry payment",
    retryFailed: "Retry failed — update your card and try again.",
    updateCard: "Update card",
```

Remove the obsolete `purchase`, `purchaseAmount`, `redirectingToPayment`, etc. keys related to Purchase Credits.

In `frontend/src/i18n/types.ts`, add the new keys to the `ScheduleStrings` (or whichever named type) interface and remove the old ones. TypeScript will tell you which other language files are now missing keys.

- [ ] **Step 2: Add placeholder translations for other languages**

For each other locale (`de.ts`, `es.ts`, `fr.ts`, etc.), copy the new keys with the English values as placeholders. Translations can come later. Remove the obsolete keys to match the updated types.

A quick approach: run `cd frontend && npm run build`. The TypeScript compiler will list every locale file missing the new keys / containing removed keys. Address each file in turn.

- [ ] **Step 3: Verify build passes**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/i18n/
git commit -m "feat(billing-fe): i18n strings for auto-reload + billing-on-hold UI"
```

---

## Task 18: One-shot backfill script for existing subscriptions

**Files:**
- Create: `backend/scripts/backfill_autoreload_pm.py`

- [ ] **Step 1: Write the script**

Create `backend/scripts/backfill_autoreload_pm.py`:

```python
"""One-shot migration: cache default_payment_method_id for every OG with a subscription.

Run after deploying PR 1 to populate the new column for customers who already
have an active Stripe subscription. New signups will populate it automatically
via the auto-reload flow.

Usage (locally):
    python -m backend.scripts.backfill_autoreload_pm

Usage (against ECS, one-off task):
    aws ecs run-task --cluster wizscheduler-cluster \
      --task-definition wizscheduler --launch-type FARGATE \
      --network-configuration "..." \
      --overrides '{"containerOverrides":[{"name":"wizscheduler",
        "command":["sh","-c","cd /app && python -m backend.scripts.backfill_autoreload_pm"]}]}'

Idempotent — re-running is safe (refreshes the cached PM if Stripe returns a new one).
"""
import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import settings
from backend.models.ownership_group import OwnershipGroup
from backend.services.billing import cache_default_payment_method

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    engine = create_async_engine(settings.DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        result = await db.execute(
            select(OwnershipGroup).where(OwnershipGroup.stripe_subscription_id.is_not(None))
        )
        ogs = list(result.scalars())
        logger.info("Found %d OG(s) with a subscription", len(ogs))

        for og in ogs:
            try:
                pm_id = await cache_default_payment_method(db, og)
                logger.info("  %s (%s): default_pm=%s", og.id, og.name, pm_id or "<none>")
            except Exception as e:  # noqa: BLE001
                logger.error("  %s (%s): FAILED %s", og.id, og.name, e)

        await db.commit()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Smoke test locally**

If you have local data, run: `cd backend && python -m backend.scripts.backfill_autoreload_pm`
Expected: prints one line per OG with a subscription; `default_pm` column updates in your DB. Don't run against production yet — that's a deploy-time action documented in the PR description.

- [ ] **Step 3: Commit**

```bash
git add backend/scripts/backfill_autoreload_pm.py
git commit -m "feat(billing): one-shot backfill script for default_payment_method_id"
```

---

## Task 19: Full test pass + final verification

- [ ] **Step 1: Run the full test suite**

Run: `pytest tests/ -v`
Expected: all PASS. Any regression here means an earlier task missed updating an existing test — go back and fix.

- [ ] **Step 2: Run the frontend build + typecheck**

Run: `cd frontend && npm run build`
Expected: clean build.

- [ ] **Step 3: Smoke-test the full flow locally**

With the local dev server running (`uvicorn` backend + `npm run dev` frontend):
1. Log in as the seed manager.
2. Visit the Schedule page; confirm the Auto-Reload card renders with default values (enabled, $2 threshold, $10 amount).
3. Click Edit; change to $5 / $25; save; reload; confirm persisted.
4. Hit `GET /api/v1/billing/charges` directly; expected: empty array (no charges yet locally).

- [ ] **Step 4: Update spec doc with implementation notes (optional)**

If anything diverged from the spec (e.g. you renamed a function), append a "Notes from implementation" section to `docs/superpowers/specs/2026-05-11-usage-overage-billing-design.md`.

- [ ] **Step 5: Final commit if anything changed**

```bash
git add -A && git commit -m "chore(billing): final cleanup after PR 1 implementation"
```

- [ ] **Step 6: Open the PR**

```bash
git push -u origin <branch>
gh pr create --title "Auto-reload billing (PR 1)" --body "$(cat <<'EOF'
## Summary
- Replaces manual Purchase Credits flow with auto-reloading prepaid balance
- New `auto_reload_if_needed` helper charges saved card off-session when AI/schedule overage debit would drop balance below threshold
- New `billing_charges` audit table; new columns on `ownership_groups`
- New endpoints: `GET/PUT /billing/autoreload`, `POST /billing/autoreload/retry`, `GET /billing/charges`, `GET /billing/portal-link`
- Auto-reload failure sets a sticky `autoreload_failed_at` that blocks AI/schedule generation until manager retries
- Spec: `docs/superpowers/specs/2026-05-11-usage-overage-billing-design.md`

## Deploy steps
1. Merge → CI deploys new image
2. Run `aws ecs run-task ... python -m backend.scripts.backfill_autoreload_pm` once to cache PMs for existing subscriptions
3. (No Stripe-side config changes needed for PR 1; PR 2 will add the Meters/webhooks for storage+employee monthly billing.)

## Test plan
- [x] All existing tests pass
- [x] New auto-reload tests cover: success, balance-sufficient skip, decline, disabled, blocked
- [x] UI smoke-tested: Auto-Reload card edit/save, failed-state banner, retry button
- [ ] After deploy: run backfill script, verify `default_payment_method_id` populated
- [ ] After deploy: manually trigger overage on a test customer, verify a real PaymentIntent fires

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review Notes

This plan covers every spec requirement listed in `docs/superpowers/specs/2026-05-11-usage-overage-billing-design.md` under "PR 1 — Auto-reload" (items 1–8). Specifically:
- Migration + models: Tasks 1–2
- Config defaults: Task 3
- `auto_reload_if_needed`: Tasks 4–5
- Wired into existing deduct paths: Tasks 6–7
- Block on failure: Task 8
- New endpoints: Tasks 9–12
- Remove purchase-credits: Task 13
- Frontend: Tasks 14–17
- Backfill script: Task 18

PR 2 work (monthly InvoiceItems, Stripe webhooks, pending-charges panel) is explicitly out of scope and will get its own plan.
