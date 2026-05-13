# Usage Overage Billing — Design Spec

**Status:** Approved (2026-05-11)
**Author:** Rubinder Randhawa
**Branch target:** f7 (or new branch off main once f7 merges)

## Goal

Charge customers for usage that exceeds their base subscription's included quotas:

| Resource | Included | Overage rate |
|---|---|---|
| AI scheduling cost | $2.00 / month | raw Anthropic cost × 130% |
| Schedules generated | 50 / month | $0.10 per 50 (block) |
| Employees | 1,000 | $1.00 per 1,000 (block) |
| Storage | 0.5 GB | $0.50 per GB |

Pricing values already exist in `backend/config.py:42-62` and match this table.

## Two-Track Billing Model

| Charge | Cadence | Stripe mechanism | Why |
|---|---|---|---|
| Base subscription ($18/mo) | Monthly recurring | Existing `Subscription` with `STRIPE_PRICE_ID` | Already live, unchanged |
| AI usage overage | Real-time (event-driven) | `PaymentIntent` (off-session) → `OwnershipGroup.ai_credits_usd` buffer | Cashflow positive; operator paid before LLM call |
| Schedule overage | Real-time (event-driven) | Same buffer | Shared with AI overage |
| Employee overage | Monthly | `InvoiceItem` on upcoming subscription invoice | Snapshot metric; bills cleanly with subscription |
| Storage overage | Monthly | `InvoiceItem` on upcoming subscription invoice | Snapshot metric; bills cleanly with subscription |

## Real-Time Track — Auto-Reload Buffer (AI + Schedules)

### Invariant
For an OwnershipGroup over its AI free tier, `ai_credits_usd >= 0` at all times. When usage would drive it below `autoreload_threshold_usd`, the backend synchronously charges the saved card to refill.

### Trigger Path
- `check_and_record_usage` (AI tokens) and `deduct_credits_for_schedule_overage` (schedules) are the only two debit paths
- Both wrap their flow in a `with_for_update()` SELECT against the OwnershipGroup row to serialize concurrent overage calls
- **Reload happens *before* the debit** (not after), so the balance is always sufficient by the time the debit applies:

```
with row_lock(og):
    if og.autoreload_failed_at: raise BlockedError(402)

    if og.ai_credits_usd - cost_usd < og.autoreload_threshold_usd:
        if not og.autoreload_enabled: raise BlockedError(402, "autoreload disabled")
        auto_reload(og)  # may raise; on success, og.ai_credits_usd was incremented

    if og.ai_credits_usd < cost_usd:
        # reload didn't top up enough — single refill < cost (very high single charge)
        # retry once with amount = max(autoreload_amount_usd, cost_usd + threshold_usd)
        auto_reload(og, override_amount=max(og.autoreload_amount_usd, cost_usd + og.autoreload_threshold_usd))

    og.ai_credits_usd -= cost_usd  # never negative now
```

- Caller blocks until the charge completes (typical: 1–3 seconds). For schedules this is fine (already a multi-second LLM call). For AI overage, the reload happens before the LLM call, so the customer waits before generation starts.

### Charge Action
```python
intent = stripe.PaymentIntent.create(
    customer=og.stripe_customer_id,
    amount=int(og.autoreload_amount_usd * 100),
    currency="usd",
    payment_method=og.default_payment_method_id,
    off_session=True,
    confirm=True,
    metadata={"og_id": og.id, "kind": "autoreload"},
)
```

- Success (`status == "succeeded"`): add `autoreload_amount_usd` to `ai_credits_usd`, insert `BillingCharge` row with `status="succeeded"`, `stripe_object_id=intent.id`
- Failure (`requires_action`, `payment_failed`, etc.): set `og.autoreload_failed_at = now()`, insert `BillingCharge` row with `status="failed"`, propagate error to caller as HTTP 402 with `{detail: "Auto-reload failed", reason: ...}`

### Blocked-Account State
While `autoreload_failed_at IS NOT NULL`:
- All AI generation requests return HTTP 402 with `{detail: "Billing on hold", retry_endpoint: "/billing/autoreload/retry"}`
- All schedule generation requests return HTTP 402 same way
- Manager UI shows a red banner with "Retry payment" button (calls retry endpoint) and "Update card" link (Stripe Billing Portal)
- Successful retry clears `autoreload_failed_at`

### Defaults
- `autoreload_enabled` = `true`
- `autoreload_threshold_usd` = `2.00` (refill when buffer below this)
- `autoreload_amount_usd` = `10.00` (refill amount)

Per-OG overrides via `PUT /billing/autoreload`. System defaults live in `backend/config.py` (new `AUTORELOAD_DEFAULT_*` settings).

## Monthly Track — InvoiceItems (Employees + Storage)

### Cadence
Weekly cron + `invoice.upcoming` webhook safeguard. Both invoke the same compute function — last-writer-wins is fine because of idempotent recompute (see below).

