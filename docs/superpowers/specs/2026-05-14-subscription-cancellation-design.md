# Subscription Cancellation Lifecycle — Design Spec

**Status:** Approved (2026-05-14)
**Author:** Rubinder Randhawa
**Target branch:** new branch off `main`

## Goal

Let a customer self-serve cancel their subscription via the Stripe Customer Portal, retain read-only access to their data for 90 days afterward, optionally reactivate during that window, and have the system hard-delete their data if they don't.

## State Machine

```
[active] ──cancel via Stripe Portal──> [pending_cancel]
                                          │
                                          │ Stripe: period ends, customer.subscription.deleted fires
                                          ▼
                                       [read_only_grace] ──90 days──> [deleted]
                                          │
                                          │ reactivate via Stripe Checkout
                                          ▼
                                       [active]
```

| State | DB indicator | User can | Backend behavior |
|---|---|---|---|
| **active** | `canceled_at = NULL`, `stripe_subscription_id` set | Everything | Normal |
| **pending_cancel** | Stripe sub has `cancel_at_period_end=true`, OG row unchanged | Everything | Banner only; no functional difference from active |
| **read_only_grace** | `canceled_at` set, age < 90d | View existing data, edit CRUD, reactivate | Block AI/schedule generation only |
| **deleted** | OG + dependents hard-deleted via CASCADE | Login fails (user record gone) | N/A |

## Data Model Changes

Single migration adds four columns to `ownership_groups`:

```sql
canceled_at TIMESTAMPTZ NULL                  -- set by customer.subscription.deleted webhook
notified_subscription_ended_at TIMESTAMPTZ NULL  -- idempotency for "your subscription has ended" email
notified_deletion_reminder_at TIMESTAMPTZ NULL   -- idempotency for "14 days until deletion" email
notified_data_deleted_at TIMESTAMPTZ NULL        -- idempotency for "your data has been deleted" email
```

`scheduled_deletion_at = canceled_at + interval '90 days'` is computed at query time, not stored — avoids drift if we ever change the grace period constant.

`pending_cancel` is **NOT** a stored state. It's queried on demand from Stripe via `stripe.Subscription.retrieve(og.stripe_subscription_id).cancel_at_period_end`. This keeps the DB authoritative for one thing (canceled vs not) and Stripe authoritative for the soft-pending state.

## Webhook Handlers

Two new event types added to the existing `POST /api/v1/webhooks/stripe` dispatch.

### `customer.subscription.updated`

Fires whenever the subscription state changes — including when the user toggles `cancel_at_period_end` in the Portal.

**Handler:**
- Look up OG by `obj.customer` ID
- Log the event for audit (`logger.info("Subscription updated for og=%s; cancel_at_period_end=%s", og.id, obj.cancel_at_period_end)`)
- **No DB mutation.** Stripe is the source of truth for the pending-cancel flag; the frontend reads it via `stripe.Subscription.retrieve` when it needs to render the "scheduled to cancel" banner.

### `customer.subscription.deleted`

Fires when the subscription is fully canceled — at `period_end` if `cancel_at_period_end=true`, immediately for hard cancels, or after Stripe's dunning retries exhaust on a chronically failing card.

**Handler:**
- Look up OG by `obj.customer` ID
- If `og.canceled_at` is already set, no-op (idempotent across redelivery)
- Otherwise: set `og.canceled_at = now()`, send "subscription ended" email, set `notified_subscription_ended_at = now()`, commit
- The 90-day deletion clock now ticks

## Access Control

A new dependency `require_active_billing` blocks paid-resource endpoints when the OG is in `read_only_grace`:

```python
async def require_active_billing(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> User:
    og_id = await get_ownership_group_id(db, current_user.company_id)
    if og_id:
        og = await db.get(OwnershipGroup, og_id)
        if og and og.canceled_at is not None:
            raise HTTPException(
                status_code=402,
                detail="Subscription canceled. Reactivate to resume scheduling.",
            )
    return current_user
```

**Applied to:**
- `POST /schedules/generate` (the only route that pays for compute via LLM tokens or schedule overage)
- Any other route that triggers `check_and_record_usage` or `deduct_credits_for_schedule_overage`

**NOT applied to:**
- All CRUD on `/employees`, `/locations`, `/regions`, `/roles`, etc. — users need to retrieve their data
- `/auth/*` — login must work so they can reach the Reactivate button
- `/billing/*` — they need to manage billing
- `/gdpr/*` — right-to-erasure must always work, even on canceled accounts

**Frontend behavior:** when `GET /billing/usage` returns `is_read_only: true`, the app renders a sitewide red-ish banner: "Your subscription ended on {date}. Data will be permanently deleted on {date + 90 days}. [Reactivate Subscription]".

