# AI Credits Purchased Separately — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the free AI credits bundled into the $18/month subscription so AI spend always debits a purchased balance, and give a manager with no balance a priced offer instead of an error.

**Architecture:** `INCLUDED_LLM_USD` drops to `0.00` but stays as a per-environment knob; a test pins its now-unreachable branch so it cannot silently misprice. Credits are bought through a Stripe Checkout redirect in fixed packs, credited by a webhook *and* a confirm endpoint, with double-crediting prevented by a partial unique index rather than application logic. The Schedule page replaces "free remaining" with the purchased balance and a buy CTA.

**Tech Stack:** FastAPI, SQLAlchemy 2.x async, Alembic, Pydantic v2, Stripe Python SDK, pytest/pytest-asyncio; React 18 + TypeScript + Vite + Tailwind.

**Spec:** `docs/superpowers/specs/2026-08-23-ai-credits-purchased-design.md`

## Global Constraints

- **Run the test suite with `backend/.venv/bin/python -m pytest`.** The system Anaconda python lacks this project's dependencies; bare `pytest` will fail on imports and look like broken code.
- **`check_schedule_quota` is OUT OF SCOPE and must not be touched.** It has its OWN `is_over_included`, for the metered 50-schedules-per-month quota (`INCLUDED_SCHEDULES_PER_MONTH`). It is a different tier for a different product line. Removing fields by name across `billing.py` will break schedule billing. Only the AI-credits path changes: `check_ai_credits`, `check_and_record_usage`, and the `llm` block of `get_full_billing_summary`.
- **`ScheduleQuota.is_over_included` in `frontend/src/api/billing.ts` stays.** Only `AiCreditStatus` and `BillingUsage.llm` lose fields.
- **Type hints on all Python; functional components and hooks in React.**
- **Multi-tenancy:** every query filters by `company_id` / ownership group.
- **No new dependencies.** `stripe` is already a dependency.
- **i18n:** `frontend/src/i18n/LanguageContext.tsx` types translations as `Record<Language, Translations>` derived from `en.ts`. A key added to `en.ts` alone **fails the TypeScript build**. All 19 locale files (`ar bn de en es fr hi id ja mr pcm pt ru ta te tr ur vi zh`) must carry every new key.
- **Landing page is out of scope** — it ships in a separate PR with the free-tier and check-in marketing.
- Lint/tests before each commit: `backend/.venv/bin/python -m pytest tests/ -q` and `cd frontend && npx tsc --noEmit`.

---

### Task 1: Zero the free tier and pin the unreachable branch

The billing math change, with the test that makes keeping the knob safe.

**Files:**
- Modify: `backend/config.py` (`INCLUDED_LLM_USD`, ~line 94)
- Test: `tests/test_llm_free_tier_zero.py`

**Interfaces:**
- Consumes: nothing
- Produces: `settings.INCLUDED_LLM_USD == 0.00`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm_free_tier_zero.py
"""The free AI grant is gone: INCLUDED_LLM_USD is 0.00.

`check_and_record_usage` has TWO cost-split paths and they behave differently
at zero, which is why both are tested here:

  * the `if usage:` path (a TokenUsage row already exists this month) splits
    three ways, and at a zero tier only the FIRST branch is reachable —
    `cost_before >= 0` is always true because costs are never negative.
  * the `else` path (first usage of the month) splits two ways, and at a zero
    tier `overage = this_cost - 0 == this_cost`, so it is still correct, just
    degenerate.

The middle branch of the first path is the dangerous one. If it ever ran at a
zero tier it would compute `overage = cost_after - 0` and charge markup on a
figure it believes is partial. It cannot run — and this file is what keeps
that true.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import Company, TokenUsage
from backend.models.ownership_group import OwnershipGroup
from backend.services.billing import calculate_cost, check_and_record_usage
from tests.conftest import _id

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def tenant(db_session: AsyncSession) -> str:
    og_id, company_id = _id(), _id()
    db_session.add(OwnershipGroup(id=og_id, name="G"))
    await db_session.flush()
    db_session.add(Company(id=company_id, name="C", slug=_id(),
                           ownership_group_id=og_id))
    await db_session.commit()
    return company_id


def test_the_free_grant_is_zero():
    """The whole point of issue #64."""
    assert settings.INCLUDED_LLM_USD == 0.00


async def test_the_first_generation_of_the_month_is_charged_in_full(
    db_session: AsyncSession, tenant: str
):
    """Exercises the `else` (no TokenUsage row yet) path."""
    result = await check_and_record_usage(db_session, tenant, 10_000, 2_000)

    expected_cost = calculate_cost(10_000, 2_000)
    assert result["cost_usd"] == pytest.approx(expected_cost)
    assert result["charged_usd"] == pytest.approx(
        round(expected_cost * settings.LLM_OVERAGE_MARKUP, 6)
    )
    assert result["charged_usd"] > 0


async def test_a_later_generation_is_also_charged_in_full(
    db_session: AsyncSession, tenant: str
):
    """Exercises the `if usage:` path — the one with the dead branch."""
    await check_and_record_usage(db_session, tenant, 10_000, 2_000)
    second = await check_and_record_usage(db_session, tenant, 5_000, 1_000)

    expected_cost = calculate_cost(5_000, 1_000)
    assert second["charged_usd"] == pytest.approx(
        round(expected_cost * settings.LLM_OVERAGE_MARKUP, 6)
    )


async def test_nothing_is_ever_free(db_session: AsyncSession, tenant: str):
    """Ten generations, every one charged. No cushion at any point."""
    for _ in range(10):
        result = await check_and_record_usage(db_session, tenant, 1_000, 200)
        assert result["charged_usd"] > 0


async def test_the_partially_free_branch_is_unreachable_at_a_zero_tier(
    db_session: AsyncSession, tenant: str
):
    """The branch that would misprice if it ever ran.

    Asserted structurally rather than by observation: for the middle branch to
    execute, `cost_before < tier` must hold. At tier 0 that requires a
    negative accumulated cost, which cannot occur. Any generation therefore
    charges the FULL markup on its own cost, never a partial overage.
    """
    assert settings.INCLUDED_LLM_USD == 0.00

    await check_and_record_usage(db_session, tenant, 8_000, 1_500)
    usage = (await db_session.execute(
        __import__("sqlalchemy").select(TokenUsage)
    )).scalar_one()
    assert usage.cost_usd >= settings.INCLUDED_LLM_USD

    this_cost = calculate_cost(2_000, 400)
    result = await check_and_record_usage(db_session, tenant, 2_000, 400)

    # Full markup on this generation's own cost. A partial-overage
    # calculation would produce a DIFFERENT (smaller) number.
    assert result["charged_usd"] == pytest.approx(
        round(this_cost * settings.LLM_OVERAGE_MARKUP, 6)
    )


