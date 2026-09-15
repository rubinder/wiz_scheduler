# Paid AI Credits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** AI Generate runs only on purchased credit packs; the $2 bundled LLM grant is gone, new groups start with auto-reload off, and the operator gets an email per credit charge naming the Anthropic-side amount to buy.

**Architecture:** The billing math in `backend/services/billing.py` keeps its cost-split code but the grant knob defaults to zero, so every generation is charged at 130% and debited from `OwnershipGroup.ai_credits_usd`. The Stripe charge moves into one `charge_saved_card` helper used by both auto-reload and a new `POST /billing/credits/purchase`. The pre-generation gate in `check_ai_credits` requires a positive balance and tells the Schedule page to open a purchase modal. A new `operator_alerts` service emails the operator after each successful charge.

**Tech Stack:** FastAPI, SQLAlchemy 2.x async, Alembic, Stripe Python SDK (off-session PaymentIntent), Resend, pytest + pytest-asyncio on SQLite (`tests/conftest.py`), React 18 + TypeScript + Tailwind, vitest.

**Spec:** `docs/superpowers/specs/2026-09-15-paid-ai-credits-design.md`

## Global Constraints

- Backend tests run from the repo root with `cd /Users/robran/IdeaProjects/wiz_scheduler && backend/.venv/bin/python -m pytest tests/<file> -q` (SQLite, no Postgres needed). Frontend checks: `cd frontend && npm run build && npm test`.
- Every new `User`/`Employee`/`Location` rule in `CLAUDE.md` is untouched by this work; do not add dependencies.
- "Today" is always `datetime.now(timezone.utc)`; never `date.today()`.
- Tailwind: logical utilities only (`ms-`, `me-`, `text-start`, `text-end`, `border-s`). `frontend/src/utils/logicalDirection.test.ts` fails the build otherwise.
- `frontend/src/i18n/types.ts` defines `TranslationKeys = typeof en`, so every key added to or removed from `en.ts` must change in all 19 locale files in the same commit or `npm run build` fails.
- Every commit message ends with:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01FvGdUM2BnEED7pBZSADz61
  ```
- Never raise out of the post-generation billing path (`check_and_record_usage`, `deduct_credits_for_schedule_overage`) for a payment outcome; the pre-generation gate is the consent point.
- Config values used verbatim: `INCLUDED_LLM_USD = 0.00`, `LLM_OVERAGE_MARKUP = 1.30`, `AI_CREDIT_PACKS_USD = (10.0, 25.0, 50.0)`, `OPERATOR_ALERT_EMAIL = ""`.

## File map

| File | Responsibility in this change |
|---|---|
| `backend/config.py` | Knob to 0.00; add `AI_CREDIT_PACKS_USD`, `OPERATOR_ALERT_EMAIL` |
| `backend/services/billing.py` | `charge_saved_card`; `auto_reload_if_needed` delegates; no-raise debit paths; `check_ai_credits` gate fields |
| `backend/services/operator_alerts.py` (new) | `send_credit_purchase_alert` |
| `backend/routers/billing.py` | `POST /billing/credits/purchase` |
| `backend/routers/schedules.py` | Structured 402 for AI credits |
| `backend/models/ownership_group.py`, `backend/models/billing_charge.py` | New defaults / constraint values |
| `backend/alembic/versions/0035_paid_ai_credits.py` (new) | Default flip + constraint widen |
| `tests/test_billing.py` | All backend billing tests (fixtures `seed_og`, `og_with_card` live here) |
| `tests/test_schedules.py` | Generate-gate 402 tests |
| `tests/test_operator_alerts.py` (new) | Alert service tests |
| `frontend/src/api/billing.ts` | Types + `purchaseCredits` |
| `frontend/src/pages/manager/Schedule.tsx` | Credit strip + purchase modal |
| `frontend/src/pages/Landing.tsx` | Pricing tile, notes, example bill |
| `frontend/src/i18n/*.ts` (19 files) | Keys |
| `CLAUDE.md`, spec | Docs |

---

### Task 1: Zero the grant and prove the math

**Files:**
- Modify: `backend/config.py:120-125`
- Test: `tests/test_billing.py`

**Interfaces:**
- Produces: `settings.INCLUDED_LLM_USD == 0.0`, `settings.AI_CREDIT_PACKS_USD == (10.0, 25.0, 50.0)`, `settings.OPERATOR_ALERT_EMAIL == ""`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_billing.py` after `test_check_and_record_usage_over_free_tier`:

```python
async def test_check_and_record_usage_full_markup_at_zero_grant(db_session: AsyncSession, seed_og):
    """With no bundled grant, the very first generation of a month is charged
    at LLM_OVERAGE_MARKUP; nothing is absorbed."""
    assert settings.INCLUDED_LLM_USD == 0.0
    seed_og.ai_credits_usd = 100.0  # keeps auto-reload out of this test
    await db_session.commit()

    first = await check_and_record_usage(db_session, COMPANY_ID, 1000, 500)
    assert first["cost_usd"] > 0
    assert first["charged_usd"] == round(first["cost_usd"] * settings.LLM_OVERAGE_MARKUP, 6)
    assert first["is_over_included"] is True
    assert first["included_remaining_usd"] == 0

    second = await check_and_record_usage(db_session, COMPANY_ID, 1000, 500)
    assert second["charged_usd"] == round(second["cost_usd"] * settings.LLM_OVERAGE_MARKUP, 6)
    assert second["monthly_charged_usd"] == pytest.approx(first["charged_usd"] + second["charged_usd"])


async def test_check_and_record_usage_splits_when_grant_configured(
    db_session: AsyncSession, seed_og, monkeypatch
):
    """A demo environment may set INCLUDED_LLM_USD > 0; the split still works."""
    monkeypatch.setattr(settings, "INCLUDED_LLM_USD", 2.0)
    seed_og.ai_credits_usd = 100.0
    await db_session.commit()

    result = await check_and_record_usage(db_session, COMPANY_ID, 1000, 500)
    assert result["charged_usd"] == 0.0
    assert result["is_over_included"] is False
    assert result["included_remaining_usd"] == pytest.approx(2.0 - result["cost_usd"])


def test_credit_pack_config():
    assert settings.AI_CREDIT_PACKS_USD == (10.0, 25.0, 50.0)
    assert settings.OPERATOR_ALERT_EMAIL == ""
```

- [ ] **Step 2: Update the two existing tests that assumed a $2 grant**

Replace `test_check_and_record_usage_within_free_tier` (around line 150) with:

```python
async def test_check_and_record_usage_within_included_grant(
    db_session: AsyncSession, seed_og, monkeypatch
):
    monkeypatch.setattr(settings, "INCLUDED_LLM_USD", 2.0)
    result = await check_and_record_usage(db_session, COMPANY_ID, 1000, 500)
    assert result["cost_usd"] > 0
    assert result["charged_usd"] == 0.0
    assert result["is_over_included"] is False
    assert result["included_remaining_usd"] > 0
```

Replace `test_check_ai_credits_within_free_tier` (around line 183) with:

```python
async def test_check_ai_credits_within_included_grant(
    db_session: AsyncSession, seed_og, monkeypatch
):
    monkeypatch.setattr(settings, "INCLUDED_LLM_USD", 2.0)
    result = await check_ai_credits(db_session, COMPANY_ID)
    assert result["can_generate"] is True
    assert result["is_over_included"] is False
    assert result["included_remaining_usd"] == 2.0
```

- [ ] **Step 3: Run the tests to verify the new ones fail**

Run: `backend/.venv/bin/python -m pytest tests/test_billing.py -q -k "zero_grant or pack_config or included_grant"`
Expected: `test_check_and_record_usage_full_markup_at_zero_grant` and `test_credit_pack_config` FAIL (grant is 2.0; `AI_CREDIT_PACKS_USD` missing). The two `included_grant` tests PASS.

- [ ] **Step 4: Change the config**

In `backend/config.py`, replace the `# LLM billing` block:

```python
    # LLM billing. AI Generate is NOT included in the subscription (#64):
    # every dollar of token cost is charged at LLM_OVERAGE_MARKUP and
    # debited from purchased credits (OwnershipGroup.ai_credits_usd).
    # INCLUDED_LLM_USD survives as a knob so a demo environment can grant
    # some spend; production leaves it at zero.
    INCLUDED_LLM_USD: float = 0.00
    LLM_OVERAGE_MARKUP: float = 1.30         # 130% of token cost
    LLM_INPUT_COST_PER_M: float = 2.00       # $ per 1M input tokens (Claude Sonnet 5)
    LLM_OUTPUT_COST_PER_M: float = 10.00     # $ per 1M output tokens (Claude Sonnet 5)

    # Fixed credit packs a paid group may buy (POST /billing/credits/purchase).
    # Charged off-session to the subscription's saved card.
    AI_CREDIT_PACKS_USD: tuple[float, ...] = (10.0, 25.0, 50.0)

    # Where operator alerts go. Every successful credit charge emails this
    # address the Anthropic-side amount to top up (amount / markup). Empty
    # disables the email; the log line is always written.
    OPERATOR_ALERT_EMAIL: str = ""
```

Also update the comment block above (`Metered allowances INCLUDED IN THE $18 SUBSCRIPTION`) by appending one line after the last sentence: `# AI spend left this family on 2026-09-15 (#64); see INCLUDED_LLM_USD below.`

- [ ] **Step 5: Run the billing tests**

Run: `backend/.venv/bin/python -m pytest tests/test_billing.py -q`
Expected: all PASS.

- [ ] **Step 6: Run the whole suite to find other tests that leaned on the grant**

Run: `backend/.venv/bin/python -m pytest tests -q -x`
Expected: `tests/test_schedules.py::test_generate_ai_returns_402_when_daily_cost_cap_exceeded` FAILS (its paid OG has a zero balance, and the credit gate now runs first). Fix it by changing `ai_credits_usd=0.0` to `ai_credits_usd=5.0` in that test's `OwnershipGroup(...)` at `tests/test_schedules.py:720` with the comment `# funded: the credit gate runs before the daily cap (#64)`. Rerun; expected all PASS. If anything else fails, it is a real regression: stop and report.

- [ ] **Step 7: Commit**

```bash
git add backend/config.py tests/test_billing.py tests/test_schedules.py
git commit -m "feat(billing): AI spend is no longer included in the subscription (#64)"
```

---

### Task 2: Migration 0035 — auto-reload opt-in, purchase charge kind

**Files:**
- Create: `backend/alembic/versions/0035_paid_ai_credits.py`
- Modify: `backend/models/ownership_group.py:24-26`, `backend/models/billing_charge.py:29-32`
- Test: `tests/test_billing.py` (fixture `og_with_card` + two new tests)

**Interfaces:**
- Produces: new ownership groups have `autoreload_enabled == False`; `BillingCharge.kind` accepts `'purchase'`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_billing.py` after `test_billing_charge_model_round_trips`:

```python
async def test_new_ownership_group_starts_with_autoreload_off(db_session: AsyncSession):
    """Opt-in, not opt-out (#64): a fresh group must not be charged automatically."""
    og = OwnershipGroup(id=_id(), name="Fresh")
    db_session.add(og)
    await db_session.commit()
    await db_session.refresh(og)
    assert og.autoreload_enabled is False


async def test_billing_charge_accepts_purchase_kind(db_session: AsyncSession, seed_og):
    db_session.add(BillingCharge(
        ownership_group_id=OG_ID, kind="purchase", amount_usd=10.0,
        stripe_object_id="pi_x", status="succeeded",
    ))
    await db_session.commit()
    row = (await db_session.execute(select(BillingCharge))).scalar_one()
    assert row.kind == "purchase"
```

- [ ] **Step 2: Update the `og_with_card` fixture**

The fixture models a group that has opted into auto-reload; every reload test relies on that. Change it to:

```python
@pytest_asyncio.fixture
async def og_with_card(db_session: AsyncSession, seed_og):
    """Paid OG with a cached payment method that has opted into auto-reload.

    Auto-reload is opt-in since #64, so the fixture says so explicitly
    rather than leaning on the column default.
    """
    seed_og.stripe_customer_id = "cus_test_abc"
    seed_og.stripe_subscription_id = "sub_test_123"
    seed_og.default_payment_method_id = "pm_test_card_456"
    seed_og.autoreload_enabled = True
    await db_session.commit()
    return seed_og
```

- [ ] **Step 3: Run to verify failure**

Run: `backend/.venv/bin/python -m pytest tests/test_billing.py -q -k "starts_with_autoreload_off or accepts_purchase_kind"`
Expected: both FAIL (default is true; CHECK constraint rejects `purchase`).

- [ ] **Step 4: Change the models**

`backend/models/ownership_group.py`:

```python
    # Opt-in since #64: a customer buys a pack first, and may tick
    # "auto-reload" in the purchase modal. Existing rows kept their value.
    autoreload_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
```

`backend/models/billing_charge.py`:

```python
        CheckConstraint(
            "kind IN ('autoreload', 'purchase', 'invoice_item_storage', 'invoice_item_employees')",
            name="billing_charges_kind_check",
        ),
```

- [ ] **Step 5: Write the migration**

Create `backend/alembic/versions/0035_paid_ai_credits.py`:

```python
"""paid AI credits: auto-reload opt-in, 'purchase' charge kind

Revision ID: 0035
Revises: 0034
Create Date: 2026-09-15 00:00:00.000000

Two changes for #64. The default for ownership_groups.autoreload_enabled
flips to false so a new group is never charged automatically until it
opts in from the purchase modal; existing rows keep their current value.
billing_charges.kind gains 'purchase' for explicit credit-pack charges.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0035"
down_revision: Union[str, None] = "0034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


KINDS_NEW = "kind IN ('autoreload', 'purchase', 'invoice_item_storage', 'invoice_item_employees')"
KINDS_OLD = "kind IN ('autoreload', 'invoice_item_storage', 'invoice_item_employees')"


def upgrade() -> None:
    op.alter_column(
        "ownership_groups", "autoreload_enabled",
        server_default=sa.text("false"), existing_type=sa.Boolean(), existing_nullable=False,
    )
    op.drop_constraint("billing_charges_kind_check", "billing_charges", type_="check")
    op.create_check_constraint("billing_charges_kind_check", "billing_charges", KINDS_NEW)


def downgrade() -> None:
    # Rows with kind='purchase' would violate the old constraint; that is the
    # correct signal rather than something to delete silently.
    op.drop_constraint("billing_charges_kind_check", "billing_charges", type_="check")
    op.create_check_constraint("billing_charges_kind_check", "billing_charges", KINDS_OLD)
    op.alter_column(
        "ownership_groups", "autoreload_enabled",
        server_default=sa.text("true"), existing_type=sa.Boolean(), existing_nullable=False,
    )
```

- [ ] **Step 6: Run the tests**

Run: `backend/.venv/bin/python -m pytest tests/test_billing.py -q`
Expected: all PASS.

Also run `backend/.venv/bin/python -m pytest tests -q` to confirm nothing else depended on the old default. Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/alembic/versions/0035_paid_ai_credits.py backend/models/ownership_group.py backend/models/billing_charge.py tests/test_billing.py
git commit -m "feat(db): auto-reload is opt-in; billing_charges accepts kind=purchase (#64)"
```

---
### Task 3: Extract `charge_saved_card` from auto-reload

**Files:**
- Modify: `backend/services/billing.py:818-912` (`auto_reload_if_needed`)
- Test: `tests/test_billing.py` (existing `test_auto_reload_*` tests are the safety net)

**Interfaces:**
- Produces: `async def charge_saved_card(db: AsyncSession, og: OwnershipGroup, amount_usd: float, kind: str) -> BillingCharge`. Raises `AutoReloadError` on decline; never touches `autoreload_failed_at`.
- `auto_reload_if_needed(db, og, cost_usd)` keeps its signature and behaviour.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_billing.py` after `test_auto_reload_failed_state_raises_blocked_error`:

```python
async def test_charge_saved_card_adds_balance_and_records_kind(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    import stripe
    from backend.services.billing import charge_saved_card

    captured = {}
    def fake_create(**kwargs):
        captured.update(kwargs)
        return MagicMock(status="succeeded", id="pi_pack_1")
    monkeypatch.setattr(stripe.PaymentIntent, "create", fake_create)

    og_with_card.ai_credits_usd = 1.5
    await db_session.commit()

    row = await charge_saved_card(db_session, og_with_card, 25.0, kind="purchase")
    await db_session.commit()

    assert row.kind == "purchase"
    assert row.status == "succeeded"
    assert row.stripe_object_id == "pi_pack_1"
    assert float(row.amount_usd) == 25.0
    assert captured["amount"] == 2500
    assert captured["metadata"] == {"og_id": OG_ID, "kind": "purchase"}
    await db_session.refresh(og_with_card)
    assert og_with_card.ai_credits_usd == 26.5


async def test_charge_saved_card_decline_records_failed_row_without_hold(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    import stripe
    from backend.services.billing import charge_saved_card, AutoReloadError

    def fake_create(**kwargs):
        raise stripe.CardError("card declined", "card_declined", "card_declined")
    monkeypatch.setattr(stripe.PaymentIntent, "create", fake_create)

    with pytest.raises(AutoReloadError):
        await charge_saved_card(db_session, og_with_card, 10.0, kind="purchase")
    await db_session.commit()

    await db_session.refresh(og_with_card)
    assert og_with_card.autoreload_failed_at is None   # the helper never sets the hold
    assert og_with_card.ai_credits_usd == 0.0
    charges = list((await db_session.execute(select(BillingCharge))).scalars())
    assert [(c.kind, c.status) for c in charges] == [("purchase", "failed")]
```

Note: `select` and `BillingCharge` are already imported at the top of `tests/test_billing.py`.

- [ ] **Step 2: Run to verify failure**

Run: `backend/.venv/bin/python -m pytest tests/test_billing.py -q -k charge_saved_card`
Expected: FAIL, `ImportError: cannot import name 'charge_saved_card'`.

- [ ] **Step 3: Implement the helper and delegate**

In `backend/services/billing.py`, add near the top-level imports (keep alphabetical with the existing model imports):

```python
from backend.models.billing_charge import BillingCharge
```

Replace the body of `auto_reload_if_needed` from `stripe.api_key = settings.STRIPE_SECRET_KEY` to the end of the function with:

```python
    try:
        await charge_saved_card(db, og, float(og.autoreload_amount_usd), kind="autoreload")
    except AutoReloadError:
        # Automatic charging failed: put billing on hold so nothing else is
        # attempted until the customer retries from the Billing UI.
        og.autoreload_failed_at = datetime.now(timezone.utc)
        await db.flush()
        raise
```

and delete the now-unused local imports of `stripe`, `settings`, and `BillingCharge` at the top of that function (keep the `datetime`/`timezone` import only if the module does not already import them at the top; it does, so delete it too).

Add, directly above `auto_reload_if_needed`:

```python
async def charge_saved_card(
    db: AsyncSession,
    og: OwnershipGroup,
    amount_usd: float,
    kind: str,
) -> BillingCharge:
    """Charge the subscription's saved card off-session and credit the balance.

    The one place money moves for credits. `kind` is 'autoreload' or
    'purchase' and lands on the BillingCharge row and the PaymentIntent
    metadata. On a decline (StripeError or a non-succeeded intent) a
    'failed' row is written and AutoReloadError is raised; whether that
    puts billing on hold is the caller's decision, so this function never
    touches og.autoreload_failed_at.
    """
    import stripe

    stripe.api_key = settings.STRIPE_SECRET_KEY

    try:
        intent = stripe.PaymentIntent.create(
            customer=og.stripe_customer_id,
            amount=int(round(amount_usd * 100)),
            currency="usd",
            payment_method=og.default_payment_method_id,
            off_session=True,
            confirm=True,
            metadata={"og_id": og.id, "kind": kind},
        )
    except stripe.StripeError as e:
        db.add(BillingCharge(
            ownership_group_id=og.id,
            kind=kind,
            amount_usd=amount_usd,
            stripe_object_id=None,
            status="failed",
            error_message=str(e),
        ))
        await db.flush()
        raise AutoReloadError(str(e))

    if intent.status != "succeeded":
        db.add(BillingCharge(
            ownership_group_id=og.id,
            kind=kind,
            amount_usd=amount_usd,
            stripe_object_id=intent.id,
            status="failed",
            error_message=f"PaymentIntent status={intent.status}",
        ))
        await db.flush()
        raise AutoReloadError(f"PaymentIntent status: {intent.status}")

    og.ai_credits_usd = round(float(og.ai_credits_usd) + amount_usd, 4)
    row = BillingCharge(
        ownership_group_id=og.id,
        kind=kind,
        amount_usd=amount_usd,
        stripe_object_id=intent.id,
        status="succeeded",
    )
    db.add(row)
    await db.flush()
    return row
```

`AutoReloadError` is defined a few lines above; the class definitions stay where they are.

- [ ] **Step 4: Run the billing tests**

Run: `backend/.venv/bin/python -m pytest tests/test_billing.py -q`
Expected: all PASS, including every pre-existing `test_auto_reload_*` and `test_post_autoreload_retry_*` test (the `purchase` kind is accepted because Task 2 widened the model's check constraint).

- [ ] **Step 5: Commit**

```bash
git add backend/services/billing.py tests/test_billing.py
git commit -m "refactor(billing): one charge_saved_card helper behind auto-reload"
```

---
### Task 4: Post-generation billing never raises on a payment outcome

**Files:**
- Modify: `backend/services/billing.py` (`check_and_record_usage` around line 232; `deduct_credits_for_schedule_overage` around line 640)
- Test: `tests/test_billing.py`

**Interfaces:**
- Consumes: `auto_reload_if_needed`, `AutoReloadError` from Task 3.
- Produces: no signature changes.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_billing.py` after `test_check_and_record_usage_triggers_reload_when_over_free_tier`:

```python
async def test_check_and_record_usage_autoreload_off_does_not_raise(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    """Auto-reload off and a short balance: usage is still recorded and the
    later debit floors at zero. The gate, not this path, is where consent lives."""
    import stripe
    def boom(**kwargs):
        raise AssertionError("Stripe must not be called when auto-reload is off")
    monkeypatch.setattr(stripe.PaymentIntent, "create", boom)

    og_with_card.autoreload_enabled = False
    og_with_card.ai_credits_usd = 0.01
    await db_session.commit()

    result = await check_and_record_usage(db_session, str(COMPANY_ID), 1_000_000, 100_000)
    assert result["charged_usd"] > 0.01
    await deduct_credits_for_overage(db_session, str(COMPANY_ID), result["charged_usd"])
    await db_session.commit()

    await db_session.refresh(og_with_card)
    assert og_with_card.ai_credits_usd == 0.0
    assert og_with_card.autoreload_failed_at is None
    usage = (await db_session.execute(select(TokenUsage))).scalar_one()
    assert usage.charged_usd == result["charged_usd"]


async def test_check_and_record_usage_declined_reload_does_not_raise(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    """A declined auto-reload after a generation is recorded (failed row +
    on-hold flag) but does not turn the finished generation into an error."""
    import stripe
    def fake_create(**kwargs):
        raise stripe.CardError("card declined", "card_declined", "card_declined")
    monkeypatch.setattr(stripe.PaymentIntent, "create", fake_create)

    og_with_card.autoreload_enabled = True
    og_with_card.ai_credits_usd = 0.01
    await db_session.commit()

    result = await check_and_record_usage(db_session, str(COMPANY_ID), 1_000_000, 100_000)
    assert result["charged_usd"] > 0
    await db_session.commit()

    await db_session.refresh(og_with_card)
    assert og_with_card.autoreload_failed_at is not None
    charges = list((await db_session.execute(select(BillingCharge))).scalars())
    assert [(c.kind, c.status) for c in charges] == [("autoreload", "failed")]


async def test_deduct_credits_for_schedule_overage_autoreload_off_does_not_raise(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    from backend.models import ShiftSchedule
    from backend.services.billing import deduct_credits_for_schedule_overage

    og_with_card.autoreload_enabled = False
    og_with_card.ai_credits_usd = 0.001
    await db_session.commit()

    now = datetime.now(timezone.utc)
    for _ in range(settings.INCLUDED_SCHEDULES_PER_MONTH + 1):
        db_session.add(ShiftSchedule(
            company_id=COMPANY_ID,
            location_id=_id(),
            week_start_date=now.date(),
            status="DRAFT",
            created_at=now,
        ))
    await db_session.commit()

    await deduct_credits_for_schedule_overage(db_session, str(COMPANY_ID))
    await db_session.commit()

    await db_session.refresh(og_with_card)
    assert og_with_card.ai_credits_usd == 0.0
```

The `ShiftSchedule` construction matches `test_deduct_credits_for_schedule_triggers_reload` in the same file.

- [ ] **Step 2: Run to verify failure**

Run: `backend/.venv/bin/python -m pytest tests/test_billing.py -q -k "does_not_raise"`
Expected: the first FAILS with `AutoReloadDisabled`, the second FAILS with `AutoReloadError`, the third FAILS with `AutoReloadDisabled`.

- [ ] **Step 3: Implement**

In `check_and_record_usage`, replace:

```python
    if this_charge > 0:
        og_result = await db.execute(
            select(OwnershipGroup).where(OwnershipGroup.id == og_id).with_for_update()
        )
        og = og_result.scalar_one()
        await auto_reload_if_needed(db, og, cost_usd=this_charge)
```

with:

```python
    if this_charge > 0:
        og_result = await db.execute(
            select(OwnershipGroup).where(OwnershipGroup.id == og_id).with_for_update()
        )
        og = og_result.scalar_one()
        await _reload_after_debit(db, og, this_charge)
```

In `deduct_credits_for_schedule_overage`, replace `await auto_reload_if_needed(db, og, cost_usd=per_schedule_cost)` with `await _reload_after_debit(db, og, per_schedule_cost)`. Change the floor on the next line to `og.ai_credits_usd = max(0.0, round(float(og.ai_credits_usd) - per_schedule_cost, 4))`.

Add, directly above `check_and_record_usage`:

```python
async def _reload_after_debit(db: AsyncSession, og: OwnershipGroup, cost_usd: float) -> None:
    """Top up after a generation has already spent tokens, if the customer opted in.

    A short balance is not an error here. The pre-generation gates
    (check_ai_credits, check_schedule_quota) are where consent is checked;
    once the tokens are spent the only job is to record what happened. A
    declined card is recorded by auto_reload_if_needed (failed row + on-hold
    flag) and blocks the *next* run at the gate.
    """
    if not og.autoreload_enabled or og.autoreload_failed_at is not None:
        return
    try:
        await auto_reload_if_needed(db, og, cost_usd=cost_usd)
    except AutoReloadError as exc:
        logger.warning("[BILLING] auto-reload declined after debit og=%s: %s", og.id, exc)
```

- [ ] **Step 4: Run the billing tests**

Run: `backend/.venv/bin/python -m pytest tests/test_billing.py -q`
Expected: all PASS (`test_deduct_credits_for_schedule_triggers_reload` and `test_check_and_record_usage_triggers_reload_when_over_free_tier` still pass because `og_with_card` has auto-reload on).

- [ ] **Step 5: Commit**

```bash
git add backend/services/billing.py tests/test_billing.py
git commit -m "fix(billing): a short balance after generation is recorded, not raised"
```

---
### Task 5: The gate requires a positive balance and names the fix

**Files:**
- Modify: `backend/services/billing.py:651-700` (`check_ai_credits`)
- Modify: `backend/routers/schedules.py:97-102`
- Test: `tests/test_billing.py`, `tests/test_schedules.py`

**Interfaces:**
- Produces: `check_ai_credits` dict gains `purchase_required: bool` and `packs_usd: list[float]`. Generate 402 detail: `{"code": "ai_credits_required" | "billing_on_hold", "message": str}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_billing.py` after `test_check_ai_credits_over_free_tier_with_purchased`:

```python
async def test_check_ai_credits_zero_balance_requires_purchase(db_session: AsyncSession, seed_og):
    result = await check_ai_credits(db_session, COMPANY_ID)
    assert result["can_generate"] is False
    assert result["purchase_required"] is True
    assert result["packs_usd"] == [10.0, 25.0, 50.0]
    assert result["purchased_credits_usd"] == 0.0


async def test_check_ai_credits_positive_balance_allows(db_session: AsyncSession, seed_og):
    seed_og.ai_credits_usd = 0.05
    await db_session.commit()
    result = await check_ai_credits(db_session, COMPANY_ID)
    assert result["can_generate"] is True
    assert result["purchase_required"] is False


async def test_check_ai_credits_on_hold_is_not_a_purchase_prompt(db_session: AsyncSession, seed_og):
    seed_og.ai_credits_usd = 20.0
    seed_og.autoreload_failed_at = datetime.now(timezone.utc)
    await db_session.commit()
    result = await check_ai_credits(db_session, COMPANY_ID)
    assert result["can_generate"] is False
    assert result["autoreload_failed"] is True
    assert result["purchase_required"] is False
```

Append to `tests/test_schedules.py` after `test_generate_ai_returns_402_when_daily_cost_cap_exceeded`:

```python
async def test_generate_ai_returns_402_ai_credits_required_when_unfunded(
    client: AsyncClient, manager_token: str, db_session: AsyncSession,
    seeded_company, monkeypatch
):
    """A paid group with a zero balance is told to buy a pack, before any LLM call."""
    from backend.models import OwnershipGroup
    from backend.services.rate_limit import schedule_generate_ai_limiter
    from sqlalchemy import select
    from backend.models import Company

    schedule_generate_ai_limiter.reset()

    og = OwnershipGroup(name="UnfundedOG", ai_credits_usd=0.0, stripe_subscription_id="sub_unfunded")
    db_session.add(og)
    await db_session.flush()
    company = (await db_session.execute(
        select(Company).where(Company.id == seeded_company.company_id)
    )).scalar_one()
    company.ownership_group_id = og.id
    await db_session.commit()

    async def must_not_run(**kwargs):
        raise AssertionError("pipeline must not run without credits")
        yield {}  # pragma: no cover
    monkeypatch.setattr("backend.scheduling.graph.run_scheduling_pipeline", must_not_run)

    resp = await client.post(
        "/api/v1/schedules/generate",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"week_start_date": "2026-05-18", "use_local": False},
    )
    assert resp.status_code == 402
    assert resp.json()["detail"]["code"] == "ai_credits_required"
```

- [ ] **Step 2: Run to verify failure**

Run: `backend/.venv/bin/python -m pytest tests/test_billing.py tests/test_schedules.py -q -k "requires_purchase or positive_balance_allows or purchase_prompt or ai_credits_required"`
Expected: `KeyError: 'purchase_required'` on the three billing tests; the schedules test fails because `detail` is a string.

- [ ] **Step 3: Implement the gate**

Replace `check_ai_credits` in `backend/services/billing.py` with:

```python
async def check_ai_credits(
    db: AsyncSession,
    company_id: str,
) -> dict:
    """Decide whether the ownership group may run AI generation.

    AI spend debits purchased credits (#64). The group may generate when its
    purchased balance is positive, or when INCLUDED_LLM_USD grants spend
    that is not yet used (a demo-environment knob; 0 in production).

    Returns:
        - can_generate: the gate result
        - included_remaining_usd: unused part of INCLUDED_LLM_USD this month
        - purchased_credits_usd: OwnershipGroup.ai_credits_usd
        - is_over_included: monthly cost has reached INCLUDED_LLM_USD
        - monthly_cost_usd: raw token cost this month
        - autoreload_failed: present and true only when billing is on hold
        - purchase_required: true when the only thing missing is a credit pack
        - packs_usd: the packs the Schedule page may offer
    """
    packs = list(settings.AI_CREDIT_PACKS_USD)
    og_id = await get_ownership_group_id(db, company_id)
    if not og_id:
        return {
            "can_generate": True,
            "included_remaining_usd": settings.INCLUDED_LLM_USD,
            "purchased_credits_usd": 0.0,
            "is_over_included": False,
            "monthly_cost_usd": 0.0,
            "purchase_required": False,
            "packs_usd": packs,
        }

    og_full = (await db.execute(select(OwnershipGroup).where(OwnershipGroup.id == og_id))).scalar_one_or_none()
    if og_full and og_full.autoreload_failed_at is not None:
        # On hold after a failed automatic charge: the fix is Retry payment
        # or a new card, not another purchase, so purchase_required is False.
        return {
            "can_generate": False,
            "included_remaining_usd": 0.0,
            "purchased_credits_usd": float(og_full.ai_credits_usd),
            "is_over_included": True,
            "monthly_cost_usd": 0.0,
            "autoreload_failed": True,
            "purchase_required": False,
            "packs_usd": packs,
        }

    usage = await get_monthly_usage(db, og_id)
    monthly_cost = usage.cost_usd if usage else 0.0
    included_remaining = max(0.0, settings.INCLUDED_LLM_USD - monthly_cost)
    is_over = monthly_cost >= settings.INCLUDED_LLM_USD

    purchased_credits = float(og_full.ai_credits_usd) if og_full else 0.0

    can_generate = included_remaining > 0 or purchased_credits > 0

    return {
        "can_generate": can_generate,
        "included_remaining_usd": round(included_remaining, 4),
        "purchased_credits_usd": round(purchased_credits, 4),
        "is_over_included": is_over,
        "monthly_cost_usd": round(monthly_cost, 4),
        "purchase_required": not can_generate,
        "packs_usd": packs,
    }
```

In `backend/routers/schedules.py`, replace the AI-mode 402:

```python
        credit_status = await check_ai_credits(db, str(current_user.company_id))
        if not credit_status["can_generate"]:
            if credit_status.get("purchase_required"):
                detail = {
                    "code": "ai_credits_required",
                    "message": "AI credits are needed for AI Generate. Buy a credit pack to continue.",
                }
            else:
                detail = {
                    "code": "billing_on_hold",
                    "message": "Billing is on hold after a failed payment. Retry payment or update your card in Billing.",
                }
            raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=detail)
```

- [ ] **Step 4: Run the tests**

Run: `backend/.venv/bin/python -m pytest tests/test_billing.py tests/test_schedules.py -q`
Expected: all PASS. `test_check_ai_credits_blocked_when_failed_at_set` still passes.

- [ ] **Step 5: Commit**

```bash
git add backend/services/billing.py backend/routers/schedules.py tests/test_billing.py tests/test_schedules.py
git commit -m "feat(billing): AI gate needs a positive balance and says a pack is required (#64)"
```

---

### Task 6: `POST /billing/credits/purchase`

**Files:**
- Modify: `backend/routers/billing.py` (imports at top; new endpoint after `retry_autoreload`)
- Test: `tests/test_billing.py`

**Interfaces:**
- Consumes: `charge_saved_card` (Task 3), `assert_paid_plan(db, company_id, feature)` from `backend/services/plan.py`, `_load_og`, `_autoreload_status`, `AutoReloadStatus` in the router.
- Produces: `POST /api/v1/billing/credits/purchase` body `{"amount_usd": float, "enable_autoreload": bool = false}` → `AutoReloadStatus`. Error codes: 400 `invalid_pack`, 402 `ai_credits_requires_paid_plan` | `no_payment_method` | `card_declined`, 409 `billing_on_hold`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_billing.py` (new section header `# POST /billing/credits/purchase (#64)`):

```python
PURCHASE_URL = "/api/v1/billing/credits/purchase"


async def test_purchase_pack_adds_balance_and_records_charge(
    client: AsyncClient, manager_token, db_session, og_with_card, monkeypatch
):
    import stripe
    monkeypatch.setattr(stripe.PaymentIntent, "create", lambda **kw: MagicMock(status="succeeded", id="pi_pack_ok"))
    og_with_card.autoreload_enabled = False
    await db_session.commit()

    resp = await client.post(PURCHASE_URL, json={"amount_usd": 10.0},
                             headers={"Authorization": f"Bearer {manager_token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["current_balance_usd"] == 10.0
    assert body["enabled"] is False

    charges = list((await db_session.execute(select(BillingCharge))).scalars())
    assert [(c.kind, c.status, float(c.amount_usd)) for c in charges] == [("purchase", "succeeded", 10.0)]


async def test_purchase_pack_can_opt_into_autoreload(
    client: AsyncClient, manager_token, db_session, og_with_card, monkeypatch
):
    import stripe
    monkeypatch.setattr(stripe.PaymentIntent, "create", lambda **kw: MagicMock(status="succeeded", id="pi_pack_ok"))
    og_with_card.autoreload_enabled = False
    await db_session.commit()

    resp = await client.post(PURCHASE_URL, json={"amount_usd": 25.0, "enable_autoreload": True},
                             headers={"Authorization": f"Bearer {manager_token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["enabled"] is True
    assert body["amount_usd"] == 25.0
    assert body["current_balance_usd"] == 25.0


async def test_purchase_rejects_amount_outside_packs(
    client: AsyncClient, manager_token, og_with_card
):
    resp = await client.post(PURCHASE_URL, json={"amount_usd": 12.0},
                             headers={"Authorization": f"Bearer {manager_token}"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_pack"


async def test_purchase_requires_paid_plan(
    client: AsyncClient, manager_token, seed_og
):
    resp = await client.post(PURCHASE_URL, json={"amount_usd": 10.0},
                             headers={"Authorization": f"Bearer {manager_token}"})
    assert resp.status_code == 402
    assert resp.json()["detail"]["code"] == "ai_credits_requires_paid_plan"


async def test_purchase_requires_saved_card(
    client: AsyncClient, manager_token, db_session, og_with_card
):
    og_with_card.default_payment_method_id = None
    await db_session.commit()
    resp = await client.post(PURCHASE_URL, json={"amount_usd": 10.0},
                             headers={"Authorization": f"Bearer {manager_token}"})
    assert resp.status_code == 402
    assert resp.json()["detail"]["code"] == "no_payment_method"


async def test_purchase_refused_while_billing_on_hold(
    client: AsyncClient, manager_token, db_session, og_with_card
):
    og_with_card.autoreload_failed_at = datetime.now(timezone.utc)
    await db_session.commit()
    resp = await client.post(PURCHASE_URL, json={"amount_usd": 10.0},
                             headers={"Authorization": f"Bearer {manager_token}"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "billing_on_hold"


async def test_purchase_declined_records_failed_row_and_no_hold(
    client: AsyncClient, manager_token, db_session, og_with_card, monkeypatch
):
    import stripe
    def boom(**kw):
        raise stripe.CardError("card declined", "card_declined", "card_declined")
    monkeypatch.setattr(stripe.PaymentIntent, "create", boom)

    resp = await client.post(PURCHASE_URL, json={"amount_usd": 10.0},
                             headers={"Authorization": f"Bearer {manager_token}"})
    assert resp.status_code == 402
    assert resp.json()["detail"]["code"] == "card_declined"

    await db_session.refresh(og_with_card)
    assert og_with_card.ai_credits_usd == 0.0
    assert og_with_card.autoreload_failed_at is None
    charges = list((await db_session.execute(select(BillingCharge))).scalars())
    assert [(c.kind, c.status) for c in charges] == [("purchase", "failed")]
```

`AsyncClient` is already imported in this file (line ~550, above the auto-reload endpoint tests); place the new section below that import.

- [ ] **Step 2: Run to verify failure**

Run: `backend/.venv/bin/python -m pytest tests/test_billing.py -q -k purchase`
Expected: the endpoint tests FAIL with 404 (route missing); `test_charge_saved_card_*` from Task 2 still PASS.

- [ ] **Step 3: Implement the endpoint**

In `backend/routers/billing.py`, extend the service import:

```python
from backend.services.billing import (
    AutoReloadBlocked,
    AutoReloadError,
    auto_reload_if_needed,
    charge_saved_card,
    get_full_billing_summary,
    get_ownership_group_id,
    record_storage_snapshots,
)
from backend.services.plan import assert_paid_plan
```

Add after `AutoReloadUpdate`:

```python
class CreditPurchaseRequest(BaseModel):
    amount_usd: float
    enable_autoreload: bool = False
```

Add after `retry_autoreload`:

```python
@router.post("/credits/purchase", response_model=AutoReloadStatus)
async def purchase_credits(
    body: CreditPurchaseRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> AutoReloadStatus:
    """Buy a fixed AI credit pack, charged now to the saved card (#64).

    The hard paywall for AI Generate: a paid group with a zero balance is
    sent here by the Schedule page. Auto-reload stays off unless the body
    asks for it, in which case the pack becomes the refill amount.
    """
    await assert_paid_plan(db, str(current_user.company_id), "ai_credits")

    if body.amount_usd not in settings.AI_CREDIT_PACKS_USD:
        packs = ", ".join(f"${p:.0f}" for p in settings.AI_CREDIT_PACKS_USD)
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_pack", "message": f"Choose one of the credit packs: {packs}."},
        )

    og_id = await get_ownership_group_id(db, str(current_user.company_id))
    if not og_id:
        raise HTTPException(status_code=404, detail="No ownership group found")
    og = (await db.execute(
        select(OwnershipGroup).where(OwnershipGroup.id == og_id).with_for_update()
    )).scalar_one()

    if og.autoreload_failed_at is not None:
        # A failed automatic charge is cleared by Retry payment, which also
        # proves the card works. A fresh purchase must not skip that.
        raise HTTPException(
            status_code=409,
            detail={"code": "billing_on_hold", "message": "Billing is on hold after a failed payment. Retry payment first."},
        )

    if not og.stripe_customer_id or not og.default_payment_method_id:
        raise HTTPException(
            status_code=402,
            detail={"code": "no_payment_method", "message": "No card on file. Add one in Manage billing, then try again."},
        )

    try:
        await charge_saved_card(db, og, float(body.amount_usd), kind="purchase")
    except AutoReloadError as e:
        await db.commit()  # keep the failed BillingCharge row
        raise HTTPException(
            status_code=402,
            detail={"code": "card_declined", "message": f"Your card was declined: {e}"},
        )

    if body.enable_autoreload:
        og.autoreload_enabled = True
        og.autoreload_amount_usd = body.amount_usd

    await db.commit()
    return _autoreload_status(og)
```

- [ ] **Step 4: Run the tests**

Run: `backend/.venv/bin/python -m pytest tests/test_billing.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/routers/billing.py tests/test_billing.py
git commit -m "feat(billing): buy a fixed AI credit pack with the saved card (#64)"
```

---

### Task 7: Operator alert on every successful credit charge

**Files:**
- Create: `backend/services/operator_alerts.py`
- Modify: `backend/services/billing.py` (`auto_reload_if_needed`), `backend/routers/billing.py` (`purchase_credits`)
- Test: `tests/test_operator_alerts.py` (new), `tests/test_billing.py`

**Interfaces:**
- Produces: `async def send_credit_purchase_alert(db: AsyncSession, og: OwnershipGroup, amount_usd: float, kind: str) -> bool`. Never raises. Reads `SUM(ownership_groups.ai_credits_usd)` through the caller's session.

- [ ] **Step 1: Write the failing service tests**

Create `tests/test_operator_alerts.py`:

```python
"""Operator alert on credit charges (#64).

The operator tops up the Anthropic Console by hand; this email tells them
how much. Customers pay LLM_OVERAGE_MARKUP × token cost, so a $10 pack
covers $10 / 1.30 = $7.69 of Anthropic usage.
"""
import logging

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models.ownership_group import OwnershipGroup
from backend.services.operator_alerts import send_credit_purchase_alert
from tests.conftest import _id


@pytest.fixture
def two_groups():
    return [
        OwnershipGroup(id=_id(), name="Alpha Cafés", ai_credits_usd=10.0),
        OwnershipGroup(id=_id(), name="Beta Bars", ai_credits_usd=5.0),
    ]


async def test_alert_logs_and_skips_email_when_unconfigured(
    db_session: AsyncSession, two_groups, monkeypatch, caplog
):
    monkeypatch.setattr(settings, "OPERATOR_ALERT_EMAIL", "")
    db_session.add_all(two_groups)
    await db_session.commit()

    with caplog.at_level(logging.INFO, logger="backend.services.operator_alerts"):
        sent = await send_credit_purchase_alert(db_session, two_groups[0], 10.0, "purchase")

    assert sent is False
    line = next(r.message for r in caplog.records if "[OPERATOR] credit_charge" in r.message)
    assert "kind=purchase" in line
    assert "paid=10.00" in line
    assert "anthropic_equiv=7.69" in line
    assert "total_prepaid=15.00" in line
    assert "total_anthropic_equiv=11.54" in line


async def test_alert_emails_operator_with_amounts(
    db_session: AsyncSession, two_groups, monkeypatch
):
    import resend
    monkeypatch.setattr(settings, "OPERATOR_ALERT_EMAIL", "ops@example.com")
    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test")
    captured = {}
    monkeypatch.setattr(resend.Emails, "send", lambda params: captured.update(params) or {"id": "em_1"})
    db_session.add_all(two_groups)
    await db_session.commit()

    sent = await send_credit_purchase_alert(db_session, two_groups[0], 10.0, "autoreload")

    assert sent is True
    assert captured["to"] == ["ops@example.com"]
    assert captured["from"] == settings.FROM_EMAIL
    assert captured["subject"] == "[WizScheduler] AI credit auto-reload $10.00 — top up Anthropic by $7.69"
    html = captured["html"]
    assert "Alpha Caf" in html
    assert two_groups[0].id in html
    assert "$7.69" in html
    assert "$15.00" in html and "$11.54" in html


async def test_alert_swallows_resend_failure(
    db_session: AsyncSession, two_groups, monkeypatch
):
    import resend
    monkeypatch.setattr(settings, "OPERATOR_ALERT_EMAIL", "ops@example.com")
    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test")
    def boom(params):
        raise RuntimeError("resend down")
    monkeypatch.setattr(resend.Emails, "send", boom)
    db_session.add_all(two_groups)
    await db_session.commit()

    sent = await send_credit_purchase_alert(db_session, two_groups[1], 50.0, "purchase")
    assert sent is False
```

- [ ] **Step 2: Run to verify failure**

Run: `backend/.venv/bin/python -m pytest tests/test_operator_alerts.py -q`
Expected: FAIL at import, `No module named 'backend.services.operator_alerts'`.

- [ ] **Step 3: Implement the service**

Create `backend/services/operator_alerts.py`:

```python
"""Emails to the site operator, not to customers.

Nothing here counts against a tenant's email cap (services/email_quota):
the operator is the recipient, and the mail exists so they can act on
their own Anthropic Console, which has no API for buying credits.
"""
from __future__ import annotations

import html
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models.ownership_group import OwnershipGroup

logger = logging.getLogger(__name__)


async def _total_prepaid_usd(db: AsyncSession) -> float:
    total = (await db.execute(
        select(func.coalesce(func.sum(OwnershipGroup.ai_credits_usd), 0.0))
    )).scalar_one()
    return float(total)


async def send_credit_purchase_alert(
    db: AsyncSession,
    og: OwnershipGroup,
    amount_usd: float,
    kind: str,
) -> bool:
    """Tell the operator a customer paid for credits and what to buy upstream.

    Customers pay LLM_OVERAGE_MARKUP × token cost, so amount / markup is the
    Anthropic-side spend this charge funds. The total across all groups is
    the outstanding liability to compare against the Console balance.

    Always writes one [OPERATOR] log line. Sends email only when both
    OPERATOR_ALERT_EMAIL and RESEND_API_KEY are set. Never raises: the
    charge has already happened and must not be undone by a mail failure.

    Returns True only when Resend accepted the send.
    """
    markup = settings.LLM_OVERAGE_MARKUP
    anthropic_equiv = round(amount_usd / markup, 2)
    try:
        total_prepaid = await _total_prepaid_usd(db)
    except Exception:
        logger.exception("[OPERATOR] could not sum prepaid balances")
        total_prepaid = 0.0
    total_equiv = round(total_prepaid / markup, 2)

    logger.info(
        "[OPERATOR] credit_charge og=%s kind=%s paid=%.2f anthropic_equiv=%.2f "
        "total_prepaid=%.2f total_anthropic_equiv=%.2f",
        og.id, kind, amount_usd, anthropic_equiv, total_prepaid, total_equiv,
    )

    if not settings.OPERATOR_ALERT_EMAIL or not settings.RESEND_API_KEY:
        return False

    label = "purchase" if kind == "purchase" else "auto-reload"
    subject = (
        f"[WizScheduler] AI credit {label} ${amount_usd:.2f} — "
        f"top up Anthropic by ${anthropic_equiv:.2f}"
    )
    body = (
        '<div style="font-family:sans-serif;max-width:600px;margin:0 auto;">'
        f"<p><strong>{html.escape(og.name)}</strong> (og={html.escape(og.id)}) "
        f"paid <strong>${amount_usd:.2f}</strong> for AI credits ({label}).</p>"
        f"<p>At {markup:.0%} markup that funds <strong>${anthropic_equiv:.2f}</strong> "
        f"of Anthropic usage. Top up the Console by at least that.</p>"
        f"<p>Outstanding prepaid across all accounts: ${total_prepaid:.2f}, "
        f"which is ${total_equiv:.2f} at Anthropic. Keep the Console balance "
        f"above that figure.</p>"
        "</div>"
    )

    try:
        import resend

        resend.api_key = settings.RESEND_API_KEY
        resend.Emails.send({
            "from": settings.FROM_EMAIL,
            "to": [settings.OPERATOR_ALERT_EMAIL],
            "subject": subject,
            "html": body,
        })
        return True
    except Exception:
        logger.exception("[OPERATOR] credit_charge email failed og=%s kind=%s", og.id, kind)
        return False
```

- [ ] **Step 4: Run the service tests**

Run: `backend/.venv/bin/python -m pytest tests/test_operator_alerts.py -q`
Expected: 3 PASS.

- [ ] **Step 5: Write the failing wiring tests**

Append to `tests/test_billing.py`:

```python
async def test_purchase_sends_operator_alert(
    client: AsyncClient, manager_token, db_session, og_with_card, monkeypatch
):
    import stripe
    monkeypatch.setattr(stripe.PaymentIntent, "create", lambda **kw: MagicMock(status="succeeded", id="pi_pack_ok"))
    calls = []
    async def fake_alert(db, og, amount_usd, kind):
        calls.append((og.id, amount_usd, kind))
        return True
    monkeypatch.setattr("backend.routers.billing.send_credit_purchase_alert", fake_alert)

    resp = await client.post(PURCHASE_URL, json={"amount_usd": 50.0},
                             headers={"Authorization": f"Bearer {manager_token}"})
    assert resp.status_code == 200, resp.text
    assert calls == [(OG_ID, 50.0, "purchase")]


async def test_auto_reload_sends_operator_alert(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    import stripe
    from backend.services.billing import auto_reload_if_needed
    monkeypatch.setattr(stripe.PaymentIntent, "create", lambda **kw: MagicMock(status="succeeded", id="pi_reload_ok"))
    calls = []
    async def fake_alert(db, og, amount_usd, kind):
        calls.append((og.id, amount_usd, kind))
        return True
    monkeypatch.setattr("backend.services.billing.send_credit_purchase_alert", fake_alert)

    og_with_card.ai_credits_usd = 0.0
    await db_session.commit()
    await auto_reload_if_needed(db_session, og_with_card, cost_usd=1.0)
    assert calls == [(OG_ID, 10.0, "autoreload")]
```

- [ ] **Step 6: Run to verify failure**

Run: `backend/.venv/bin/python -m pytest tests/test_billing.py -q -k operator_alert`
Expected: both FAIL, `AttributeError: ... has no attribute 'send_credit_purchase_alert'`.

- [ ] **Step 7: Wire the callers**

`backend/services/billing.py`: add the top-level import `from backend.services.operator_alerts import send_credit_purchase_alert`. In `auto_reload_if_needed`, after the `try/except` that calls `charge_saved_card`, add:

```python
    await send_credit_purchase_alert(db, og, float(og.autoreload_amount_usd), "autoreload")
```

`backend/routers/billing.py`: add `from backend.services.operator_alerts import send_credit_purchase_alert` to the imports. In `purchase_credits`, between `await db.commit()` and `return _autoreload_status(og)`, add:

```python
    # After the commit: the charge is durable whatever the mail does.
    await send_credit_purchase_alert(db, og, float(body.amount_usd), "purchase")
```

- [ ] **Step 8: Run everything**

Run: `backend/.venv/bin/python -m pytest tests -q`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/services/operator_alerts.py backend/services/billing.py backend/routers/billing.py tests/test_operator_alerts.py tests/test_billing.py
git commit -m "feat(billing): email the operator the Anthropic top-up amount on every credit charge (#64)"
```

---

### Task 8: Schedule page — balance strip and purchase modal (+ schedule i18n keys in all locales)

**Files:**
- Modify: `frontend/src/api/billing.ts`
- Modify: `frontend/src/pages/manager/Schedule.tsx` (imports line 7-8; state ~321-330; handlers after `handleOpenPortal` ~465; credit strip ~903-932; modal ~1478-1596)
- Modify: `frontend/src/i18n/en.ts` (`schedule:` block starts line 636) and the same block in `ar bn de es fr hi id ja mr pcm pt ru ta te tr ur vi zh`
- Test: `cd frontend && npm run build && npm test`

**Interfaces:**
- Consumes: `GET /schedules/ai-credits` fields from Task 4; `POST /billing/credits/purchase` from Task 6.
- Produces: `purchaseCredits(amountUsd: number, enableAutoreload: boolean): Promise<AutoReloadStatus>`; i18n keys listed in Step 1.

- [ ] **Step 1: i18n keys — English first**

In `frontend/src/i18n/en.ts`, inside `schedule: {`:

Delete these keys: `freeRemaining`, `purchasedRemaining`, `freeTierUsed`, `buyAiCredits`, `monthlyUsage`, `currentBalance`, `creditAmount`, `purchase`.

Reword:

```ts
    creditsExhaustedMsg: "AI Generate runs on prepaid credits. Your balance is empty — buy a pack to continue.",
    scheduleQuotaExhaustedMsg: "You have used the 50 schedules included this month. Extra schedules are paid from the same credit balance — buy a pack to continue.",
    autoReloadDescription: "Optional. When your credit balance falls below the threshold, we charge your saved card the refill amount. Off by default; every charge is one you asked for.",
```

Add (after `buyCreditsPaidOnly`):

```ts
    balanceLabel: "balance",
    includedThisMonth: "included this month",
    purchaseModalTitle: "AI credits",
    purchaseModalBody: "Credits pay for AI Generate and for schedules beyond the 50 included each month. Packs are charged to the card on your subscription.",
    packButton: "Buy {amount}",
    autoReloadOptIn: "Auto-reload this amount when my balance runs out",
    purchaseDeclined: "Your card was declined. Update it in Manage billing and try again.",
    purchaseNoCard: "No card on file. Add one in Manage billing, then try again.",
    purchaseOnHold: "Billing is on hold after a failed payment. Retry payment first.",
    purchaseSuccess: "Credits added.",
```

- [ ] **Step 2: i18n keys — the other 18 locales**

Apply the same deletions, rewordings and additions to the `schedule:` block of every other locale file (`ar bn de es fr hi id ja mr pcm pt ru ta te tr ur vi zh`). Write each value in that locale's language, matching the style already used in that file's `schedule` block (several locales keep product terms such as "AI Generate" and "Manage billing" in English; follow what the surrounding keys do). Keep the `{amount}` placeholder verbatim in `packButton`. Do not add or drop any key that is not in the lists above.

Verify parity: `cd frontend && npm run build`. Expected: succeeds (any missing or extra key fails type-checking against `typeof en`).

- [ ] **Step 3: API types and call**

In `frontend/src/api/billing.ts`:

```ts
export interface AiCreditStatus {
  can_generate: boolean;
  included_remaining_usd: number;
  purchased_credits_usd: number;
  is_over_included: boolean;
  monthly_cost_usd: number;
  autoreload_failed?: boolean;
  /** True when the only thing missing is a credit pack (not on hold). */
  purchase_required: boolean;
  /** Pack sizes the server accepts on purchaseCredits, in USD. */
  packs_usd: number[];
}
```

Change `BillingChargeRow.kind` to `"autoreload" | "purchase" | "invoice_item_storage" | "invoice_item_employees"`.

Add after `retryAutoReload`:

```ts
export function purchaseCredits(
  amountUsd: number,
  enableAutoreload: boolean
): Promise<AutoReloadStatus> {
  return apiFetch<AutoReloadStatus>("/billing/credits/purchase", {
    method: "POST",
    body: JSON.stringify({ amount_usd: amountUsd, enable_autoreload: enableAutoreload }),
  });
}
```

- [ ] **Step 4: Schedule page state and handler**

In `frontend/src/pages/manager/Schedule.tsx`, add to the imports:

```ts
import { ApiError, errorMessage } from "../../api/client";
```

Change the `autoReloadDraft` initial state to `{ enabled: false, threshold_usd: 2, amount_usd: 10 }`.

After `const [autoReloadSaving, setAutoReloadSaving] = useState(false);` add:

```ts
  const [purchasingPack, setPurchasingPack] = useState<number | null>(null);
  const [purchaseError, setPurchaseError] = useState("");
  const [optInAutoReload, setOptInAutoReload] = useState(false);