## Reactivation

Stripe doesn't let you "un-delete" a subscription via API. After `subscription.deleted`, reactivation = a brand-new subscription.

**Flow:**
1. User in `read_only_grace` clicks **Reactivate Subscription** on the banner
2. Frontend calls `POST /api/v1/billing/reactivate-checkout` (new endpoint)
3. Backend creates a `stripe.checkout.Session` in `mode="subscription"` with the same `STRIPE_PRICE_ID`. Pre-fills `customer=og.stripe_customer_id` so Stripe reuses the existing Customer record (preserves saved payment methods + billing history). Success URL: `https://wizscheduler.com/manager/dashboard?reactivate_session_id={CHECKOUT_SESSION_ID}`
4. User completes Checkout
5. Frontend on `/manager/dashboard` reads `?reactivate_session_id=` from the URL, posts it to `POST /api/v1/billing/confirm-reactivation`
6. Backend verifies the session is `paid`, then updates OG:
   - `stripe_customer_id = session.customer` (usually unchanged, but Stripe can issue a new one)
   - `stripe_subscription_id = session.subscription`
   - `canceled_at = NULL`
   - All three `notified_*` columns cleared (so a future re-cancel goes through the notification flow again cleanly)
7. Frontend clears the query param, refreshes `/billing/usage`, banner disappears

The same flow handles a customer who let their grace period expire and now wants to re-sign-up — except by then their user records are deleted and they'd start fresh from `/register` instead of `/manager/dashboard`. That's a separate flow, not implemented here.

## UI

### Cancellation entry point
On the existing **Auto-Reload card** in `Schedule.tsx`, add a secondary "Manage Billing" button. Clicking it calls `getPortalLink()` and redirects to the Stripe Customer Portal. The Portal handles cancellation natively.

**Stripe Customer Portal config** (Stripe Dashboard → Settings → Billing → Customer portal):
- ✅ Enable "Cancel subscriptions" (must be set; not on by default)
- Choose cancellation mode: "Cancel at end of billing period" (matches our state machine)
- ✅ Already on: "Update payment method", "View invoice history"

### Sitewide read-only banner
When `og.canceled_at IS NOT NULL`, a banner renders at the top of every authenticated page (above the existing TopBar):

```
┌─────────────────────────────────────────────────────────────────────┐
│ ⚠️  Subscription ended {canceled_at}. Data deletion: {+90 days}.   │
│    [Reactivate Subscription]                                        │
└─────────────────────────────────────────────────────────────────────┘
```

### Cancellation card on Schedule page
When `og.canceled_at IS NOT NULL`, the Auto-Reload card on Schedule.tsx is **replaced** with a Cancellation card showing:
- "Subscription ended on {canceled_at}"
- "Your data will be permanently deleted on {canceled_at + 90 days}"
- Big "Reactivate Subscription" button → triggers the same flow as the banner button

### Pending-cancel banner (yellow)
On the Schedule page (just the Schedule page, not sitewide), if the manager loads it and the OG is in `pending_cancel`, fetch `stripe.Subscription.retrieve` server-side and pass `cancel_at_period_end + period_end` back via `/billing/usage`. Render a yellow info banner: "Subscription set to cancel on {period_end}. Open Stripe Portal to undo."

## Cron + Emails

A new **daily background loop** in `backend/main.py`, modeled on the existing `_daily_storage_snapshot_loop` and `_weekly_monthly_billing_loop`:

```python
async def _daily_cancellation_lifecycle_loop() -> None:
    """Walk canceled OGs daily. Send reminder emails at day 76, hard-delete + final email at day 90."""
    await asyncio.sleep(120)  # let the app settle
    while True:
        try:
            async with async_session_factory() as db:
                await process_cancellation_lifecycle(db)
        except Exception as e:
            log.error("Cancellation lifecycle failed: %s", e)
        await asyncio.sleep(24 * 60 * 60)
```

`process_cancellation_lifecycle(db)` does:

```python
now = datetime.now(timezone.utc)
canceled_ogs = (await db.execute(
    select(OwnershipGroup).where(OwnershipGroup.canceled_at.is_not(None))
)).scalars().all()

for og in canceled_ogs:
    age_days = (now - og.canceled_at).days

    # Day 76: send 14-day reminder (once)
    if age_days >= 76 and og.notified_deletion_reminder_at is None:
        send_deletion_reminder_email(og)
        og.notified_deletion_reminder_at = now

    # Day 90+: hard-delete + final email (once)
    if age_days >= 90 and og.notified_data_deleted_at is None:
        send_data_deleted_email(og)  # send BEFORE delete so we still have email
        og.notified_data_deleted_at = now  # tracked in a separate audit table since OG itself is going away
        # NOTE: see "Audit trail" below — flag is logically a tombstone
        await delete_og_and_dependents(db, og)

await db.commit()
```