async def test_the_branch_still_works_if_a_grant_is_reinstated(
    db_session: AsyncSession, tenant: str, monkeypatch
):
    """The knob is kept so a grant can come back per environment. If it does,
    the partially-free branch becomes live again and must be correct."""
    monkeypatch.setattr(settings, "INCLUDED_LLM_USD", 5.00)

    # Tiny first generation, well inside the grant: charged nothing.
    first = await check_and_record_usage(db_session, tenant, 100, 20)
    assert first["charged_usd"] == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/bin/python -m pytest tests/test_llm_free_tier_zero.py -v`
Expected: `test_the_free_grant_is_zero` FAILS (`2.0 != 0.0`), and the charge tests fail because small generations fall inside the $2 grant.

- [ ] **Step 3: Zero the tier**

In `backend/config.py`, replace the `INCLUDED_LLM_USD` line:

```python
    # Zeroed for issue #64: the base subscription no longer includes AI
    # spend. Kept as a knob rather than deleted so a grant can be reinstated
    # per environment — a promotional month, or the demo tenant — without a
    # code change.
    #
    # At 0.00 the middle branch of check_and_record_usage's three-way split
    # ("partially free") is unreachable: it needs cost_before < tier, and
    # costs are never negative. That matters because if it DID run at zero it
    # would compute overage = cost_after - 0 and charge markup on a figure it
    # believes is partial. tests/test_llm_free_tier_zero.py pins this.
    INCLUDED_LLM_USD: float = 0.00          # no free credits; see #64
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/bin/python -m pytest tests/test_llm_free_tier_zero.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Run the full suite and read the failures carefully**

Run: `backend/.venv/bin/python -m pytest tests/ -q`

Existing tests almost certainly assert the old $2 behaviour. **Fix each by keying it on `settings.INCLUDED_LLM_USD` or by `monkeypatch`ing a non-zero tier where the test is genuinely about tier arithmetic — do NOT weaken an assertion to make it pass.** If a test's premise is now void (it asserts a generation is free), rewrite it to assert the new truth and say so in the commit message, the way `test_old_demo_starts_over_the_free_limits` was rewritten when the plan caps changed.

- [ ] **Step 6: Commit**

```bash
git add backend/config.py tests/test_llm_free_tier_zero.py
git commit -m "feat(billing): zero the free AI grant, pin the unreachable branch

INCLUDED_LLM_USD 2.00 -> 0.00. Kept as a per-environment knob rather than
deleted; a test asserts the partially-free branch cannot run at zero, where
it would charge markup on a figure it believes is partial."
```

---

### Task 2: Remove the free-tier fields from the AI-credits API

**Files:**
- Modify: `backend/services/billing.py` — `check_ai_credits` (~line 666), `check_and_record_usage` (~lines 153-255), `get_full_billing_summary` llm block (~line 777)
- Modify: `backend/routers/billing.py` (~lines 75-76)
- Test: `tests/test_ai_credits_api.py`

