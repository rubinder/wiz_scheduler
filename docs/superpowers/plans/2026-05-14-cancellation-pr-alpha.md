# Subscription Cancellation PR α — User-Facing Cancel + Reactivation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a customer self-serve cancel their subscription via the Stripe Customer Portal, see a read-only state in the UI after the subscription deletes, and reactivate by completing a new Stripe Checkout — all without breaking any existing flows.

**Architecture:** A single column `canceled_at` on `ownership_groups` is the source of truth for "is this OG in the read-only grace period." A new `require_active_billing` dependency blocks the schedule-generation endpoint when that's set. A new pair of endpoints (`POST /billing/reactivate-checkout` + `POST /billing/confirm-reactivation`) handle the round-trip to Stripe Checkout for a brand-new subscription. Two new Stripe webhook handlers — `customer.subscription.updated` (audit only) and `customer.subscription.deleted` (sets `canceled_at`, sends notification email) — wire the lifecycle.

**Tech Stack:** FastAPI · SQLAlchemy 2.x async · Alembic · `stripe` Python SDK · Resend (email) · React 18 · TypeScript · Vite · pytest-asyncio · httpx

**Spec:** `docs/superpowers/specs/2026-05-14-subscription-cancellation-design.md`

**Out of scope for this PR (lands in PR β):** the daily deletion cron, the 14-day reminder email, the final "data deleted" email, hard-delete via CASCADE.

---

## Task 1: Migration + OwnershipGroup model columns

**Files:**
- Create: `backend/alembic/versions/0022_add_cancellation_columns.py`
- Modify: `backend/models/ownership_group.py`

- [ ] **Step 1: Write the migration**

Create `backend/alembic/versions/0022_add_cancellation_columns.py`:

```python
"""Add cancellation lifecycle columns to ownership_groups.

- canceled_at: set by customer.subscription.deleted webhook. Presence
  indicates the OG is in read_only_grace state.
- notified_subscription_ended_at / notified_deletion_reminder_at /
  notified_data_deleted_at: idempotency timestamps for the three
  lifecycle emails (subscription ended, 14-day reminder, deleted).
  Cleared on reactivation so a future re-cancel re-fires correctly.

Revision ID: 0022
Revises: 0021
Create Date: 2026-05-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ownership_groups",
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ownership_groups",
        sa.Column("notified_subscription_ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ownership_groups",
        sa.Column("notified_deletion_reminder_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ownership_groups",
        sa.Column("notified_data_deleted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ownership_groups", "notified_data_deleted_at")
    op.drop_column("ownership_groups", "notified_deletion_reminder_at")
    op.drop_column("ownership_groups", "notified_subscription_ended_at")
    op.drop_column("ownership_groups", "canceled_at")
```

- [ ] **Step 2: Add columns to the model**

In `backend/models/ownership_group.py`, after the existing `default_payment_method_id` column and before `api_integration`, add:

```python
    canceled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notified_subscription_ended_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notified_deletion_reminder_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notified_data_deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
```

- [ ] **Step 3: Run the full test suite to confirm the model + SQLite tables align**

Run: `pytest tests/test_billing.py --no-header -q`
Expected: all green (tests/conftest.py rebuilds tables from the model on every test run, so any model/migration mismatch shows immediately).

- [ ] **Step 4: Commit**

```bash
git add backend/alembic/versions/0022_add_cancellation_columns.py backend/models/ownership_group.py
git commit -m "feat(billing): add cancellation lifecycle columns to ownership_groups"
```

---

## Task 2: Two new webhook handlers

**Files:**
- Modify: `backend/routers/webhooks.py`
- Modify: `tests/test_billing.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_billing.py`:

```python
async def test_webhook_subscription_updated_logs_only(
    client: AsyncClient, db_session, og_with_card, monkeypatch
):
    """customer.subscription.updated is acked but does not mutate the OG."""
    import stripe
    fake_event = {
        "type": "customer.subscription.updated",
        "data": {"object": {
            "customer": "cus_test_abc",
            "cancel_at_period_end": True,
            "current_period_end": int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()),
        }},
    }
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda p, s, k: fake_event)
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test")

    response = await client.post(
        "/api/v1/webhooks/stripe",
        content=b"{}",
        headers={"stripe-signature": "t=1,v1=x", "content-type": "application/json"},
    )
    assert response.status_code == 200
    await db_session.refresh(og_with_card)
    assert og_with_card.canceled_at is None  # NOT set — Stripe is authoritative


async def test_webhook_subscription_deleted_sets_canceled_at(
    client: AsyncClient, db_session, og_with_card, monkeypatch
):
    """customer.subscription.deleted sets og.canceled_at and sends an email."""
    import stripe
    fake_event = {
        "type": "customer.subscription.deleted",
        "data": {"object": {"customer": "cus_test_abc"}},
    }
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda p, s, k: fake_event)
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test")

    sent_emails = []
    async def fake_send(og):
        sent_emails.append(og.id)
    monkeypatch.setattr(
        "backend.routers.webhooks.send_subscription_ended_email", fake_send
    )

    assert og_with_card.canceled_at is None
    response = await client.post(
        "/api/v1/webhooks/stripe",
        content=b"{}",
        headers={"stripe-signature": "t=1,v1=x", "content-type": "application/json"},
    )
    assert response.status_code == 200

    await db_session.refresh(og_with_card)
    assert og_with_card.canceled_at is not None
    assert og_with_card.notified_subscription_ended_at is not None
    assert sent_emails == [OG_ID]


async def test_webhook_subscription_deleted_idempotent(
    client: AsyncClient, db_session, og_with_card, monkeypatch
):
    """Redelivering the deletion event does not overwrite canceled_at."""
    import stripe
    original_canceled = datetime(2026, 4, 1, tzinfo=timezone.utc)
    og_with_card.canceled_at = original_canceled
    og_with_card.notified_subscription_ended_at = original_canceled
    await db_session.commit()

    fake_event = {
        "type": "customer.subscription.deleted",
        "data": {"object": {"customer": "cus_test_abc"}},
    }
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda p, s, k: fake_event)
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test")

    sent = []
    async def fake_send(og):
        sent.append(og.id)
    monkeypatch.setattr(
        "backend.routers.webhooks.send_subscription_ended_email", fake_send
    )

    response = await client.post(
        "/api/v1/webhooks/stripe",
        content=b"{}",
        headers={"stripe-signature": "t=1,v1=x", "content-type": "application/json"},
    )
    assert response.status_code == 200

    await db_session.refresh(og_with_card)
    assert og_with_card.canceled_at == original_canceled  # unchanged
    assert sent == []  # email NOT re-sent
```