### Audit trail (deleted OGs)

When an OG is hard-deleted, the row goes away — so `notified_data_deleted_at` can't live on the OG row. Two options:
1. **Simple:** Don't track the deletion notification as a re-runnable flag. Just send the email and trust the deletion is permanent (idempotency naturally holds — once the OG is gone, the loop won't see it again).
2. **Audit table:** Add `deleted_ownership_groups` table that records `og_id, name, canceled_at, deleted_at, final_admin_email`. Provides forensic trail and avoids re-sending if the email fails midway.

Going with option 1 for simplicity — keep YAGNI.

### Email templates
Reuse existing `RESEND_API_KEY` infra. Four templates (HTML in Python f-strings, same pattern as `_send_welcome_email` in `auth.py`):
- (Stripe handles "cancel confirmation" natively)
- "Subscription ended" — sent immediately on webhook, mentions 90-day grace
- "14 days until deletion" — sent at day 76 by the cron
- "Data deleted" — sent at day 90 by the cron, immediately before the actual deletion

## Edge Cases

| Scenario | Handling |
|---|---|
| User cancels then card is declined for a final invoice before period_end | Stripe still fires `subscription.deleted` at period_end. Treated like any cancel. |
| User cancels, the webhook is delivered twice (Stripe retry) | Handler checks `if og.canceled_at is not None: return` — idempotent |
| User reactivates, then cancels again | Standard flow — `canceled_at` set again, all `notified_*` cleared on reactivation so emails re-fire correctly |
| User reactivates on day 88 (right before deletion) | Reactivation clears `canceled_at`; deletion cron skips because `canceled_at IS NULL` |
| User reactivates on day 91+ but before the cron runs that day | Possible race window. Mitigation: reactivation endpoint takes a row-level lock and re-checks `canceled_at` before clearing. If deletion already happened, `og` wouldn't exist and the endpoint returns 404. |
| Webhook never delivers (Stripe outage during period_end) | Fallback: a separate daily reconciliation task could `stripe.Subscription.list(status='canceled')` and cross-check. Deferred to PR β if needed. |
| Customer wants their data immediately on cancellation | Already supported via `/gdpr/export` — independent of cancellation state |
| GDPR right-to-erasure during grace period | Also works — `/gdpr/erase` is exempt from `require_active_billing`. Sets a separate flag (`erased_at` already exists on User) |

## Phasing

### PR α — User-facing cancellation + reactivation
- Migration: `canceled_at` + 3 notification timestamps
- Webhook handlers: `customer.subscription.updated`, `customer.subscription.deleted`
- New service helper: `send_subscription_ended_email`
- New endpoints: `POST /billing/reactivate-checkout`, `POST /billing/confirm-reactivation`
- Augment `GET /billing/usage` with `is_read_only`, `canceled_at`, `pending_cancel` fields
- New dependency `require_active_billing`, applied to schedule generation routes
- Frontend: "Manage Billing" button on Auto-Reload card, Cancellation card replacing it when canceled, sitewide read-only banner, pending-cancel yellow banner, reactivation flow handler
- i18n: ~10 new keys across 19 locales
- Tests: webhook, reactivation, access-control denial

### PR β — Deletion cron + remaining notifications
- New `process_cancellation_lifecycle` service function + daily background loop in main.py
- 14-day reminder email template + helper
- Final "data deleted" email template + helper
- Hard-delete helper that walks CASCADE relationships (and verifies they're configured correctly)
- Tests: cron skips active OGs, sends reminder at day 76, deletes at day 90, idempotency on repeated runs

## Configuration Constants

New settings in `backend/config.py`:

```python
SUBSCRIPTION_GRACE_DAYS: int = 90
SUBSCRIPTION_REMINDER_DAYS_BEFORE_DELETE: int = 14
```

## Out of Scope

- Refunds for unused subscription time (handled in Stripe Dashboard manually if needed)
- Pro-rating credits on reactivation
- Partial cancellation (e.g., disable schedules but keep storage) — single all-or-nothing plan
- Self-serve "delete my data NOW" inside the grace period — they have GDPR erase already; rare enough we don't need a polished UI
- Sending a "we noticed you canceled, here's a 20% discount" winback email
- Audit table for deleted OGs (chose YAGNI)
- Stripe webhook signature verification for these two new event types (already covered by the existing endpoint-level signature check from PR 2)