**Interfaces:**
- Consumes: Task 1's zeroed tier
- Produces: `check_ai_credits` returns `{can_generate, purchased_credits_usd, monthly_cost_usd}` plus optional `autoreload_failed`. No `included_remaining_usd`, no `is_over_included`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ai_credits_api.py
"""The AI-credits API after the free grant is gone.

`can_generate` reduces to "there is a purchased balance". The two fields that
described the old grant are removed rather than pinned at zero, because the
only consumer is this repo's frontend and a permanently-zero field makes
every future reader work out for themselves that it is vestigial.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Company
from backend.models.ownership_group import OwnershipGroup
from backend.services.billing import check_ai_credits
from tests.conftest import _id

pytestmark = pytest.mark.asyncio

REMOVED_FIELDS = ("included_remaining_usd", "is_over_included")


async def _tenant(db: AsyncSession, credits: float) -> str:
    og_id, company_id = _id(), _id()
    db.add(OwnershipGroup(id=og_id, name="G", ai_credits_usd=credits))
    await db.flush()
    db.add(Company(id=company_id, name="C", slug=_id(),
                   ownership_group_id=og_id))
    await db.commit()
    return company_id


async def test_the_removed_fields_are_gone(db_session: AsyncSession):
    status = await check_ai_credits(db_session, await _tenant(db_session, 5.0))
    for field in REMOVED_FIELDS:
        assert field not in status, f"{field} should have been removed"


async def test_a_balance_allows_generation(db_session: AsyncSession):
    status = await check_ai_credits(db_session, await _tenant(db_session, 5.0))
    assert status["can_generate"] is True
    assert status["purchased_credits_usd"] == pytest.approx(5.0)


async def test_a_zero_balance_blocks_generation(db_session: AsyncSession):
    """This is the paywall. Previously the $2 grant made this True."""
    status = await check_ai_credits(db_session, await _tenant(db_session, 0.0))
    assert status["can_generate"] is False
    assert status["purchased_credits_usd"] == 0.0


async def test_a_company_with_no_ownership_group_is_ungated(
    db_session: AsyncSession
):
    """Matches the convention everywhere else: seed/dev data is not gated."""
    company_id = _id()
    db_session.add(Company(id=company_id, name="Loose", slug=_id()))
    await db_session.commit()

    status = await check_ai_credits(db_session, company_id)
    assert status["can_generate"] is True
    for field in REMOVED_FIELDS:
        assert field not in status


async def test_a_failed_autoreload_still_blocks(db_session: AsyncSession):
    """Unchanged behaviour — the hold is independent of the grant."""
    from datetime import datetime, timezone

    og_id, company_id = _id(), _id()
    db_session.add(OwnershipGroup(
        id=og_id, name="G", ai_credits_usd=50.0,
        autoreload_failed_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    ))
    await db_session.flush()
    db_session.add(Company(id=company_id, name="C", slug=_id(),
                           ownership_group_id=og_id))
    await db_session.commit()

    status = await check_ai_credits(db_session, company_id)
    assert status["can_generate"] is False
    assert status["autoreload_failed"] is True


async def test_the_schedule_quota_keeps_its_own_free_tier(
    db_session: AsyncSession
):
    """GUARD RAIL. check_schedule_quota has an UNRELATED is_over_included for
    the metered 50-schedules-per-month allowance. Removing fields by name
    across billing.py would break schedule billing; this test fails loudly if
    someone does."""
    from backend.services.billing import check_schedule_quota

    quota = await check_schedule_quota(db_session, await _tenant(db_session, 0.0))
    assert "is_over_included" in quota
    assert "schedules_included" in quota
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/bin/python -m pytest tests/test_ai_credits_api.py -v`
Expected: `test_the_removed_fields_are_gone` FAILS — both fields are still present.

- [ ] **Step 3: Strip the fields from `check_ai_credits`**

In `backend/services/billing.py`, in `check_ai_credits` ONLY (starts ~line 666):

- Delete `included_remaining_usd` and `is_over_included` from all three return dicts (the no-group early return, the `autoreload_failed` return, and the final return).
- Delete the `free_remaining` and `is_over` local computations.
- Replace the `can_generate` computation with:

```python
    # The base subscription no longer includes any AI spend (#64), so this is
    # simply "has the customer bought credits". The old expression also
    # allowed generation while a monthly free grant lasted.
    can_generate = purchased_credits > 0
```

- Update the docstring's Returns block to list only the surviving keys.

**Do not touch `check_schedule_quota`.**

- [ ] **Step 4: Strip the field from `check_and_record_usage` and the summary**

In `check_and_record_usage`: delete `included_remaining_usd` from the no-group early return and from the final return dict, and remove the two docstring lines describing it and `is_over_included`. Leave `is_over_included` in its return **only if** a caller still reads it — check with `grep -rn "check_and_record_usage" backend/` and follow the callers; if none reads it, delete it too.

In `get_full_billing_summary`'s `llm` block (~line 777), delete `included_usd`, `included_remaining_usd` and `is_over_included`, keeping `raw_cost_usd`, `charged_usd` and `overage_markup`.

In `backend/routers/billing.py` lines 75-76, delete the same three keys from the zero-state fallback dict.

- [ ] **Step 5: Run tests**

Run: `backend/.venv/bin/python -m pytest tests/test_ai_credits_api.py tests/ -q`
Expected: the new file passes; fix any suite failures by following the same rule as Task 1 — never weaken an assertion.

- [ ] **Step 6: Commit**

```bash
git add backend/services/billing.py backend/routers/billing.py tests/test_ai_credits_api.py
git commit -m "feat(billing): drop free-tier fields from the AI-credits API

can_generate reduces to 'has a purchased balance'. check_schedule_quota's
unrelated is_over_included (the metered 50/month schedule allowance) is
deliberately untouched, and a test guards it."
```

---

### Task 3: Idempotent credit purchases — packs, index, and the crediting service

The money path. Everything here is server-side; the endpoints are Task 4.

**Files:**
- Modify: `backend/config.py`
- Create: `backend/alembic/versions/0031_unique_billing_charge_stripe_object.py`
- Modify: `backend/services/billing.py` (add `credit_purchase` helpers)
- Test: `tests/test_credit_purchase.py`

**Interfaces:**
- Consumes: nothing from Tasks 1-2
- Produces:
  - `settings.CREDIT_PACKS_USD: list[float]`
  - `def pack_amount(pack_id: str) -> float` — raises `ValueError` on an unknown pack
  - `async def apply_credit_purchase(db, og_id: str, amount_usd: float, stripe_object_id: str) -> bool` — returns `True` if it credited, `False` if this Stripe object was already applied

- [ ] **Step 1: Write the failing test**

```python
# tests/test_credit_purchase.py
"""Buying AI credits.

The important property is idempotency. The balance is credited by TWO paths —
a Stripe webhook and a confirm endpoint the return page calls — so a customer
who reloads the return page while the webhook is in flight can be credited
twice for one payment. The guard is a partial unique index on
BillingCharge.stripe_object_id, not application logic, following the pattern
migration 0029 already established for the ownership_groups Stripe columns.
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models.billing_charge import BillingCharge
from backend.models.ownership_group import OwnershipGroup
from backend.services.billing import apply_credit_purchase, pack_amount
from tests.conftest import _id

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def og(db_session: AsyncSession) -> OwnershipGroup:
    group = OwnershipGroup(id=_id(), name="G", ai_credits_usd=0.0)
    db_session.add(group)
    await db_session.commit()
    return group


# --- packs ------------------------------------------------------------------

def test_packs_are_configured():
    assert settings.CREDIT_PACKS_USD == [10.0, 25.0, 50.0]


def test_pack_amount_resolves_a_known_pack():
    assert pack_amount("10") == 10.0
    assert pack_amount("50") == 50.0


def test_pack_amount_rejects_an_unknown_pack():
    """An arbitrary amount must not be smuggled in through the pack id — that
    would let a caller name their own price."""
    for bad in ("1", "0", "-10", "9999", "abc", ""):
        with pytest.raises(ValueError):
            pack_amount(bad)


# --- crediting --------------------------------------------------------------

async def test_a_purchase_credits_the_balance(
    db_session: AsyncSession, og: OwnershipGroup
):
    credited = await apply_credit_purchase(db_session, og.id, 25.0, "cs_test_1")

    assert credited is True
    await db_session.refresh(og)
    assert float(og.ai_credits_usd) == pytest.approx(25.0)


async def test_a_purchase_records_a_billing_charge(
    db_session: AsyncSession, og: OwnershipGroup
):
    await apply_credit_purchase(db_session, og.id, 25.0, "cs_test_2")

    charge = (await db_session.execute(select(BillingCharge))).scalar_one()
    assert charge.kind == "ai_credit_purchase"
    assert float(charge.amount_usd) == pytest.approx(25.0)
    assert charge.stripe_object_id == "cs_test_2"
    assert charge.status == "succeeded"