```

After `handleOpenPortal` add:

```ts
  const handlePurchasePack = async (amount: number) => {
    setPurchasingPack(amount);
    setPurchaseError("");
    try {
      const updated = await billingApi.purchaseCredits(amount, optInAutoReload);
      setAutoReload(updated);
      setAutoReloadDraft({
        enabled: updated.enabled,
        threshold_usd: updated.threshold_usd,
        amount_usd: updated.amount_usd,
      });
      await fetchCredits();
      setShowBillingModal(false);
    } catch (err: unknown) {
      const code =
        err instanceof ApiError && err.data && typeof err.data === "object"
          ? (err.data as { code?: string }).code
          : undefined;
      if (code === "no_payment_method") setPurchaseError(t.schedule.purchaseNoCard);
      else if (code === "billing_on_hold") setPurchaseError(t.schedule.purchaseOnHold);
      else if (code === "card_declined") setPurchaseError(t.schedule.purchaseDeclined);
      else setPurchaseError(errorMessage(err, t.schedule.purchaseDeclined));
    } finally {
      setPurchasingPack(null);
    }
  };
```

- [ ] **Step 5: Credit strip**

Replace the `{/* AI credits */}` block (from `{creditStatus && (` through its closing `)}`) with:

```tsx
          {/* AI credits: purchased balance only (#64). The included line
              appears only when a demo environment sets INCLUDED_LLM_USD. */}
          {creditStatus && (
            <div className={`p-3 rounded-lg border text-sm flex items-center justify-between gap-4 ${
              creditStatus.can_generate
                ? `${border.default} ${bg.sectionSubtle} ${text.muted}`
                : "border-red-200 bg-red-50 text-red-700"
            }`}>
              <div className="flex items-center gap-4 flex-wrap">
                <span>
                  {t.schedule.aiCredits}: ${creditStatus.purchased_credits_usd.toFixed(2)} {t.schedule.balanceLabel}
                </span>
                {creditStatus.included_remaining_usd > 0 && (
                  <span className={`text-xs ${text.muted}`}>
                    + ${creditStatus.included_remaining_usd.toFixed(2)} {t.schedule.includedThisMonth}
                  </span>
                )}
              </div>
              {creditStatus.purchase_required && (
                <button
                  onClick={() => { setPurchaseReason("ai"); setPurchaseError(""); setShowBillingModal(true); }}
                  className="glass-btn-primary text-xs px-3 py-1 whitespace-nowrap"
                >
                  {t.schedule.buyCredits}
                </button>
              )}
            </div>
          )}
