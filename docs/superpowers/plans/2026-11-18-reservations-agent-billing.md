# AI Host Subscription & Usage Billing Implementation Plan (step 5 of 11)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restaurants opt in to the AI host with their own Stripe
subscription ($10/month, first $5 of usage included, then our cost × 1.5);
the agent answers only for companies with an active subscription and stays
inside per-location daily spend caps; usage is metered per conversation and
billed as invoice items on that subscription.

**Architecture:** A new `reservation_subscriptions` row per ownership group
holds the agent subscription (separate from the scheduling subscription).
`agent_usage` holds one row per billable conversation (billed amount,
period, invoiced flag). Overage is reconciled onto the agent subscription's
upcoming invoice as a pending `InvoiceItem`, using the same reconcile
pattern `bill_monthly_overages_for_og` already uses for storage/employee
overage (BillingCharge rows of a new kind). All Stripe calls live in
`backend/services/billing_reservations.py` (`stripe-outside-billing`
ratchet). Webhooks route agent-subscription events by subscription id so
they never touch the scheduling subscription's state.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, stripe-python (already
installed; follow the existing module-level pattern in services/billing.py
for consistency), React, vitest.

**Spec:** `docs/superpowers/specs/2026-11-17-reservation-agent-design.md`
section 9 ("Billing: the agent subscription"), section 12 step 5; decisions
log (subscription is a deliberate second money path).

## Global Constraints

- Money calls only in `backend/services/billing*.py`.
- Prices from settings: `RESERVATIONS_PRICE_ID` (licensed $10/month),
  `RESERVATIONS_INCLUDED_USAGE_USD = 5.00`, `RESERVATIONS_USAGE_MARKUP =
  1.5`. No Stripe metered price: overage is an InvoiceItem (see Decision
  below). Empty `RESERVATIONS_PRICE_ID` → subscribe endpoints answer 503
  `not_configured`.
- Billed amount per conversation = `round_half_up(cost_usd × 1.5, 0.01)`;
  conversations with outcome `blocked` or cost 0 bill nothing.
- Included usage: the first $5.00 of billed amount per agent-subscription
  billing period is free; overage = max(0, period total − 5.00).
- Post-conversation billing never raises into the call path; metering
  happens in recording.finish (a DB write only) and Stripe is only called
  by the reconcile job / webhook.
- The agent answers only when: `RESERVATIONS_ENABLED` and the location's
  ownership group has a reservation subscription with status in
  {active, trialing} and the location's daily spend cap isn't reached.
  Otherwise public routes answer 404 (no subscription) or a closed-chat
  reply (cap reached).
- The existing scheduling-subscription webhook paths must behave exactly
  as before for the scheduling subscription (tests pin it).
- CLAUDE.md "AI spend always debits purchased credits" gets an explicit
  exception for the reservation agent in the same PR.

## Decision (record as decision 37)

Stripe recommends Metronome for new usage-based billing; that adds a
vendor. Billing Meters is a lower-level primitive. The app already bills
overage as reconciled InvoiceItems on the subscription's upcoming invoice;
the agent overage uses the same pattern: exact (we hold per-conversation
billed amounts), no new vendor or dependency, invoice line reads
"AI host usage over $5 — <period>". Cost if wrong: moving to Meters or
Metronome later replaces only the reconcile job.

## Review Focus

1. **Agent-subscription events must never touch the scheduling
   subscription**: `invoice.payment_failed` for the agent subscription must
   not set `autoreload_failed_at`; `customer.subscription.deleted` for it
   must not mark the account cancelled. Tests in Task 2.
2. **Double billing**: the reconcile is idempotent per (og, period) — a
   second run with unchanged usage is a no-op; a change replaces the
   pending item. Test in Task 3.
3. **Usage landing in the wrong period** at a period boundary: usage
   is assigned to the period containing the conversation's `ended_at`
   using the subscription's stored period bounds. Test in Task 1.
4. **Cap reached mid-conversation**: an open conversation may finish its
   current turn; new turns get the cap reply. Test in Task 1.
5. **Cancelled subscription**: the agent stops answering at the end of the
   paid period (`cancel_at_period_end`) and immediately on deletion; the
   last period's overage is still reconciled before the final invoice
   (handled on `invoice.upcoming`). Test in Tasks 2–3.

---

### Task 1: Models, metering, gate

**Files:** `backend/reservations/subscription.py` (models + queries; no Stripe), migration `0059_reservation_subscriptions.py`, `backend/models/billing_charge.py` (+ kind `invoice_item_reservation_agent` in the model check and the migration), `backend/reservations/agent/recording.py` (meter on finish), `backend/reservations/rules.py` (+ `daily_spend_cap_usd: float = 20.0`, ge 1), `backend/routers/public_reservations.py` (gate), config, tests.