- [ ] **Step 2: Run, verify failure**

Run: `pytest tests/test_billing.py -k "subscription_updated_logs_only or subscription_deleted" --no-header -q`
Expected: all three FAIL (handlers not implemented, `send_subscription_ended_email` not defined)

- [ ] **Step 3: Implement the handlers**

In `backend/routers/webhooks.py`, update the imports near the top:

```python
from backend.services.billing import (
    bill_monthly_overages_for_og,
    cache_default_payment_method,
    send_subscription_ended_email,
)
```

Inside `stripe_webhook`, after the existing `invoice.payment_succeeded` block and before the fallthrough `return {"received": True, "type": event_type}`:

```python
    if event_type == "customer.subscription.updated":
        og = await _find_og(_customer_id())
        cape = obj.get("cancel_at_period_end") if isinstance(obj, dict) else getattr(obj, "cancel_at_period_end", None)
        logger.info(
            "Subscription updated: og=%s cancel_at_period_end=%s",
            og.id if og else None,
            cape,
        )
        return {"received": True, "type": event_type, "og_id": og.id if og else None}

    if event_type == "customer.subscription.deleted":
        og = await _find_og(_customer_id())
        if og and og.canceled_at is None:
            og.canceled_at = datetime.now(timezone.utc)
            og.notified_subscription_ended_at = og.canceled_at
            await db.commit()
            await send_subscription_ended_email(og)
        return {"received": True, "type": event_type, "og_id": og.id if og else None}
```

- [ ] **Step 4: Stub the email helper**

In `backend/services/billing.py`, append at the end of the file:

```python
async def send_subscription_ended_email(og: OwnershipGroup) -> None:
    """Send the 'your subscription has ended' email via Resend.

    Idempotency is handled by the caller (webhook handler checks
    notified_subscription_ended_at before invoking). Non-fatal — exceptions
    are logged and swallowed so a Resend outage doesn't drop the webhook.
    """
    if not settings.RESEND_API_KEY:
        logger.info("Skipping subscription-ended email for og=%s (RESEND_API_KEY unset)", og.id)
        return
    # Real send wired in Task 6; this stub exists so webhook handler can import it.
    logger.info("send_subscription_ended_email stub for og=%s", og.id)
```

- [ ] **Step 5: Run tests, verify pass**

Run: `pytest tests/test_billing.py -k "subscription_updated_logs_only or subscription_deleted" --no-header -q`
Expected: 3 PASS

- [ ] **Step 6: Commit**

```bash
git add backend/routers/webhooks.py backend/services/billing.py tests/test_billing.py
git commit -m "feat(billing): customer.subscription.updated + deleted webhook handlers"
```

---

## Task 3: `require_active_billing` dependency + apply to `/schedules/generate`

**Files:**
- Modify: `backend/dependencies.py`
- Modify: `backend/routers/schedules.py`
- Modify: `tests/test_billing.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_billing.py`:

```python
async def test_generate_blocked_when_canceled(
    client: AsyncClient, manager_token, db_session, og_with_card
):
    """POST /schedules/generate returns 402 when og.canceled_at is set."""
    og_with_card.canceled_at = datetime.now(timezone.utc)
    await db_session.commit()

    response = await client.post(
        "/api/v1/schedules/generate",
        json={"start_date": "2026-06-01", "end_date": "2026-06-07", "use_local": True},
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert response.status_code == 402
    assert "subscription" in response.json()["detail"].lower()


async def test_employees_list_still_works_when_canceled(
    client: AsyncClient, manager_token, db_session, og_with_card, seed_employees
):
    """CRUD reads stay accessible when the OG is canceled — users need
    to retrieve their data."""
    og_with_card.canceled_at = datetime.now(timezone.utc)
    await db_session.commit()

    response = await client.get(
        "/api/v1/employees/",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert response.status_code == 200
```

- [ ] **Step 2: Run, verify failure**

Run: `pytest tests/test_billing.py -k "blocked_when_canceled or list_still_works_when_canceled" --no-header -q`
Expected: 1 fail (generate test returns 200 instead of 402), 1 pass

- [ ] **Step 3: Add the dependency**

In `backend/dependencies.py`, append:

```python
async def require_active_billing(
    current_user: "User" = Depends(require_manager),
    db: "AsyncSession" = Depends(get_db),
) -> "User":
    """Block requests when the OG is in the read-only grace period.

    Used to gate paid-resource endpoints (AI/schedule generation) without
    blocking CRUD, billing, auth, or GDPR endpoints — users in grace need
    to retrieve their data and reactivate.
    """
    from backend.models.ownership_group import OwnershipGroup
    from backend.services.billing import get_ownership_group_id

    og_id = await get_ownership_group_id(db, str(current_user.company_id))
    if not og_id:
        return current_user
    og = await db.get(OwnershipGroup, og_id)
    if og and og.canceled_at is not None:
        raise HTTPException(
            status_code=402,
            detail="Subscription canceled. Reactivate to resume scheduling.",
        )
    return current_user
```

(The existing imports at the top of `dependencies.py` should already cover `Depends`, `HTTPException`, etc — if not, add them.)

- [ ] **Step 4: Apply to the generate route**

In `backend/routers/schedules.py`, change the `generate_schedule` signature from:

```python
async def generate_schedule(
    body: GenerateRequest,
    request: Request,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
```

to:

```python
async def generate_schedule(
    body: GenerateRequest,
    request: Request,
    current_user: User = Depends(require_active_billing),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
```

Add `require_active_billing` to the existing `dependencies` import line at the top of `schedules.py` (alongside `require_manager`, `get_db`, etc.).

- [ ] **Step 5: Run, verify pass**

Run: `pytest tests/test_billing.py -k "blocked_when_canceled or list_still_works_when_canceled" --no-header -q`
Expected: 2 PASS

Run: `pytest tests/test_schedules.py --no-header -q`
Expected: still PASS (existing tests use OGs without `canceled_at` set, so the new dependency is a no-op for them).

- [ ] **Step 6: Commit**

```bash
git add backend/dependencies.py backend/routers/schedules.py tests/test_billing.py
git commit -m "feat(billing): require_active_billing dependency blocks generate when canceled"
```

---

## Task 4: Augment `GET /billing/usage` with cancellation fields

**Files:**
- Modify: `backend/routers/billing.py`
- Modify: `tests/test_billing.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_billing.py`:

```python
async def test_get_usage_reports_canceled_state(
    client: AsyncClient, manager_token, db_session, og_with_card
):
    """GET /billing/usage includes is_read_only, canceled_at, scheduled_deletion_at
    when the OG has canceled_at set."""
    canceled = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    og_with_card.canceled_at = canceled
    await db_session.commit()

    response = await client.get(
        "/api/v1/billing/usage",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["is_read_only"] is True
    assert body["canceled_at"].startswith("2026-05-01T12:00:00")
    # 90 days after 2026-05-01 = 2026-07-30
    assert body["scheduled_deletion_at"].startswith("2026-07-30")


async def test_get_usage_active_state_no_cancellation_fields(
    client: AsyncClient, manager_token, db_session, og_with_card
):
    """Active OGs return is_read_only=False and null cancellation timestamps."""
    response = await client.get(
        "/api/v1/billing/usage",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["is_read_only"] is False
    assert body["canceled_at"] is None
    assert body["scheduled_deletion_at"] is None
```

- [ ] **Step 2: Run, verify failure**

Run: `pytest tests/test_billing.py -k "canceled_state or active_state_no_cancellation" --no-header -q`
Expected: 2 FAIL (fields missing in response)

- [ ] **Step 3: Modify `/billing/usage`**

In `backend/routers/billing.py`, locate the `get_usage` function. Add the import at the top of the file if not already present:

```python
from datetime import timedelta
```

Replace the body of `get_usage` to thread cancellation state into both branches:

```python
@router.get("/usage")
async def get_usage(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get the full billing summary for the current ownership group.

    Includes LLM/storage/employee usage, pending invoice items, and the
    cancellation lifecycle state (is_read_only, canceled_at, scheduled_deletion_at).
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
            "is_read_only": False,
            "canceled_at": None,
            "scheduled_deletion_at": None,
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

    # Cancellation state: read directly off the OG row.
    og = await db.get(OwnershipGroup, og_id)
    if og and og.canceled_at is not None:
        summary["is_read_only"] = True
        summary["canceled_at"] = og.canceled_at.isoformat()
        summary["scheduled_deletion_at"] = (
            og.canceled_at + timedelta(days=settings.SUBSCRIPTION_GRACE_DAYS)
        ).isoformat()
    else:
        summary["is_read_only"] = False
        summary["canceled_at"] = None
        summary["scheduled_deletion_at"] = None

    return summary
```

- [ ] **Step 4: Add the config constant**

In `backend/config.py`, right after `BASE_MONTHLY_USD: float = 18.00`:

```python
    # Subscription cancellation lifecycle
    SUBSCRIPTION_GRACE_DAYS: int = 90
    SUBSCRIPTION_REMINDER_DAYS_BEFORE_DELETE: int = 14
```

- [ ] **Step 5: Run, verify pass**

Run: `pytest tests/test_billing.py -k "canceled_state or active_state_no_cancellation" --no-header -q`
Expected: 2 PASS

- [ ] **Step 6: Commit**

```bash
git add backend/routers/billing.py backend/config.py tests/test_billing.py
git commit -m "feat(billing): augment /billing/usage with cancellation lifecycle fields"
```

---

## Task 5: Reactivation endpoints

**Files:**
- Modify: `backend/routers/billing.py`
- Modify: `tests/test_billing.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_billing.py`:

```python
async def test_reactivate_checkout_creates_session(
    client: AsyncClient, manager_token, db_session, og_with_card, monkeypatch
):
    """POST /billing/reactivate-checkout returns a Stripe Checkout URL,
    reusing the existing stripe_customer_id."""
    import stripe
    og_with_card.canceled_at = datetime.now(timezone.utc)
    await db_session.commit()

    captured_kwargs = {}
    def fake_create(**kwargs):
        captured_kwargs.update(kwargs)
        return MagicMock(id="cs_reactivate_1", url="https://checkout.stripe.com/c/cs_reactivate_1")
    monkeypatch.setattr(stripe.checkout.Session, "create", fake_create)
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setattr(settings, "STRIPE_PRICE_ID", "price_test")

    response = await client.post(
        "/api/v1/billing/reactivate-checkout",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert response.status_code == 200
    assert response.json()["url"].startswith("https://checkout.stripe.com/")
    assert captured_kwargs["customer"] == "cus_test_abc"
    assert captured_kwargs["mode"] == "subscription"


async def test_reactivate_checkout_rejects_when_not_canceled(
    client: AsyncClient, manager_token, db_session, og_with_card
):
    """Cannot reactivate an OG that hasn't been canceled."""
    assert og_with_card.canceled_at is None
    response = await client.post(
        "/api/v1/billing/reactivate-checkout",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert response.status_code == 400


async def test_confirm_reactivation_clears_canceled_state(
    client: AsyncClient, manager_token, db_session, og_with_card, monkeypatch
):
    """POST /billing/confirm-reactivation clears canceled_at + all notified_* flags."""
    import stripe
    og_with_card.canceled_at = datetime.now(timezone.utc)
    og_with_card.notified_subscription_ended_at = datetime.now(timezone.utc)
    og_with_card.notified_deletion_reminder_at = datetime.now(timezone.utc)
    await db_session.commit()

    fake_session = MagicMock(
        payment_status="paid",
        customer="cus_test_abc",
        subscription="sub_reactivated_xyz",
    )
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", lambda sid: fake_session)
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_dummy")

    response = await client.post(
        "/api/v1/billing/confirm-reactivation",
        json={"session_id": "cs_reactivate_1"},
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert response.status_code == 200

    await db_session.refresh(og_with_card)
    assert og_with_card.canceled_at is None
    assert og_with_card.notified_subscription_ended_at is None
    assert og_with_card.notified_deletion_reminder_at is None
    assert og_with_card.stripe_subscription_id == "sub_reactivated_xyz"


async def test_confirm_reactivation_rejects_unpaid_session(
    client: AsyncClient, manager_token, db_session, og_with_card, monkeypatch
):
    import stripe
    og_with_card.canceled_at = datetime.now(timezone.utc)
    await db_session.commit()

    fake_session = MagicMock(payment_status="unpaid", customer="cus_test_abc")
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", lambda sid: fake_session)
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_dummy")

    response = await client.post(
        "/api/v1/billing/confirm-reactivation",
        json={"session_id": "cs_unpaid"},
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert response.status_code == 400
```

- [ ] **Step 2: Run, verify failure**

Run: `pytest tests/test_billing.py -k "reactivate_checkout or confirm_reactivation" --no-header -q`
Expected: 4 FAIL with 404 / route not found

- [ ] **Step 3: Implement the endpoints**

Append to `backend/routers/billing.py`:

```python
class ReactivateCheckoutResponse(BaseModel):
    session_id: str
    url: str


class ConfirmReactivationRequest(BaseModel):
    session_id: str


@router.post("/reactivate-checkout", response_model=ReactivateCheckoutResponse)
async def reactivate_checkout(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> ReactivateCheckoutResponse:
    """Create a Stripe Checkout session for reactivating a canceled subscription.

    Reuses the existing stripe_customer_id so saved payment methods + invoice
    history persist across the cancel/reactivate cycle. The customer completes
    Checkout and is redirected back; the frontend then calls
    /billing/confirm-reactivation with the session_id.
    """
    og = await _load_og(db, current_user)
    if og.canceled_at is None:
        raise HTTPException(status_code=400, detail="Subscription is not canceled")
    if not settings.STRIPE_SECRET_KEY or not settings.STRIPE_PRICE_ID:
        raise HTTPException(status_code=503, detail="Stripe billing is not configured")

    stripe.api_key = settings.STRIPE_SECRET_KEY
    success_url = settings.STRIPE_SUCCESS_URL.replace(
        "/register", "/manager/dashboard"
    ).replace("session_id=", "reactivate_session_id=")
    cancel_url = settings.STRIPE_CANCEL_URL.replace("/register", "/manager/dashboard")

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            payment_method_types=["card"],
            customer=og.stripe_customer_id,
            line_items=[{"price": settings.STRIPE_PRICE_ID, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
        )
    except stripe.StripeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return ReactivateCheckoutResponse(session_id=session.id, url=session.url)


@router.post("/confirm-reactivation")
async def confirm_reactivation(
    body: ConfirmReactivationRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Verify the reactivation Checkout session and clear cancellation state."""
    og = await _load_og(db, current_user)
    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Stripe billing is not configured")

    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        session = stripe.checkout.Session.retrieve(body.session_id)
    except stripe.StripeError:
        raise HTTPException(status_code=400, detail="Invalid session")

    if session.payment_status != "paid":
        raise HTTPException(status_code=400, detail="Payment not completed")

    og.stripe_customer_id = session.customer
    og.stripe_subscription_id = session.subscription
    og.canceled_at = None
    og.notified_subscription_ended_at = None
    og.notified_deletion_reminder_at = None
    og.notified_data_deleted_at = None
    await db.commit()

    return {"reactivated": True, "subscription_id": og.stripe_subscription_id}
```