```

Also change the schedule-quota Buy Credits button's `onClick` (the one near `buy-credits-reason`) to `() => { setPurchaseReason("schedules"); setPurchaseError(""); setShowBillingModal(true); }`.

- [ ] **Step 6: Purchase modal**

Replace the modal block (`{showBillingModal && autoReload && (` … `)}`) with:

```tsx
      {showBillingModal && autoReload && (
        <div className="glass-modal-overlay">
          <div className="glass-modal w-full max-w-md mx-4">
            <div className={`flex items-center justify-between px-6 py-4 border-b ${border.default}`}>
              <h3 className={`text-lg font-semibold ${text.heading}`}>{t.schedule.purchaseModalTitle}</h3>
              <button
                onClick={() => setShowBillingModal(false)}
                className="text-gray-500 hover:text-gray-600 text-xl leading-none"
              >
                &times;
              </button>
            </div>
            <div className="px-6 py-4 space-y-4">
              <p className={`text-sm ${text.muted}`}>
                {purchaseReason === "schedules"
                  ? t.schedule.scheduleQuotaExhaustedMsg
                  : t.schedule.creditsExhaustedMsg}
              </p>
              <p className={`text-sm ${text.muted}`}>{t.schedule.purchaseModalBody}</p>

              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={optInAutoReload}
                  onChange={(e) => setOptInAutoReload(e.target.checked)}
                />
                {t.schedule.autoReloadOptIn}
              </label>
              <div className="grid grid-cols-3 gap-3">
                {(creditStatus?.packs_usd ?? [10, 25, 50]).map((amount) => (
                  <button
                    key={amount}
                    type="button"
                    onClick={() => handlePurchasePack(amount)}
                    disabled={purchasingPack !== null}
                    className="glass-btn-primary text-sm font-medium py-2 disabled:cursor-not-allowed"
                  >
                    {purchasingPack === amount
                      ? t.common.saving
                      : t.schedule.packButton.replace("{amount}", `$${amount.toFixed(0)}`)}
                  </button>
                ))}
              </div>
              {purchaseError && (
                <p className="text-sm text-red-700" role="alert">{purchaseError}</p>
              )}

              <div className={`pt-4 border-t ${border.default} space-y-3`}>
                <p className={`text-xs ${text.muted}`}>{t.schedule.autoReloadDescription}</p>
                <div className="grid grid-cols-3 gap-3 text-center">
                  <div>
                    <div className={`text-xs ${text.muted}`}>{t.schedule.balance}</div>
                    <div className={`text-lg font-semibold ${text.heading}`}>${autoReload.current_balance_usd.toFixed(2)}</div>
                  </div>
                  <div>
                    <div className={`text-xs ${text.muted}`}>{t.schedule.threshold}</div>
                    <div className={`text-lg font-semibold ${text.heading}`}>${autoReload.threshold_usd.toFixed(2)}</div>
                  </div>
                  <div>
                    <div className={`text-xs ${text.muted}`}>{t.schedule.refillAmount}</div>
                    <div className={`text-lg font-semibold ${text.heading}`}>${autoReload.amount_usd.toFixed(2)}</div>
                  </div>
                </div>
                {autoReloadEditing && (
                  <div className="space-y-3 pt-2 border-t border-sage/10">
                    <label className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={autoReloadDraft.enabled}
                        onChange={(e) => setAutoReloadDraft({ ...autoReloadDraft, enabled: e.target.checked })}
                      />
                      {t.schedule.autoReloadEnabled}
                    </label>
                    <label className="block text-sm">
                      {t.schedule.threshold}: $
                      <input
                        type="number"
                        min="0.5"
                        step="0.5"
                        value={autoReloadDraft.threshold_usd}
                        onChange={(e) => setAutoReloadDraft({ ...autoReloadDraft, threshold_usd: parseFloat(e.target.value) || 0 })}
                        className="ms-2 border rounded px-2 py-1 w-24"
                      />
                    </label>
                    <label className="block text-sm">
                      {t.schedule.refillAmount}: $
                      <input
                        type="number"
                        min="0.5"
                        step="1"
                        value={autoReloadDraft.amount_usd}
                        onChange={(e) => setAutoReloadDraft({ ...autoReloadDraft, amount_usd: parseFloat(e.target.value) || 0 })}
                        className="ms-2 border rounded px-2 py-1 w-24"
                      />
                    </label>
                  </div>
                )}
              </div>
            </div>
            <div className={`flex justify-end gap-3 px-6 py-4 border-t ${border.default} ${bg.sectionSubtle} rounded-b-2xl`}>
              {!autoReloadEditing ? (
                <>
                  <button
                    type="button"
                    onClick={handleOpenPortal}
                    className="glass-btn-secondary text-sm font-medium me-auto"
                  >
                    {t.schedule.manageBilling}
                  </button>
                  <button
                    onClick={() => setShowBillingModal(false)}
                    className="glass-btn-secondary text-sm font-medium"
                  >
                    {t.common.close}
                  </button>
                  <button
                    onClick={() => setAutoReloadEditing(true)}
                    className="glass-btn-primary text-sm font-medium"
                  >
                    {t.common.edit}
                  </button>
                </>
              ) : (
                <>
                  <button
                    onClick={() => {
                      setAutoReloadEditing(false);
                      setAutoReloadDraft({
                        enabled: autoReload.enabled,
                        threshold_usd: autoReload.threshold_usd,
                        amount_usd: autoReload.amount_usd,
                      });
                    }}
                    className="glass-btn-secondary text-sm font-medium"
                  >
                    {t.common.cancel}
                  </button>
                  <button
                    onClick={handleSaveAutoReload}
                    disabled={autoReloadSaving}
                    className="glass-btn-primary text-sm font-medium"
                  >
                    {autoReloadSaving ? t.common.saving : t.common.save}
                  </button>
                </>
              )}
            </div>
          </div>
        </div>
      )}