### Compute Function
```
def bill_monthly_overages_for_og(og):
    sub = stripe.Subscription.retrieve(og.stripe_subscription_id)
    if sub.status not in ("active", "trialing"):
        return  # skip suspended / canceled

    period = format_period(sub.current_period_start)  # e.g. "2026-05"

    for kind in ("storage", "employees"):
        charge_usd = compute_charge(og, kind, period)
        existing = find_pending_invoice_item(og.stripe_customer_id, period, kind)

        if charge_usd == 0:
            if existing: stripe.InvoiceItem.delete(existing.id)
            continue

        if existing and existing.amount != int(charge_usd * 100):
            stripe.InvoiceItem.delete(existing.id)
            existing = None

        if not existing:
            stripe.InvoiceItem.create(
                customer=og.stripe_customer_id,
                subscription=og.stripe_subscription_id,
                amount=int(charge_usd * 100),
                currency="usd",
                description=f"{kind.title()} overage — {period}",
                metadata={"og_id": og.id, "period": period, "kind": kind},
            )
            # also insert BillingCharge row for audit
```

### Idempotency
Per-run, per-(og, period, kind) the function re-snapshots the latest usage value. If the corresponding InvoiceItem already exists with the same amount, it's left alone. If the amount has changed, it's deleted and recreated. If the amount became zero, it's deleted. This makes the cron safe to run any cadence — weekly is sufficient, the webhook ensures final state matches reality just before invoice close.

### Scheduling
- Weekly cron via ECS Scheduled Task (EventBridge rule firing `aws_ecs_run_task` against the existing task definition with `containerOverrides.command=["python","-m","backend.billing.monthly_cron"]`). Runs Sundays 02:00 UTC.
- Webhook: `invoice.upcoming` fires ~1 day before invoice finalizes (configured at Stripe webhook setup). Backend processes it via `POST /webhooks/stripe` and invokes the same compute function for the affected OG.

## Data Model Changes

### Migration `alembic/versions/XXXX_billing_overage.py`

**Add to `ownership_groups`:**
- `autoreload_enabled BOOLEAN NOT NULL DEFAULT TRUE`
- `autoreload_threshold_usd NUMERIC(10,4) NOT NULL DEFAULT 2.0`
- `autoreload_amount_usd NUMERIC(10,4) NOT NULL DEFAULT 10.0`
- `autoreload_failed_at TIMESTAMPTZ NULL`
- `default_payment_method_id TEXT NULL`  -- cached from Stripe subscription

**New table `billing_charges`:**
- `id UUID PK DEFAULT gen_random_uuid()`
- `ownership_group_id UUID NOT NULL REFERENCES ownership_groups(id) ON DELETE CASCADE`
- `kind TEXT NOT NULL CHECK (kind IN ('autoreload', 'invoice_item_storage', 'invoice_item_employees'))`
- `amount_usd NUMERIC(10,4) NOT NULL`
- `stripe_object_id TEXT NULL`  -- PaymentIntent ID or InvoiceItem ID
- `period TEXT NULL`  -- "YYYY-MM", null for autoreload
- `status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed', 'pending'))`
- `error_message TEXT NULL`
- `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`
- Index: `(ownership_group_id, kind, period)` for idempotency lookups
- Index: `(ownership_group_id, created_at DESC)` for audit page

### One-Shot Migration for Existing Subscription
- For each OG with `stripe_subscription_id IS NOT NULL`:
  - Retrieve subscription from Stripe
  - Cache `default_payment_method_id = subscription.default_payment_method`
  - Initialize `ai_credits_usd = 0`, `autoreload_enabled = TRUE`
  - (defaults handle the rest)