- [ ] **Step 4: Run, verify pass**

Run: `pytest tests/test_billing.py -k "reactivate_checkout or confirm_reactivation" --no-header -q`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/routers/billing.py tests/test_billing.py
git commit -m "feat(billing): reactivation endpoints (checkout + confirm)"
```

---

## Task 6: Wire the "subscription ended" email through Resend

**Files:**
- Modify: `backend/services/billing.py`
- Modify: `tests/test_billing.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_billing.py`:

```python
async def test_send_subscription_ended_email_calls_resend(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    """When RESEND_API_KEY is set, the helper calls resend.Emails.send with
    a properly formatted body addressed to the OG's manager(s)."""
    from backend.services.billing import send_subscription_ended_email
    from backend.models import User
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

    sent = []

    class FakeEmails:
        @staticmethod
        def send(payload):
            sent.append(payload)

    class FakeResend:
        api_key = None
        Emails = FakeEmails()

    monkeypatch.setitem(__import__("sys").modules, "resend", FakeResend)

    await send_subscription_ended_email(og_with_card)

    assert len(sent) == 1
    body = sent[0]
    assert "manager@acme.test" in body["to"]
    assert "subscription" in body["subject"].lower()
    assert "90" in body["html"] or "ninety" in body["html"].lower()


async def test_send_subscription_ended_email_noop_without_resend_key(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    """No exception, no email when RESEND_API_KEY is unset."""
    from backend.services.billing import send_subscription_ended_email
    monkeypatch.setattr(settings, "RESEND_API_KEY", "")
    # Should not raise even though we haven't stubbed resend module.
    await send_subscription_ended_email(og_with_card)
```

- [ ] **Step 2: Run, verify failure**

Run: `pytest tests/test_billing.py -k "send_subscription_ended_email" --no-header -q`
Expected: 1 FAIL (the stub doesn't actually call Resend), 1 PASS (the noop branch is already correct)

- [ ] **Step 3: Replace the stub with a real implementation**

In `backend/services/billing.py`, replace the body of `send_subscription_ended_email`:

```python
async def send_subscription_ended_email(og: OwnershipGroup) -> None:
    """Send the 'your subscription has ended' email via Resend.

    Idempotency is the caller's responsibility (the webhook handler checks
    notified_subscription_ended_at before invoking). Non-fatal — exceptions
    are logged and swallowed so a Resend outage doesn't drop the webhook.
    """
    if not settings.RESEND_API_KEY:
        logger.info("Skipping subscription-ended email for og=%s (RESEND_API_KEY unset)", og.id)
        return

    # Look up all manager emails in the OG so they all get the notification.
    from sqlalchemy import select
    from backend.models import Company, User

    # The OG row was passed in already-loaded; the caller may not have an open
    # session, so we fetch managers via a fresh session.
    from backend.database import async_session_factory
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
        logger.info("No manager emails found for og=%s; subscription-ended email skipped", og.id)
        return

    try:
        import resend
        resend.api_key = settings.RESEND_API_KEY
        resend.Emails.send({
            "from": settings.FROM_EMAIL,
            "to": list(manager_emails),
            "subject": "Your WizScheduler subscription has ended",
            "html": (
                f'<div style="font-family:sans-serif;max-width:600px;margin:0 auto;">'
                f"<p>Hi {og.name},</p>"
                f"<p>Your WizScheduler subscription has been canceled. "
                f"You still have read-only access to your data.</p>"
                f"<p><strong>You have 90 days to reactivate</strong> before "
                f"your data is permanently deleted.</p>"
                f'<p>To resume scheduling, log in and click <em>Reactivate '
                f"Subscription</em>.</p>"
                f"</div>"
            ),
        })
    except Exception as e:
        logger.error("Failed to send subscription-ended email for og=%s: %s", og.id, e)
```

- [ ] **Step 4: Run, verify pass**

Run: `pytest tests/test_billing.py -k "send_subscription_ended_email" --no-header -q`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/billing.py tests/test_billing.py
git commit -m "feat(billing): wire subscription-ended email via Resend"
```

---

## Task 7: Frontend API client + types

**Files:**
- Modify: `frontend/src/api/billing.ts`

- [ ] **Step 1: Extend the BillingUsage type and add the reactivation functions**

In `frontend/src/api/billing.ts`, find the `BillingUsage` interface (already exists from PR 2) and add three optional fields:

```typescript
export interface BillingUsage {
  // ...existing fields...
  is_read_only?: boolean;
  canceled_at?: string | null;
  scheduled_deletion_at?: string | null;
}
```

And append two new functions:

```typescript
export function reactivateCheckout(): Promise<{ session_id: string; url: string }> {
  return apiFetch<{ session_id: string; url: string }>("/billing/reactivate-checkout", {
    method: "POST",
  });
}

export function confirmReactivation(
  sessionId: string
): Promise<{ reactivated: boolean; subscription_id: string }> {
  return apiFetch<{ reactivated: boolean; subscription_id: string }>(
    "/billing/confirm-reactivation",
    {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId }),
    }
  );
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && ./node_modules/.bin/tsc --noEmit`
Expected: clean (exit 0)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/billing.ts
git commit -m "feat(billing-fe): reactivateCheckout + confirmReactivation API client"
```

---

## Task 8: Schedule.tsx — "Manage Billing" button + Cancellation card

**Files:**
- Modify: `frontend/src/pages/manager/Schedule.tsx`

- [ ] **Step 1: Add a "Manage Billing" button to the Auto-Reload modal footer**

Locate the Auto-Reload Settings Modal block (search for `t.schedule.autoReloadTitle` near line 1295). In the modal's footer (where Save/Cancel buttons live), add a `Manage Billing` button that calls `handleOpenPortal` (already exists from PR 1):

In the non-editing branch of the modal footer (where the Close + Edit buttons render), prepend a Manage Billing button:

```tsx
<button
  type="button"
  onClick={handleOpenPortal}
  className="glass-btn-secondary text-sm font-medium mr-auto"
>
  {t.schedule.manageBilling}
</button>
```

(`mr-auto` pushes it to the left while Close/Edit stay on the right.)

- [ ] **Step 2: Add the Cancellation card render**

The Auto-Reload card on the Schedule page renders inside the body. Find the existing card render (search for `autoReload && (` near the top of the JSX). Replace the conditional shape so that when the OG is canceled, a different card renders:

Find this block (or equivalent):

```tsx
{autoReload && (
  <div ...> /* Auto-Reload card */ </div>
)}
```

Add a new state slot near the other useState declarations:

```typescript
const [reactivating, setReactivating] = useState(false);
```

Add a handler near `handleSaveAutoReload`:

```typescript
const handleReactivate = async () => {
  setReactivating(true);
  try {
    const { url } = await billingApi.reactivateCheckout();
    window.location.href = url;
  } catch (err: unknown) {
    setActionError(err instanceof Error ? err.message : "Could not start reactivation");
    setReactivating(false);
  }
};
```

Replace the `{autoReload && (` JSX with:

```tsx
{billingUsage?.is_read_only ? (
  <div className="mb-4 p-6 rounded-lg border border-red-300 bg-red-50">
    <h3 className="text-lg font-semibold text-red-900 mb-2">
      {t.schedule.cancellationCardTitle}
    </h3>
    <p className="text-sm text-red-800 mb-1">
      {t.schedule.cancellationEndedOn.replace(
        "{date}",
        billingUsage.canceled_at ? new Date(billingUsage.canceled_at).toLocaleDateString() : ""
      )}
    </p>
    <p className="text-sm text-red-800 mb-4">
      {t.schedule.cancellationDeletionOn.replace(
        "{date}",
        billingUsage.scheduled_deletion_at
          ? new Date(billingUsage.scheduled_deletion_at).toLocaleDateString()
          : ""
      )}
    </p>
    <button
      onClick={handleReactivate}
      disabled={reactivating}
      className="px-4 py-2 bg-red-600 text-white rounded font-medium hover:bg-red-700 disabled:opacity-50"
    >
      {reactivating ? t.schedule.redirectingToPayment : t.schedule.reactivateSubscription}
    </button>
  </div>
) : autoReload && (
  /* ...existing Auto-Reload card JSX, unchanged... */
)}
```

(Leave the existing Auto-Reload card JSX exactly as-is inside the `else` branch.)

- [ ] **Step 3: Verify TS compiles**

Run: `cd frontend && ./node_modules/.bin/tsc --noEmit`
Expected: clean (the new i18n keys will not exist yet — Task 11 adds them; for now, TS may surface "Property does not exist" errors on `t.schedule.manageBilling`, `t.schedule.cancellationCardTitle`, etc. If that's the case, defer to Task 11 — the build will go green once those keys are added).

- [ ] **Step 4: Commit (even if tsc has temp errors on missing i18n keys)**

```bash
git add frontend/src/pages/manager/Schedule.tsx
git commit -m "feat(billing-fe): Manage Billing button + Cancellation card on Schedule page"
```

---

## Task 9: Sitewide CancellationBanner component

**Files:**
- Create: `frontend/src/components/shared/CancellationBanner.tsx`
- Modify: `frontend/src/App.tsx` (mount the banner inside the authenticated layout)

- [ ] **Step 1: Create the component**

Create `frontend/src/components/shared/CancellationBanner.tsx`:

```tsx
import { useEffect, useState } from "react";
import * as billingApi from "../../api/billing";
import type { BillingUsage } from "../../api/billing";
import { useLanguage } from "../../i18n/LanguageContext";

/**
 * Sitewide red banner that renders when the current manager's OG is in
 * the read-only grace period. Polls /billing/usage once on mount and
 * once every 60s while visible. Shows "Subscription ended on X. Data
 * deletion on Y. [Reactivate]".
 */
export default function CancellationBanner() {
  const { t } = useLanguage();
  const [usage, setUsage] = useState<BillingUsage | null>(null);
  const [reactivating, setReactivating] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const fetch = () => {
      billingApi.getUsage().then((u) => {
        if (!cancelled) setUsage(u);
      }).catch(() => {});
    };
    fetch();
    const id = setInterval(fetch, 60_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  if (!usage?.is_read_only) return null;

  const handleReactivate = async () => {
    setReactivating(true);
    try {
      const { url } = await billingApi.reactivateCheckout();
      window.location.href = url;
    } catch {
      setReactivating(false);
    }
  };

  const endDate = usage.canceled_at ? new Date(usage.canceled_at).toLocaleDateString() : "";
  const deleteDate = usage.scheduled_deletion_at
    ? new Date(usage.scheduled_deletion_at).toLocaleDateString()
    : "";

  return (
    <div className="bg-red-600 text-white text-sm px-4 py-2 flex items-center justify-between flex-wrap gap-2">
      <div>
        <strong>{t.cancellationBanner.title}</strong>{" "}
        {t.cancellationBanner.body
          .replace("{endDate}", endDate)
          .replace("{deleteDate}", deleteDate)}
      </div>
      <button
        onClick={handleReactivate}
        disabled={reactivating}
        className="bg-white text-red-700 px-3 py-1 rounded text-xs font-semibold hover:bg-red-50 disabled:opacity-50"
      >
        {reactivating ? t.schedule.redirectingToPayment : t.schedule.reactivateSubscription}
      </button>
    </div>
  );
}
```

- [ ] **Step 2: Mount the banner inside the authenticated layout**

In `frontend/src/App.tsx`, find where the authenticated layout renders (where `<TopBar />` or the sidebar is rendered for `/manager/...` routes). Add the banner inside that layout, above the routed content:

```tsx
import CancellationBanner from "./components/shared/CancellationBanner";
// ...
<CancellationBanner />
{/* existing routed content */}
```

The exact insertion point depends on App.tsx's structure — anywhere inside the authenticated layout that's above the `<Routes>` block.

- [ ] **Step 3: Verify TS compiles**

Run: `cd frontend && ./node_modules/.bin/tsc --noEmit`
Expected: cleanup of any `cancellationBanner.title`/etc. — defer to Task 11 if missing.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/shared/CancellationBanner.tsx frontend/src/App.tsx
git commit -m "feat(billing-fe): sitewide CancellationBanner component"
```

---

## Task 10: Frontend reactivation-redirect handler

**Files:**
- Modify: `frontend/src/pages/manager/Dashboard.tsx` (or wherever the manager lands after Checkout)

- [ ] **Step 1: Add a useEffect that handles the reactivate_session_id query param**

In `frontend/src/pages/manager/Dashboard.tsx`, add at the top of the component body:

```tsx
import { useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import * as billingApi from "../../api/billing";
```

And inside the Dashboard component:

```tsx
const [searchParams, setSearchParams] = useSearchParams();

useEffect(() => {
  const sessionId = searchParams.get("reactivate_session_id");
  if (!sessionId) return;
  searchParams.delete("reactivate_session_id");
  setSearchParams(searchParams, { replace: true });

  billingApi.confirmReactivation(sessionId)
    .then(() => {
      // Force a hard refresh so the CancellationBanner re-fetches /billing/usage
      // and disappears.
      window.location.reload();
    })
    .catch((err) => {
      console.error("Reactivation confirmation failed", err);
    });
}, []); // eslint-disable-line react-hooks/exhaustive-deps
```

- [ ] **Step 2: Verify TS compiles**

Run: `cd frontend && ./node_modules/.bin/tsc --noEmit`
Expected: clean (no new i18n keys here).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/manager/Dashboard.tsx
git commit -m "feat(billing-fe): handle reactivate_session_id redirect on Dashboard"
```

---

## Task 11: i18n keys across 19 locales

**Files:**
- Modify: `frontend/src/i18n/en.ts`
- Modify: `frontend/src/i18n/types.ts`
- Modify: 18 other locale files (`ar bn de es fr hi id ja mr pcm pt ru ta te tr ur vi zh`)

- [ ] **Step 1: Add the new English keys**

In `frontend/src/i18n/en.ts`, inside the `schedule:` block (after `updateCard:`), add:

```typescript
    manageBilling: "Manage Billing",
    cancellationCardTitle: "Subscription canceled",
    cancellationEndedOn: "Your subscription ended on {date}.",
    cancellationDeletionOn: "Your data will be permanently deleted on {date}.",
    reactivateSubscription: "Reactivate Subscription",
```

And as a NEW top-level object at the end of the `en.ts` exports (just before `} as const;`):

```typescript
  cancellationBanner: {
    title: "Subscription ended.",
    body: "Your subscription ended on {endDate}. Your data will be permanently deleted on {deleteDate} unless you reactivate.",
  },
```

- [ ] **Step 2: Add the keys to the type definitions**

In `frontend/src/i18n/types.ts`, the type is inferred from `en.ts` via `DeepStringify<typeof en>` — no manual edit needed there; TS will complain in other locale files until they catch up.

- [ ] **Step 3: Use a Python script to inject the same keys (English placeholders) into all 18 non-English locale files**

From the repo root, run:

```bash
python3 - <<'PYEOF'
import re, pathlib

LOCALES = ["ar","bn","de","es","fr","hi","id","ja","mr","pcm","pt","ru","ta","te","tr","ur","vi","zh"]

SCHEDULE_INSERT = """\
    manageBilling: "Manage Billing",
    cancellationCardTitle: "Subscription canceled",
    cancellationEndedOn: "Your subscription ended on {date}.",
    cancellationDeletionOn: "Your data will be permanently deleted on {date}.",
    reactivateSubscription: "Reactivate Subscription",
"""

BANNER_BLOCK = """\

  cancellationBanner: {
    title: "Subscription ended.",
    body: "Your subscription ended on {endDate}. Your data will be permanently deleted on {deleteDate} unless you reactivate.",
  },
"""

root = pathlib.Path("frontend/src/i18n")
for loc in LOCALES:
    p = root / f"{loc}.ts"
    text = p.read_text()
    # Inject schedule keys right after updateCard
    text = re.sub(
        r'(    updateCard: "[^"]+",\n)',
        r'\1' + SCHEDULE_INSERT,
        text,
        count=1,
    )
    # Inject cancellationBanner block right before "} as const;"
    text = re.sub(r'(} as const;\s*)$', BANNER_BLOCK + r'\1', text, count=1)
    p.write_text(text)
    print(f"updated {loc}.ts")
PYEOF
```

(Translations come later — these are English placeholders, same approach as PR #20 and PR #21.)

- [ ] **Step 4: Verify TypeScript compiles**

Run: `cd frontend && ./node_modules/.bin/tsc --noEmit`
Expected: clean (exit 0).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/i18n/
git commit -m "feat(billing-fe): i18n keys for cancellation lifecycle (en.ts + 18 placeholders)"
```

---

## Task 12: Stripe Customer Portal config + final verification + PR

- [ ] **Step 1: Verify the Stripe Customer Portal config in the dashboard**

Open Stripe Dashboard → Settings → Billing → Customer Portal. Verify:
- ✅ **Cancel subscriptions** is enabled
- Cancellation mode: "Cancel at end of billing period"
- ✅ "Update payment method" enabled
- ✅ "View invoice history" enabled

If "Cancel subscriptions" is OFF, customers won't see a cancel option in the Portal — toggle it on and save.

- [ ] **Step 2: Add `customer.subscription.updated` and `customer.subscription.deleted` to the existing webhook subscription**

Stripe Dashboard → Developers → Webhooks → your `wizscheduler.com/api/v1/webhooks/stripe` endpoint → Update endpoint → in the event list, check:
- ✅ `customer.subscription.updated`
- ✅ `customer.subscription.deleted`

Save.

- [ ] **Step 3: Run the full backend test suite**

Run: `pytest tests/ --no-header -q`
Expected: all passing.

- [ ] **Step 4: Run the frontend build**

Run: `cd frontend && ./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/vite build`
Expected: clean build.

- [ ] **Step 5: Push and open the PR**

```bash
git push -u origin <branch>
gh pr create --title "Subscription cancellation lifecycle (PR α: user-facing cancel + reactivation)" --body "$(cat <<'EOF'
## Summary
- New `canceled_at` (+ 3 notification timestamps) column on `ownership_groups`
- Two new webhook handlers: `customer.subscription.updated` (audit-log) and `customer.subscription.deleted` (sets `canceled_at`, sends email)
- New endpoints: `POST /billing/reactivate-checkout`, `POST /billing/confirm-reactivation`
- New dependency `require_active_billing`; applied to `/schedules/generate`
- `GET /billing/usage` now includes `is_read_only`, `canceled_at`, `scheduled_deletion_at`
- Frontend: "Manage Billing" button on Auto-Reload modal, Cancellation card replacing Auto-Reload when canceled, sitewide red banner with Reactivate button
- Subscription-ended email via Resend (other lifecycle emails land in PR β)

## Deploy steps
1. Merge → CI applies migration `0022` and deploys
2. **In Stripe Dashboard**: enable "Cancel subscriptions" in Customer Portal config, add `customer.subscription.updated` and `customer.subscription.deleted` to the webhook subscription
3. Smoke-test on live: log in as a manager, click Manage Billing in the Auto-Reload modal, verify the Portal opens with a Cancel option

## Test plan
- [x] All pytest tests pass (backend + new ones for webhook, reactivation, access-control denial)
- [x] Frontend tsc + vite build pass
- [ ] After deploy: test cancel flow against a test-mode subscription (Stripe test card, cancel in Portal, verify webhook hits and OG.canceled_at gets set in DB)
- [ ] After deploy: reactivation flow (click Reactivate in banner → Stripe Checkout → return to dashboard → banner disappears)

## Out of scope (PR β)
- Daily deletion cron
- 14-day reminder email
- Final "data deleted" email
- Hard-delete via CASCADE

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review

**Spec coverage check** (against `docs/superpowers/specs/2026-05-14-subscription-cancellation-design.md`):
- ✅ State machine: 4 states represented in code (active/canceled_at/scheduled_deletion_at)
- ✅ Data model: 4 columns added (Task 1)
- ✅ Webhook `customer.subscription.updated` handler: Task 2
- ✅ Webhook `customer.subscription.deleted` handler: Task 2
- ✅ Access control via `require_active_billing` on `/schedules/generate`: Task 3
- ✅ `GET /billing/usage` augmented: Task 4
- ✅ Reactivation endpoints: Task 5
- ✅ Subscription-ended email: Task 6
- ✅ Frontend Manage Billing button: Task 8
- ✅ Cancellation card: Task 8
- ✅ Sitewide banner: Task 9
- ✅ Reactivation redirect handler: Task 10
- ✅ i18n: Task 11
- ✅ Stripe Customer Portal config verification: Task 12
- ✅ Webhook event subscription on Stripe side: Task 12

Items deferred to PR β (out of scope, correctly excluded):
- Daily deletion cron
- 14-day reminder email
- Final "data deleted" email
- Hard-delete with CASCADE
- Audit table for deleted OGs (chosen YAGNI in spec)

**Type consistency:** `canceled_at`, `notified_subscription_ended_at`, `notified_deletion_reminder_at`, `notified_data_deleted_at` used consistently throughout. `require_active_billing` referenced in Tasks 3 and (implicitly) elsewhere with consistent signature. `BillingUsage` interface fields match what backend returns.

**No placeholders found.** Every step has runnable code or a runnable command with expected output.