```

`t.common.saving`, `t.common.close`, `t.common.edit`, `t.common.cancel`, `t.common.save`, `t.schedule.manageBilling`, `t.schedule.balance`, `t.schedule.threshold`, `t.schedule.refillAmount`, `t.schedule.autoReloadEnabled` already exist.

- [ ] **Step 7: Build and test**

Run: `cd frontend && npm run build && npm test`
Expected: build succeeds with no type errors; vitest passes including `logicalDirection.test.ts`. Grep for leftovers: `grep -rn "freeRemaining\|freeTierUsed\|purchasedRemaining\|autoReloadTitle" src` must return only the `autoReloadTitle` definitions in the i18n files (that key stays; it is still typed but now unused, which is fine) and nothing for the three removed keys.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/api/billing.ts frontend/src/pages/manager/Schedule.tsx frontend/src/i18n
git commit -m "feat(schedule): purchased balance strip and credit-pack purchase modal (#64)"
```

---

### Task 9: Landing pricing (+ landing i18n keys in all locales)

**Files:**
- Modify: `frontend/src/pages/Landing.tsx:204-237`
- Modify: `landing:` block (starts `en.ts:855`) in all 19 locale files
- Test: `cd frontend && npm run build && npm test`

- [ ] **Step 1: English keys**