async def test_the_same_stripe_object_credits_only_once(
    db_session: AsyncSession, og: OwnershipGroup
):
    """The webhook and the confirm endpoint racing on one payment."""
    first = await apply_credit_purchase(db_session, og.id, 25.0, "cs_test_3")
    second = await apply_credit_purchase(db_session, og.id, 25.0, "cs_test_3")

    assert first is True
    assert second is False, "the second application must not credit again"

    await db_session.refresh(og)
    assert float(og.ai_credits_usd) == pytest.approx(25.0), "balance doubled"

    count = (await db_session.execute(
        select(func.count()).select_from(BillingCharge)
    )).scalar_one()
    assert count == 1


async def test_the_loser_of_the_race_does_not_raise(
    db_session: AsyncSession, og: OwnershipGroup
):
    """The customer's money arrived and their balance is right. That is not an
    error to surface — the second caller returns False, it does not blow up."""
    await apply_credit_purchase(db_session, og.id, 10.0, "cs_test_4")
    assert await apply_credit_purchase(db_session, og.id, 10.0, "cs_test_4") is False
    # Session still usable after the swallowed integrity error.
    assert (await db_session.execute(select(func.count()).select_from(BillingCharge))).scalar_one() == 1


async def test_distinct_purchases_both_credit(
    db_session: AsyncSession, og: OwnershipGroup
):
    await apply_credit_purchase(db_session, og.id, 10.0, "cs_test_5")
    await apply_credit_purchase(db_session, og.id, 25.0, "cs_test_6")

    await db_session.refresh(og)
    assert float(og.ai_credits_usd) == pytest.approx(35.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/bin/python -m pytest tests/test_credit_purchase.py -v`
Expected: FAIL — `ImportError: cannot import name 'apply_credit_purchase'`

- [ ] **Step 3: Add the packs config**

In `backend/config.py`, below `LLM_OVERAGE_MARKUP`:

```python
    # Fixed credit packs, in USD. Fixed rather than an arbitrary amount so the
    # purchase is a one-click decision and the Landing page can advertise
    # concrete prices. The pack id is the amount as a string ("10", "25",
    # "50"); pack_amount() rejects anything not in this list, so a caller
    # cannot name their own price through the id.
    CREDIT_PACKS_USD: list[float] = [10.0, 25.0, 50.0]
```

- [ ] **Step 4: Write the migration**

```python
# backend/alembic/versions/0031_unique_billing_charge_stripe_object.py
"""unique billing_charges.stripe_object_id where not null

Credits are applied by two independent paths — the Stripe webhook and the
confirm endpoint the return page calls — so one payment can be processed
twice. This makes the second application fail in the database rather than in
application logic a future caller could forget.

Partial (WHERE NOT NULL) because most BillingCharge rows have no Stripe object
id and would otherwise collide with each other. Same shape as migration 0029.

Revision ID: 0031
Revises: 0030
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: Union[str, None] = "0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_billing_charges_stripe_object_id",
        "billing_charges",
        ["stripe_object_id"],
        unique=True,
        postgresql_where=sa.text("stripe_object_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_billing_charges_stripe_object_id", table_name="billing_charges"
    )
```

Mirror it on the model in `backend/models/billing_charge.py` by adding `__table_args__`:

```python
    __table_args__ = (
        sa.Index(
            "uq_billing_charges_stripe_object_id",
            "stripe_object_id",
            unique=True,
            postgresql_where=sa.text("stripe_object_id IS NOT NULL"),
        ),
    )
```

Import `sqlalchemy as sa` there if it is not already imported.

**SQLite note:** SQLite honours `unique=True` but ignores `postgresql_where`, so under the test suite the index is unconditionally unique. Every existing `BillingCharge` row written by tests must therefore have a distinct `stripe_object_id` or `None` — `NULL`s do not collide under a unique index in either engine. If an existing test writes two rows with the same non-null id, fix that test.

- [ ] **Step 5: Write the crediting service**

Append to `backend/services/billing.py`:

```python
def pack_amount(pack_id: str) -> float:
    """Resolve a credit pack id to its dollar amount.

    The id is the amount as a string. Validating against the configured list
    rather than parsing the id is the point: a caller must not be able to name
    their own price by passing an arbitrary number.
    """
    for amount in settings.CREDIT_PACKS_USD:
        if pack_id == str(int(amount)) or pack_id == str(amount):
            return float(amount)
    raise ValueError(f"Unknown credit pack: {pack_id!r}")


async def apply_credit_purchase(
    db: AsyncSession,
    og_id: str,
    amount_usd: float,
    stripe_object_id: str,
) -> bool:
    """Credit a completed purchase exactly once. True if applied, False if
    this Stripe object was already processed.

    Called by BOTH the checkout.session.completed webhook and the confirm
    endpoint the return page hits, either of which may arrive first. The
    partial unique index on stripe_object_id is the arbiter — the loser
    catches IntegrityError and reports "already applied" rather than raising,
    because the customer's money did arrive and their balance is correct.
    That is not an error condition to surface to anyone.
    """
    from sqlalchemy.exc import IntegrityError

    from backend.models.billing_charge import BillingCharge

    og = await db.get(OwnershipGroup, og_id)
    if og is None:
        raise ValueError(f"Unknown ownership group: {og_id!r}")

    db.add(BillingCharge(
        ownership_group_id=og_id,
        kind="ai_credit_purchase",
        amount_usd=amount_usd,
        stripe_object_id=stripe_object_id,
        status="succeeded",
    ))
    og.ai_credits_usd = float(og.ai_credits_usd) + amount_usd

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        logger.info(
            "Credit purchase %s already applied; ignoring duplicate",
            stripe_object_id,
        )
        return False

    return True
```

Confirm `logger` and `OwnershipGroup` are already imported at module level in `billing.py`; add them if not.

- [ ] **Step 6: Run tests**

Run: `backend/.venv/bin/python -m pytest tests/test_credit_purchase.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 7: Verify the migration against real Postgres**

The SQLite suite cannot catch a Postgres-only DDL problem, and the `postgresql_where` clause is Postgres-specific.

```bash
docker-compose up -d postgres
export DATABASE_URL="postgresql+asyncpg://shiftsync:shiftsync@localhost:5433/shiftsync"
cd backend && alembic upgrade head && alembic downgrade 0030 && alembic upgrade head
docker exec wiz_scheduler-postgres-1 psql -U shiftsync -d shiftsync -c "\d billing_charges"
```

Expected: all three succeed, and `\d` shows the partial unique index with its `WHERE (stripe_object_id IS NOT NULL)` predicate.

- [ ] **Step 8: Commit**

```bash
git add backend/config.py backend/models/billing_charge.py backend/alembic/versions/0031_unique_billing_charge_stripe_object.py backend/services/billing.py tests/test_credit_purchase.py
git commit -m "feat(billing): idempotent credit purchases with fixed packs

Two crediting paths (webhook + confirm endpoint) mean one payment can be
processed twice. A partial unique index on stripe_object_id makes the second
fail in the database, following migration 0029's pattern."
```

---

### Task 4: The purchase endpoints and the webhook handler

**Files:**
- Modify: `backend/routers/billing.py`
- Modify: `backend/routers/webhooks.py`
- Test: `tests/test_credit_purchase_api.py`

**Interfaces:**
- Consumes: Task 3's `pack_amount`, `apply_credit_purchase`, `settings.CREDIT_PACKS_USD`
- Produces: `GET /billing/credit-packs`, `POST /billing/buy-credits`, `POST /billing/confirm-credit-purchase`, and a `checkout.session.completed` webhook branch

- [ ] **Step 1: Write the failing test**

```python
# tests/test_credit_purchase_api.py
"""The credit purchase endpoints.

Stripe is mocked throughout — these tests are about our routing, our
authorization, and our idempotency, not about Stripe's behaviour.
"""

from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import Company, User
from backend.models.billing_charge import BillingCharge
from backend.models.ownership_group import OwnershipGroup
from tests.conftest import _id, _make_token

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def tenant(db_session: AsyncSession) -> dict:
    og_id, company_id, user_id = _id(), _id(), _id()
    db_session.add(OwnershipGroup(
        id=og_id, name="G", ai_credits_usd=0.0,
        stripe_customer_id="cus_test", stripe_subscription_id="sub_test",
    ))
    await db_session.flush()
    db_session.add(Company(id=company_id, name="C", slug=_id(),
                           ownership_group_id=og_id))
    await db_session.flush()
    db_session.add(User(id=user_id, company_id=company_id,
                        email=f"{user_id}@example.com", hashed_password="x",
                        full_name="M", user_role="manager"))
    await db_session.commit()
    return {
        "og_id": og_id,
        "company_id": company_id,
        "headers": {"Authorization":
                    f"Bearer {_make_token(user_id, company_id, 'manager')}"},
    }


async def test_packs_are_listed(client: AsyncClient, tenant: dict):
    resp = await client.get("/api/v1/billing/credit-packs",
                            headers=tenant["headers"])

    assert resp.status_code == 200, resp.text
    amounts = [p["amount_usd"] for p in resp.json()["packs"]]
    assert amounts == settings.CREDIT_PACKS_USD


async def test_buying_credits_returns_a_checkout_url(
    client: AsyncClient, tenant: dict, monkeypatch
):
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_x")
    session = MagicMock(id="cs_test_buy", url="https://checkout.stripe.com/x")

    with patch("stripe.checkout.Session.create", return_value=session) as create:
        resp = await client.post("/api/v1/billing/buy-credits",
                                 json={"pack_id": "25"},
                                 headers=tenant["headers"])

    assert resp.status_code == 200, resp.text
    assert resp.json()["url"] == "https://checkout.stripe.com/x"
    # The amount must come from OUR config, never from the request body.
    assert create.call_args.kwargs["line_items"][0]["price_data"]["unit_amount"] == 2500


async def test_an_unknown_pack_is_refused(
    client: AsyncClient, tenant: dict, monkeypatch
):
    """A caller must not be able to name their own price."""
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_x")

    resp = await client.post("/api/v1/billing/buy-credits",
                             json={"pack_id": "1"},
                             headers=tenant["headers"])

    assert resp.status_code == 400


async def test_buying_requires_authentication(client: AsyncClient):
    resp = await client.post("/api/v1/billing/buy-credits",
                             json={"pack_id": "25"})
    assert resp.status_code in (401, 403)


async def test_confirming_credits_the_balance(
    client: AsyncClient, db_session: AsyncSession, tenant: dict, monkeypatch
):
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_x")
    session = MagicMock(
        id="cs_test_confirm", payment_status="paid",
        metadata={"og_id": tenant["og_id"], "amount_usd": "25.0"},
    )

    with patch("stripe.checkout.Session.retrieve", return_value=session):
        resp = await client.post("/api/v1/billing/confirm-credit-purchase",
                                 json={"session_id": "cs_test_confirm"},
                                 headers=tenant["headers"])

    assert resp.status_code == 200, resp.text
    og = await db_session.get(OwnershipGroup, tenant["og_id"])
    await db_session.refresh(og)
    assert float(og.ai_credits_usd) == pytest.approx(25.0)


async def test_confirming_twice_credits_once(
    client: AsyncClient, db_session: AsyncSession, tenant: dict, monkeypatch
):
    """The customer reloading the return page."""
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_x")
    session = MagicMock(
        id="cs_test_twice", payment_status="paid",
        metadata={"og_id": tenant["og_id"], "amount_usd": "25.0"},
    )

    with patch("stripe.checkout.Session.retrieve", return_value=session):
        first = await client.post("/api/v1/billing/confirm-credit-purchase",
                                  json={"session_id": "cs_test_twice"},
                                  headers=tenant["headers"])
        second = await client.post("/api/v1/billing/confirm-credit-purchase",
                                   json={"session_id": "cs_test_twice"},
                                   headers=tenant["headers"])

    assert first.status_code == 200
    assert second.status_code == 200, "a duplicate confirm is not an error"

    og = await db_session.get(OwnershipGroup, tenant["og_id"])
    await db_session.refresh(og)
    assert float(og.ai_credits_usd) == pytest.approx(25.0)
    assert (await db_session.execute(
        select(func.count()).select_from(BillingCharge)
    )).scalar_one() == 1


async def test_an_unpaid_session_credits_nothing(
    client: AsyncClient, db_session: AsyncSession, tenant: dict, monkeypatch
):
    """Someone replaying a session id for an abandoned checkout."""
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_x")
    session = MagicMock(
        id="cs_test_unpaid", payment_status="unpaid",
        metadata={"og_id": tenant["og_id"], "amount_usd": "25.0"},
    )

    with patch("stripe.checkout.Session.retrieve", return_value=session):
        resp = await client.post("/api/v1/billing/confirm-credit-purchase",
                                 json={"session_id": "cs_test_unpaid"},
                                 headers=tenant["headers"])

    assert resp.status_code == 402
    og = await db_session.get(OwnershipGroup, tenant["og_id"])
    await db_session.refresh(og)
    assert float(og.ai_credits_usd) == 0.0


async def test_confirming_another_tenants_session_is_refused(
    client: AsyncClient, db_session: AsyncSession, tenant: dict, monkeypatch
):
    """The session's metadata names an ownership group; it must match the
    caller's, or one tenant could credit itself from another's payment."""
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_x")
    session = MagicMock(
        id="cs_test_foreign", payment_status="paid",
        metadata={"og_id": "someoneelse", "amount_usd": "25.0"},
    )

    with patch("stripe.checkout.Session.retrieve", return_value=session):
        resp = await client.post("/api/v1/billing/confirm-credit-purchase",
                                 json={"session_id": "cs_test_foreign"},
                                 headers=tenant["headers"])

    assert resp.status_code in (403, 404)
    og = await db_session.get(OwnershipGroup, tenant["og_id"])
    await db_session.refresh(og)
    assert float(og.ai_credits_usd) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/bin/python -m pytest tests/test_credit_purchase_api.py -v`
Expected: FAIL — 404 on every route.

- [ ] **Step 3: Add the schemas**

In `backend/schemas/billing.py` (create the classes alongside the existing ones):

```python
class CreditPack(BaseModel):
    pack_id: str
    amount_usd: float


class CreditPacksResponse(BaseModel):
    packs: list[CreditPack]


class BuyCreditsRequest(BaseModel):
    pack_id: str


class BuyCreditsResponse(BaseModel):
    url: str
    session_id: str


class ConfirmCreditPurchaseRequest(BaseModel):
    session_id: str


class ConfirmCreditPurchaseResponse(BaseModel):
    credited: bool
    balance_usd: float
```

- [ ] **Step 4: Add the endpoints**

In `backend/routers/billing.py`:

```python
@router.get("/credit-packs", response_model=CreditPacksResponse)
async def get_credit_packs(
    current_user: User = Depends(require_manager),
) -> CreditPacksResponse:
    """The purchasable credit packs. Fixed amounts, defined server-side."""
    return CreditPacksResponse(packs=[
        CreditPack(pack_id=str(int(a)), amount_usd=a)
        for a in settings.CREDIT_PACKS_USD
    ])


@router.post("/buy-credits", response_model=BuyCreditsResponse)
async def buy_credits(
    body: BuyCreditsRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> BuyCreditsResponse:
    """Start a Stripe Checkout session for one credit pack."""
    import stripe

    from backend.services.billing import get_ownership_group_id, pack_amount

    try:
        amount = pack_amount(body.pack_id)
    except ValueError:
        # The amount comes from OUR config, never the request body — otherwise
        # a caller could name their own price.
        raise HTTPException(status_code=400, detail="Unknown credit pack")

    og_id = await get_ownership_group_id(db, str(current_user.company_id))
    if not og_id:
        raise HTTPException(status_code=404, detail="No ownership group")

    stripe.api_key = settings.STRIPE_SECRET_KEY
    session = stripe.checkout.Session.create(
        mode="payment",
        customer=(await db.get(OwnershipGroup, og_id)).stripe_customer_id,
        line_items=[{
            "quantity": 1,
            "price_data": {
                "currency": "usd",
                "unit_amount": int(amount * 100),
                "product_data": {"name": f"WizScheduler AI credits — ${amount:.0f}"},
            },
        }],
        # Read back by BOTH the webhook and the confirm endpoint.
        metadata={"og_id": og_id, "amount_usd": str(amount),
                  "kind": "ai_credit_purchase"},
        success_url=settings.STRIPE_BILLING_PORTAL_RETURN_URL,
        cancel_url=settings.STRIPE_BILLING_PORTAL_RETURN_URL,
    )
    return BuyCreditsResponse(url=session.url, session_id=session.id)


@router.post("/confirm-credit-purchase",
             response_model=ConfirmCreditPurchaseResponse)
async def confirm_credit_purchase(
    body: ConfirmCreditPurchaseRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> ConfirmCreditPurchaseResponse:
    """Credit a completed Checkout session.

    Paired with the webhook rather than replacing it: whichever arrives first
    credits, and apply_credit_purchase makes the loser a no-op.
    """
    import stripe

    from backend.services.billing import (
        apply_credit_purchase,
        get_ownership_group_id,
    )

    og_id = await get_ownership_group_id(db, str(current_user.company_id))
    if not og_id:
        raise HTTPException(status_code=404, detail="No ownership group")

    stripe.api_key = settings.STRIPE_SECRET_KEY
    session = stripe.checkout.Session.retrieve(body.session_id)

    metadata = session.metadata or {}
    if metadata.get("og_id") != og_id:
        # One tenant must not be able to credit itself from another's payment.
        raise HTTPException(status_code=403, detail="Session belongs to another account")

    if session.payment_status != "paid":
        raise HTTPException(status_code=402, detail="Payment not completed")

    credited = await apply_credit_purchase(
        db, og_id, float(metadata["amount_usd"]), session.id
    )
    og = await db.get(OwnershipGroup, og_id)
    return ConfirmCreditPurchaseResponse(
        credited=credited, balance_usd=float(og.ai_credits_usd)
    )
```

Add the imports for the new schema classes at the top of the file.

- [ ] **Step 5: Add the webhook branch**

In `backend/routers/webhooks.py`, alongside the other `if event_type == ...` branches:

```python
    if event_type == "checkout.session.completed":
        session = event["data"]["object"]
        metadata = session.get("metadata") or {}
        if metadata.get("kind") != "ai_credit_purchase":
            # Subscription checkouts are handled by the invoice events.
            return {"received": True, "type": event_type, "handled": False}

        from backend.services.billing import apply_credit_purchase

        credited = await apply_credit_purchase(
            db, metadata["og_id"], float(metadata["amount_usd"]), session["id"]
        )
        return {"received": True, "type": event_type,
                "og_id": metadata["og_id"], "credited": credited}
```

- [ ] **Step 6: Run tests**

Run: `backend/.venv/bin/python -m pytest tests/test_credit_purchase_api.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 7: Commit**

```bash
git add backend/routers/billing.py backend/routers/webhooks.py backend/schemas/billing.py tests/test_credit_purchase_api.py
git commit -m "feat(billing): credit purchase endpoints and webhook

Amounts come from server config, never the request body. The confirm endpoint
verifies the session's metadata names the caller's own ownership group."
```

---

### Task 5: Frontend — types, client, i18n

**Files:**
- Modify: `frontend/src/api/billing.ts`
- Modify: all 19 files in `frontend/src/i18n/`

**Interfaces:**
- Consumes: Task 4's endpoints
- Produces: `getCreditPacks()`, `buyCredits(packId)`, `confirmCreditPurchase(sessionId)`; `AiCreditStatus` without the removed fields; i18n keys under `schedule`

- [ ] **Step 1: Update the types and add the client functions**

In `frontend/src/api/billing.ts`:

Remove `included_remaining_usd` and `is_over_included` from `AiCreditStatus`. **Leave `ScheduleQuota.is_over_included` alone — it is the unrelated schedule allowance.** Remove `included_usd`, `included_remaining_usd` and `is_over_included` from `BillingUsage.llm`.

Add:

```typescript
export interface CreditPack {
  pack_id: string;
  amount_usd: number;
}

export function getCreditPacks(): Promise<{ packs: CreditPack[] }> {
  return apiFetch("/billing/credit-packs");
}

export function buyCredits(packId: string): Promise<{ url: string; session_id: string }> {
  return apiFetch("/billing/buy-credits", {
    method: "POST",
    body: JSON.stringify({ pack_id: packId }),
  });
}

export function confirmCreditPurchase(
  sessionId: string
): Promise<{ credited: boolean; balance_usd: number }> {
  return apiFetch("/billing/confirm-credit-purchase", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId }),
  });
}
```

- [ ] **Step 2: Add the English copy**

In `frontend/src/i18n/en.ts`, inside the existing `schedule` namespace:

```typescript
    creditsBalance: "AI credits",
    buyCredits: "Buy credits",
    noCreditsTitle: "AI generation needs credits",
    noCreditsBody:
      "Your plan includes the Rotation, Fairness and Max-Hours schedulers in full. AI generation is billed by usage — buy credits to switch it on.",
    creditPack: "${amount}",
    purchaseFailed: "Could not start the purchase. Try again.",
    purchaseCredited: "Credits added. Balance is now ${balance}.",
```

Remove the now-unused `freeRemaining` key **only if nothing else references it** — check with `grep -rn "freeRemaining" frontend/src`.

- [ ] **Step 3: Mirror into the other 18 locales**

`LanguageContext.tsx` types translations as `Record<Language, Translations>` derived from `en.ts`, so **a key present only in English fails the build.** Add translated equivalents of every key above to: `ar bn de es fr hi id ja mr pcm pt ru ta te tr ur vi zh`.

Keep `{amount}` and `{balance}` verbatim in every locale — they are substituted with `.replace()` at the call site, and a translated placeholder fails silently at runtime rather than at build time. `pcm` is Nigerian Pidgin, a distinct language; do not leave English there.

If you remove `freeRemaining`, remove it from all 19.

**Verify the locale diff is purely ADDITIVE** apart from any deliberate `freeRemaining` removal — `git diff --numstat frontend/src/i18n/` should show no unexpected deletions. A previous bulk edit in this repo silently dropped an existing key from 18 files.

- [ ] **Step 4: Verify the build**

Run: `cd frontend && npx tsc --noEmit`
Expected: exit 0. A missing locale key surfaces here and names the offending locale.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/billing.ts frontend/src/i18n
git commit -m "feat(billing): frontend credit-purchase client, types and copy"
```

---

### Task 6: The paywall on the Schedule page

**Files:**
- Modify: `frontend/src/pages/manager/Schedule.tsx` (~lines 1040-1065)

**Interfaces:**
- Consumes: Task 5's `getCreditPacks`, `buyCredits`, `confirmCreditPurchase`, `AiCreditStatus`

- [ ] **Step 1: Replace the free-remaining indicator**

At `Schedule.tsx` ~line 1050, the block currently reads `creditStatus.is_over_included ? ... : ` $${creditStatus.included_remaining_usd.toFixed(2)} ...``. Both fields are gone. Replace that indicator with the purchased balance:

```tsx
<span className={text.muted}>
  {t.schedule.creditsBalance}: ${creditStatus.purchased_credits_usd.toFixed(2)}
</span>
```

**Leave every `scheduleQuota.is_over_included` reference untouched** (~lines 1008 and 1019) — that is the schedule allowance, a different tier entirely.

- [ ] **Step 2: Add the paywall state**

When `creditStatus.can_generate` is false and `autoreload_failed` is not set, render the offer instead of letting the button 402:

```tsx
{!creditStatus.can_generate && !creditStatus.autoreload_failed && (
  <div className="glass-alert-info mt-2">
    <p className="font-medium">{t.schedule.noCreditsTitle}</p>
    <p className={`text-sm ${text.muted}`}>{t.schedule.noCreditsBody}</p>
    <div className="mt-2 space-x-2">
      {packs.map((p) => (
        <button
          key={p.pack_id}
          onClick={() => void startPurchase(p.pack_id)}
          className="glass-btn-primary"
        >
          {t.schedule.creditPack.replace("{amount}", p.amount_usd.toFixed(0))}
        </button>
      ))}
    </div>
  </div>
)}
```

with:

```tsx
const [packs, setPacks] = useState<CreditPack[]>([]);

useEffect(() => {
  getCreditPacks().then((r) => setPacks(r.packs)).catch(() => setPacks([]));
}, []);

const startPurchase = async (packId: string) => {
  try {
    const { url } = await buyCredits(packId);
    window.location.href = url;   // Stripe Checkout redirect
  } catch {
    setError(t.schedule.purchaseFailed);
  }
};
```

- [ ] **Step 2b: Disable the AI Generate control rather than letting it fail**

Find the AI generate button's `disabled` expression (the same one that already reads `plan?.over_limit === true || generationCapReached` around line 1106) and add `|| creditStatus?.can_generate === false`. A disabled control with a visible reason is a price; an enabled control that 402s reads as a bug.

- [ ] **Step 3: Handle the return from Checkout**

Stripe returns to `STRIPE_BILLING_PORTAL_RETURN_URL`, which is the Schedule page. Confirm the session on arrival:

```tsx
useEffect(() => {
  const params = new URLSearchParams(window.location.search);
  const sessionId = params.get("session_id");
  if (!sessionId) return;

  confirmCreditPurchase(sessionId)
    .then((r) => {
      setNotice(t.schedule.purchaseCredited.replace(
        "{balance}", r.balance_usd.toFixed(2)
      ));
      void refreshCreditStatus();
      // Strip the param so a reload does not re-confirm. Harmless if it does
      // — apply_credit_purchase is idempotent — but it keeps the URL clean.
      window.history.replaceState({}, "", window.location.pathname);
    })
    .catch(() => setError(t.schedule.purchaseFailed));
}, []);
```

Match `setNotice` / `setError` / `refreshCreditStatus` to whatever the file already uses; read the surrounding code rather than assuming these names exist.

**`success_url` must carry the session id.** In `backend/routers/billing.py`'s `buy_credits`, change `success_url` to append `?session_id={CHECKOUT_SESSION_ID}` — Stripe substitutes that placeholder. Without it this effect never fires and the balance is credited only by the webhook.

- [ ] **Step 4: Verify the build**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: both succeed.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/manager/Schedule.tsx backend/routers/billing.py
git commit -m "feat(billing): paywall AI generation with a buy-credits offer"
```

---

### Task 7: End-to-end verification and docs

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Full suite**

Run: `backend/.venv/bin/python -m pytest tests/ -q` and `cd frontend && npx tsc --noEmit && npm run build`
Expected: all pass.

- [ ] **Step 2: Verify against real Postgres**

```bash
docker-compose up -d postgres
export DATABASE_URL="postgresql+asyncpg://shiftsync:shiftsync@localhost:5433/shiftsync"
cd backend && alembic upgrade head
docker exec wiz_scheduler-postgres-1 psql -U shiftsync -d shiftsync \
  -c "\d billing_charges" | grep -i stripe_object_id
```

Expected: the partial unique index appears with its `WHERE (stripe_object_id IS NOT NULL)` predicate.

Then prove idempotency on Postgres, not just SQLite — the partial predicate only exists there:

```sql
INSERT INTO billing_charges (id, ownership_group_id, kind, amount_usd, stripe_object_id, status, created_at)
  VALUES ('bc000001', '<some og id>', 'ai_credit_purchase', 25.0, 'cs_dup', 'succeeded', now());
INSERT INTO billing_charges (id, ownership_group_id, kind, amount_usd, stripe_object_id, status, created_at)
  VALUES ('bc000002', '<same og id>', 'ai_credit_purchase', 25.0, 'cs_dup', 'succeeded', now());
```

Expected: the second INSERT fails on `uq_billing_charges_stripe_object_id`. Then confirm two rows with `NULL` stripe_object_id both insert fine.

- [ ] **Step 3: Update the README**

In the billing/pricing section, state that the $18 base subscription includes **no** AI spend, that AI generation requires purchased credits in fixed packs, and that the deterministic schedulers (Rotation, Fairness, Max-Hours) remain included and unmetered. Note `INCLUDED_LLM_USD` is retained as a per-environment knob defaulting to `0.00`.

- [ ] **Step 4: Update CLAUDE.md**

Add under Architecture:

```markdown
### AI credits (`backend/services/billing.py`)

The base subscription includes no AI spend (#64). `INCLUDED_LLM_USD` is kept
as a per-environment knob but defaults to `0.00`; at zero, the partially-free
branch of `check_and_record_usage` is unreachable and
`tests/test_llm_free_tier_zero.py` pins that. Credits are bought in fixed
`CREDIT_PACKS_USD` through Stripe Checkout and credited by BOTH the
`checkout.session.completed` webhook and `POST /billing/confirm-credit-purchase`;
`apply_credit_purchase` is idempotent via a partial unique index on
`billing_charges.stripe_object_id`.

Not to be confused with `check_schedule_quota`, which has its own unrelated
`is_over_included` for the metered 50-schedules-per-month allowance.
```

- [ ] **Step 5: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs(billing): AI credits are purchased, not included"
```

---

## Self-Review

**Spec coverage**

| Spec section | Task |
|---|---|
| Internal ledger, not an Anthropic transfer | documented in spec; no code |
| `INCLUDED_LLM_USD` kept at 0.00 | 1 |
| Dead-branch test | 1 |
| Remove `included_remaining_usd` / `is_over_included` | 2 |
| `can_generate` = balance > 0 | 2 |
| `POST /billing/buy-credits`, Checkout redirect | 4 |
| Webhook + confirm endpoint | 4 |
| Idempotency via partial unique index, migration 0031 | 3 |
| Fixed packs in config | 3, 4 |
| Paywall with CTA; local scheduler unaffected | 6 |
| 402 paths unchanged | 2 (test), 6 (state guard) |
| Landing page excluded | Global Constraints |
| Testing list | 1, 3, 4, 7 |

**Deliberate spec deviations**

- The spec wanted each pack to show "about N generations", derived from measured `TokenUsage` data. **Not implemented** — the plan ships packs as bare dollar amounts. The spec itself says to omit rather than guess, and deriving an honest average is a data question best answered against production rows, not invented in a task. Worth a follow-up issue.

**Placeholder scan:** none — every code step carries real code.

**Type consistency:** `pack_amount`, `apply_credit_purchase`, `CREDIT_PACKS_USD`, `CreditPack`, `getCreditPacks`, `buyCredits`, `confirmCreditPurchase` are used with identical signatures across Tasks 3-6. `AiCreditStatus` loses exactly the two fields named in Task 2 and nowhere else.

**Known cross-task ordering trap:** Task 6 Step 3 changes `success_url` in `backend/routers/billing.py`, a file Task 4 created the handler in. That is deliberate — the frontend need only becomes visible in Task 6 — but the implementer of Task 6 must edit a backend file, and the commit in Task 6 Step 5 includes it.