- Right now there is exactly one production subscription (the operator's own). A one-shot script invoked once post-deploy is sufficient.

## API Changes

### New endpoints
- `GET /billing/autoreload` → `{enabled, threshold_usd, amount_usd, current_balance_usd, last_reload_at, failed_at}`
- `PUT /billing/autoreload` (body: `{enabled?, threshold_usd?, amount_usd?}`) — manager only
- `POST /billing/autoreload/retry` — re-attempt the most recent failed reload; clears `autoreload_failed_at` on success
- `GET /billing/charges?limit=50` → recent `BillingCharge` rows for audit
- `POST /webhooks/stripe` — unauthenticated; verifies signature via `STRIPE_WEBHOOK_SECRET`

### Webhook events handled
- `invoice.upcoming` → recompute monthly InvoiceItems for the affected OG
- `invoice.payment_failed` → set `autoreload_failed_at` (subscription itself failed, not auto-reload), suspend AI/schedule generation
- `payment_method.attached` → update `default_payment_method_id`
- `customer.subscription.deleted` → mark subscription as gone (out-of-scope: full account suspension; for now log)

### Removed endpoints
- `POST /billing/purchase-credits` — replaced by auto-reload
- `POST /billing/confirm-credits` — replaced by auto-reload

The matching frontend Purchase Credits UI is removed (see Frontend section).

### Augmented endpoints
- `GET /billing/usage` — adds `pending_invoice_items: [{kind, amount_usd, period}]` showing what storage + employees are projected to bill at next invoice

## Frontend Changes

### `pages/manager/Billing.tsx` (existing usage page)
- **Remove:** "Purchase AI Credits" card and modal
- **Add:** "Auto-Reload Settings" card
  - Toggle: enabled/disabled
  - Threshold input ($USD, min 1)
  - Refill amount input ($USD, min 5)
  - "Current balance: $X.XX" display
  - "Last refill: YYYY-MM-DD HH:MM, $X.XX" if any
- **Add:** "Pending Monthly Charges" panel showing projected storage/employee InvoiceItems for current period
- **Add:** Red banner when `autoreload_failed_at IS NOT NULL`:
  - "Billing on hold — your last automatic payment was declined."
  - "Retry payment" button → `POST /billing/autoreload/retry`
  - "Update card" link → opens Stripe Billing Portal (new `/billing/portal-link` endpoint creates a Stripe Customer Portal Session)
- **Add:** "Recent Charges" table from `GET /billing/charges`

### New API client functions
- `frontend/src/api/billing.ts` — `getAutoreload`, `updateAutoreload`, `retryAutoreload`, `getCharges`, `getPortalLink`

## Failure Modes & Edge Cases

| Scenario | Handling |
|---|---|
| Card declined on auto-reload | `autoreload_failed_at` set, AI/schedule generation blocked, manager retries via UI |
| SCA / 3DS required (`requires_action`) | Treated as failure; manager updates payment via Stripe Billing Portal then retries |
| Two concurrent overage calls both trigger reload | Row lock on `OwnershipGroup`; second waits, sees fresh balance, no second reload |
| Stripe API outage | Auto-reload raises HTTP 502 to caller; existing usage call fails; user retries |
| Subscription canceled/past_due | Webhook handler sets `autoreload_failed_at` (treats as suspended); both billing paths skipped |
| Stripe webhook signature mismatch | 400; no state change |
| Webhook delivered twice (Stripe retries) | Idempotent: compute function is safe to re-run; auto-reload PaymentIntent has its own idempotency via metadata + recent-charge check |
| Customer below free tier but balance still > 0 | No reload needed; nothing to do |
| Refunds / disputes | Out of scope; manual via Stripe dashboard |
| Operator's own subscription has no payment method cached yet | First overage call fails gracefully with 402 "Payment method not configured"; manager visits Billing Portal to add one |

## Configuration

**New env vars (terraform → Secrets Manager + ECS task env):**
- `STRIPE_WEBHOOK_SECRET` (secret) — `whsec_...` from Stripe dashboard webhook config
- `STRIPE_BILLING_PORTAL_RETURN_URL` (env) — typically `https://wizscheduler.com/manager/billing`

**New config defaults (`backend/config.py`):**
- `AUTORELOAD_DEFAULT_THRESHOLD_USD = 2.0`
- `AUTORELOAD_DEFAULT_AMOUNT_USD = 10.0`
- `AUTORELOAD_ENABLED_BY_DEFAULT = True`

## Phasing

**PR 1 — Auto-reload (real-time billing for AI + Schedules):**
1. Migration: new columns on `ownership_groups`, new `billing_charges` table
2. `services/billing.py`: new `auto_reload_if_needed(db, og)` helper
3. Wire helper into `check_and_record_usage` and `deduct_credits_for_schedule_overage`
4. New routers: `GET/PUT /billing/autoreload`, `POST /billing/autoreload/retry`, `GET /billing/charges`, `GET /billing/portal-link`
5. Remove `/billing/purchase-credits` and `/billing/confirm-credits` (router + frontend + tests)
6. Frontend: replace Purchase Credits card with Auto-Reload Settings card, add failed-state banner
7. One-shot migration script: backfill `default_payment_method_id` for existing OGs
8. Tests: unit tests for `auto_reload_if_needed` with mocked Stripe; integration test for `/billing/autoreload/*` endpoints

**PR 2 — Monthly InvoiceItems + Webhooks:**
1. `services/billing.py`: new `bill_monthly_overages_for_og(db, og)`
2. `backend/billing/monthly_cron.py`: entry point that iterates all OGs and invokes the function
3. `routers/webhooks.py`: new `/webhooks/stripe` endpoint with signature verification
4. Terraform: EventBridge rule for weekly ECS scheduled task, Stripe webhook resource (or manual webhook setup in Stripe dashboard)
5. Frontend: "Pending Monthly Charges" panel
6. Tests: cron idempotency tests, webhook signature verification tests

The PRs are independently mergeable. Either can ship without the other; PR 1 alone provides immediate cashflow protection for AI/schedule overage.

## Out of Scope

- Refunds and disputes (manual via Stripe dashboard)
- Per-customer pricing (everything uses the same rates from `config.py`)
- Multi-currency (USD only)
- Invoicing for storage/employees on a non-monthly cadence
- Real-time billing for storage/employees
- Customer-facing usage forecasting / spend caps (could be added later)
- Hard-blocking generation when over a configurable monthly spend ceiling (could be added later)