In `frontend/src/i18n/en.ts` `landing:` block, reword:

```ts
    basePlanDesc: "Base subscription per ownership group. Includes 50 schedules a month, 0.5 GB of storage and 1K employees.",
    aiCredits: "AI credit packs",
    normalStrategiesNote: "Rotation, Max Hours and Random scheduling are always included. AI Generate runs on prepaid credit packs.",
    aiOverageNote: "Claude Sonnet 5 token cost ($2/M in, $10/M out) at 130%, debited from prepaid packs of $10, $25 or $50.",
    exampleAICost: "$1.95 from credits",
```

Add after `aiCredits`:

```ts
    aiPackFrom: "From $10",
```

- [ ] **Step 2: The other 18 locales**

Apply the same five rewordings and the one addition to the `landing:` block of every other locale file, in that locale's language, matching the surrounding style. Keep dollar figures and "Claude Sonnet 5" as they are.

- [ ] **Step 3: Landing markup**

In `frontend/src/pages/Landing.tsx`, replace the pricing tile block (from `<div className={`border ${m.rule.line} p-6 md:p-8 mb-10`}>` through its closing `</div>` after `normalStrategiesNote`) with:

```tsx
        <div className={`border ${m.rule.line} p-6 md:p-8 mb-10`}>
          <p className={`${m.text.meta} mb-3`}>{t.landing.allInOnePlan}</p>
          <div className={`${m.text.display} font-display text-5xl font-semibold mb-2`}>
            $18<span className={`${m.text.muted} text-xl font-normal`}> {t.landing.pricePerMonth}</span>
          </div>
          <p className={`${m.text.clear} mb-3 max-w-[60ch]`}>{t.landing.normalStrategiesNote}</p>
          <p className={`${m.text.muted} mb-8 max-w-[60ch]`}>{t.landing.basePlanDesc}</p>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-6 max-w-4xl">
            <div>
              <div className={`${m.text.data} text-2xl font-semibold`}>{t.landing.aiPackFrom}</div>
              <div className={`${m.text.meta} mt-1`}>{t.landing.aiCredits}</div>
            </div>
            <div>
              <div className={`${m.text.data} text-2xl font-semibold`}>50</div>
              <div className={`${m.text.meta} mt-1`}>{t.landing.compSchedules}</div>
            </div>
            <div>
              <div className={`${m.text.data} text-2xl font-semibold`}>0.5 GB</div>
              <div className={`${m.text.meta} mt-1`}>{t.landing.storageIncluded}</div>
            </div>
            <div>
              <div className={`${m.text.data} text-2xl font-semibold`}>1K</div>
              <div className={`${m.text.meta} mt-1`}>{t.landing.employeesIncluded}</div>
            </div>
          </div>
        </div>
```