**Interfaces:**
- `ReservationSubscription(id, ownership_group_id (unique, FK ownership_groups), stripe_subscription_id unique nullable, status ("incomplete"|"active"|"trialing"|"past_due"|"canceled"|"unpaid"), current_period_start, current_period_end, cancel_at_period_end bool, created_at, updated_at)` — RLS block following 0052's ownership-group pattern (read 0052/0056 for keyed-by-OG tables).
- `AgentUsage(id, company_id, ownership_group_id, location_id, conversation_id unique FK agent_conversations ON DELETE SET NULL, cost_usd Numeric(12,6), billed_usd Numeric(12,2), period_start (tz), product "reservation_agent", created_at)`. Deliberately survives conversation deletion (retention) — no diner data in it.
- `subscription.billed_amount(cost: Decimal) -> Decimal`; `async subscription.active_for_location(db, company_id, location_id, now) -> ReservationSubscription | None`; `async subscription.spend_today(db, location_id, now, tz) -> Decimal` (sum of agent_conversations.cost_usd started in the location's local day, open conversations included); `async subscription.period_usage(db, og_id, period_start) -> Decimal`.
- `recording.finish` creates the AgentUsage row (idempotent on conversation_id) when outcome != blocked and cost > 0, using the subscription's current period_start (or the UTC month start if none).
- Public routes: no active subscription → 404 (same body as other misses); cap reached at conversation start → 201 with a closed conversation and reply "Online booking is busy right now — please call the restaurant." (phone if set); cap reached on a later turn → that turn answers the same and closes.

- [ ] Tests (failing first): billed_amount rounding (0.2449→0.37, 0.0033→0.00 → no row); finish creates exactly one usage row, none for blocked; gate 404 without subscription and with canceled status; cap reached at start and mid-conversation; usage assigned by period bounds.
- [ ] Implement; run `pytest tests/reservations -q` + test_migrations.
- [ ] Commit `feat(agent-billing): subscription model, usage metering and spend caps (#259)`

### Task 2: Subscribe, manage, webhooks

**Files:** `backend/services/billing_reservations.py`, `backend/routers/billing.py` (thin routes), `backend/routers/webhooks.py` (routing), tests.

**Interfaces:**
- `POST /api/v1/billing/reservation-agent/checkout` (require_admin; flag on; 503 if price unset; 409 if already active) → Checkout Session `mode=subscription`, `line_items=[{price: RESERVATIONS_PRICE_ID, quantity: 1}]`, existing customer, `subscription_data.metadata={"product": "reservation_agent", "og_id": ...}`, no `payment_method_types`, success/cancel URLs to the billing page. Returns `{url}`.
- `POST /api/v1/billing/reservation-agent/confirm {session_id}` → verifies the session belongs to the OG's customer and is a reservation_agent subscription, upserts the row (idempotent with the webhook).
- `GET /api/v1/billing/reservation-agent` → status, period, cancel_at_period_end, usage this period (billed total, included 5.00, overage so far), per-location today's spend vs cap.
- Cancel/payment-method changes go through the existing Customer Portal link (no new cancel route).
- Webhook routing in `services/billing_reservations.is_agent_subscription(sub_or_invoice) -> bool` (subscription id matches a ReservationSubscription row, or subscription metadata product == reservation_agent). For agent objects: `checkout.session.completed` (mode subscription) → upsert; `customer.subscription.created/updated` → sync status/period/cancel flag; `customer.subscription.deleted` → status canceled; `invoice.upcoming` → reconcile agent overage (Task 3); `invoice.payment_failed` → status past_due only (no scheduling hold); `invoice.payment_succeeded` → sync period. Scheduling-subscription handling unchanged for non-agent objects.
- Webhook subscription list: document `customer.subscription.created` needs adding in the Stripe dashboard (owner checklist).

- [ ] Tests (failing first, Stripe mocked like existing billing tests): checkout params (mode, price, no payment_method_types, metadata); confirm idempotent with webhook; each agent event updates only the agent row; scheduling events behave exactly as before (existing tests stay green + one new test per routed event type proving no cross-talk); 503 when price unset.
- [ ] Implement; run billing + webhook + reservations tests.
- [ ] Commit `feat(agent-billing): agent subscription checkout, status and webhook routing (#259)`

### Task 3: Overage reconcile

**Interfaces:**
- `async billing_reservations.reconcile_agent_overage(db, og, now=None) -> dict` mirrors `bill_monthly_overages_for_og`: retrieve the agent subscription (skip unless active/trialing/past_due), period = its current period_start (ISO date), overage_cents = max(0, period_usage − 5.00) × 100; existing pending BillingCharge (kind `invoice_item_reservation_agent`, period) equal → noop; else delete the old InvoiceItem (ignore errors), create a new one (customer, subscription = agent sub, amount, currency usd, description "AI host usage over $5.00 — <period>", metadata og_id/period/kind, idempotency key `agent-overage-<og>-<period>-<cents>`), record BillingCharge pending; zero → delete. Commits; returns a summary; never raises on Stripe errors (logs + returns error).
- Called from the `invoice.upcoming` agent route and from a daily job `backend/scripts/run_agent_overage_reconcile.py` (all OGs with active agent subscriptions). Terraform schedule for the job is part of step 6's infra PR (owner applies); the script is runnable now.
- `invoice.payment_succeeded` for the agent subscription marks that period's pending BillingCharge `succeeded`.

- [ ] Tests: zero usage → no item; $7.50 billed → $2.50 item; rerun → noop; usage grows → item replaced (delete + create, one pending row); Stripe error → logged, no exception, no row change; payment_succeeded flips status.
- [ ] Commit `feat(agent-billing): reconcile AI host overage onto the agent invoice (#259)`

### Task 4: Billing UI, docs

**Files:** frontend billing page section, rules editor (daily cap field), i18n ×19, CLAUDE.md convention update, architecture skill entry points, decisions 37–38, owner checklist update (create Stripe product/price, set RESERVATIONS_PRICE_ID, add webhook events, schedule the reconcile job).

- Billing page "AI host" card (admins): not subscribed → price, what's included, Subscribe (checkout); subscribed → status, renews/cancels on date, usage this period (billed / included / overage), per-location today vs cap, "Manage" (portal).
- Rules editor: "Daily AI host spend cap ($)".
- CLAUDE.md: amend the "AI spend always debits purchased credits" bullet: scheduling AI as before; the reservation agent bills through its own subscription (`services/billing_reservations.py`), the only other money path.

- [ ] Frontend helper tests (usage formatting, status labels); `npm test`, `tsc`, build; backend full suite.
- [ ] Commit `feat(agent-billing): billing page card, daily cap setting, docs (#259)`