`m` is `marketing` from `frontend/src/theme.ts`; `m.text.clear` exists and is what the example-row costs use.

In the example bill, change the hard-coded total `$20.30` to `$22.25` (base 18.00 + schedules 0.30 + AI 1.95 + storage 0.00 + employees 2.00, per the English `example*Cost` strings).

- [ ] **Step 4: Build and test**

Run: `cd frontend && npm run build && npm test`
Expected: both succeed.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Landing.tsx frontend/src/i18n
git commit -m "feat(landing): price AI as prepaid credit packs; local strategies lead the tile (#64)"
```

---

### Task 10: Docs and final verification

**Files:**
- Modify: `CLAUDE.md` (Conventions list, after the "Free-plan limits" bullet)
- Modify: `docs/superpowers/specs/2026-09-15-paid-ai-credits-design.md` (three factual corrections)

- [ ] **Step 1: CLAUDE.md**

Add after the `Free-plan limits live in backend/services/plan.py` bullet:

```markdown
- **AI spend always debits purchased credits.** `INCLUDED_LLM_USD` is `0.00`
  in production and exists only so a demo environment can grant spend. Every
  successful credit charge (pack purchase or auto-reload) goes through
  `services/billing.charge_saved_card`, which is what fires the operator
  alert with the Anthropic-side top-up amount; do not add a second charge
  path. Post-generation billing never raises on a payment outcome — the
  pre-generation gate is the consent point.
```

- [ ] **Step 2: Spec corrections**

In the spec: replace "all against the Postgres test database like the rest of the suite" with "on the SQLite test database like the rest of the suite"; replace "`tests/test_credit_purchase.py` (new)" with "`tests/test_billing.py` (which owns the `seed_og`/`og_with_card` fixtures)"; in section 8 change the signature line to `send_credit_purchase_alert(db, og, amount_usd, kind) -> bool` and the sentence "The alert reads `SUM(ai_credits_usd)` in its own short session so it does not hold the caller's row lock." to "The alert reads `SUM(ai_credits_usd)` through the caller's session; a SELECT takes no row locks.". In section 1 replace "`deduct_credits_for_schedule_overage` is unchanged. Schedule overage keeps its existing gate in `check_schedule_quota`." with "`deduct_credits_for_schedule_overage` gets the same treatment through a shared `_reload_after_debit` helper, because auto-reload defaulting to off makes the raise reachable there too; its gate in `check_schedule_quota` is unchanged."

- [ ] **Step 3: Full verification**

Run, from the repo root:

```bash
backend/.venv/bin/python -m pytest tests -q
cd frontend && npm run build && npm test && cd ..
cd backend && .venv/bin/python -m alembic heads && cd ..
```

Expected: all tests PASS; build clean; a single alembic head `0035`.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md docs/superpowers/specs/2026-09-15-paid-ai-credits-design.md
git commit -m "docs: paid AI credits convention and spec corrections (#64)"
```
