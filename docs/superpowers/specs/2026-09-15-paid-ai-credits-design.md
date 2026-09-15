# Paid AI Credits — Design

**Date:** 2026-09-15
**Issue:** #64 — Remove free AI credits from the base subscription
**Status:** approved in conversation; implementation follows this spec.

## Goal

AI Generate runs only on credits the customer has bought. The $18
subscription stops bundling any LLM spend. A new paid subscriber sees a
purchase step before their first AI run rather than a 402, and the site
operator is told, per purchase, how much to add on the Anthropic side.

Decisions made by the owner in conversation:

| Decision | Choice |
|---|---|
| Free grant | Keep `INCLUDED_LLM_USD` as a config knob, default `0.00`. A demo environment may set it above zero. |
| Cold start | Hard paywall. AI Generate stays blocked until the customer buys a pack. No implicit first charge. |
| Purchase model | Fixed packs of $10 / $25 / $50, charged off-session to the card already on the subscription. |
| Auto-reload default | Off for new ownership groups. The purchase modal offers opt-in at the pack size. Existing groups keep their setting. |
| Existing subscribers | No grandfathering, no notice. Their next AI run hits the paywall like everyone else's. |
| Operator alert | Email on every successful credit charge (pack purchase and auto-reload refill) with the Anthropic-side amount to buy. |

## Non-goals

- Buying Anthropic Console credits from code. There is no public API for
  it. The operator alert (section 8) is the bridge; the Console's own
  auto-reload is the operational backstop.
- Changing the schedule, storage, or employee allowances. Those stay
  included in the subscription.
- Stripe Checkout for purchases, invoices for credits, refunds, or
  transferring balance between groups.
- Free-plan access to AI. `check_can_generate` already refuses it; packs
  are paid-plan only.

## Current state (what changes from)

- `INCLUDED_LLM_USD = 2.00` in `backend/config.py`. Renamed from
  `LLM_FREE_TIER_USD` in PR #82; the issue text predates that.
- `check_and_record_usage` (`backend/services/billing.py`) splits each
  generation's token cost into a free part and a part charged at
  `LLM_OVERAGE_MARKUP` (130%). Before debiting, it calls
  `auto_reload_if_needed`, which charges the saved card when the balance
  would fall under the threshold, and **raises** when auto-reload is off
  or no card is on file. `graph.py` then calls `deduct_credits_for_overage`,
  floored at zero.
- `check_ai_credits` allows AI when the grant has room or
  `ai_credits_usd > 0`, and blocks when `autoreload_failed_at` is set.
- Auto-reload defaults on (`server_default=true`, threshold $2, refill
  $10). The card is cached from the subscription by the Stripe webhook.
- There is no purchase endpoint. "Buy Credits" on the Schedule page opens
  the auto-reload settings modal. Money moves only through auto-reload
  and `POST /billing/autoreload/retry`.
- Landing advertises "$2.00 AI credits / month" and an overage note that
  quotes $3/$15 per million tokens while config bills $2/$10 (Sonnet 5).

## 1. Billing math

`INCLUDED_LLM_USD` default becomes `0.00`. The comment block above the
metered allowances explains that AI is no longer one of them and that the
knob exists for demo environments.

The cost-split code in `check_and_record_usage` stays. At zero it
degenerates correctly: `cost_before >= 0` is always true, so every
generation, including the first of a month, is charged `cost × markup`.
Tests cover both the zero default and a monkeypatched positive grant so
the split branch stays exercised rather than silently dead.

**The AI debit path stops raising.** With auto-reload off by default,
the post-generation call to `auto_reload_if_needed` would raise
`AutoReloadDisabled` whenever the balance is short, turning a finished
generation into a `PIPELINE_ERROR` after the tokens were spent. New rule
for `check_and_record_usage`:

- Usage rows (monthly and daily) are always written.
- A reload is attempted only when `autoreload_enabled` is true and the
  balance after this charge would be under the threshold.
- `AutoReloadDisabled` is not raised here at all (the guard is checked
  first, not caught). `AutoReloadError` from a declined card is caught
  and logged; `auto_reload_if_needed` has already written the failed
  `BillingCharge` row and set `autoreload_failed_at`, so the next pre-gate
  blocks. `AutoReloadBlocked` cannot occur because the pre-gate refuses a
  group with `autoreload_failed_at` set.
- The debit in `deduct_credits_for_overage` keeps its floor at zero. The
  pre-gate requires a positive balance, so the worst case is one
  generation's shortfall, a few cents, on the last run before the paywall.

`deduct_credits_for_schedule_overage` is unchanged. Schedule overage
keeps its existing gate in `check_schedule_quota`.

## 2. Pre-generation gate

`check_ai_credits` returns:

```
can_generate           bool   purchased > 0 or included_remaining > 0, and not on hold
included_remaining_usd float  unchanged; reads 0.0 at the default knob
purchased_credits_usd  float  unchanged
is_over_included       bool   unchanged
monthly_cost_usd       float  unchanged
autoreload_failed      bool   present and true only when on hold (unchanged)
purchase_required      bool   NEW: true when blocked by balance, false when on hold or allowed
packs_usd              list   NEW: settings.AI_CREDIT_PACKS_USD
```

`GET /schedules/ai-credits` passes this through. The 402 raised by
`POST /schedules/generate` in AI mode becomes a structured detail like the
daily-cap one:

```json
{"code": "ai_credits_required", "message": "AI credits are needed for AI Generate. Buy a credit pack to continue."}
```

`frontend/src/api/billing.ts` gains the two new fields on `AiCreditStatus`.

## 3. Purchase endpoint and shared charge helper

### Config

```python
AI_CREDIT_PACKS_USD: tuple[float, ...] = (10.0, 25.0, 50.0)
OPERATOR_ALERT_EMAIL: str = ""
```

### Charge helper

The Stripe `PaymentIntent` block inside `auto_reload_if_needed` moves to
one function in `backend/services/billing.py`:

```python
async def charge_saved_card(db, og, amount_usd, kind) -> BillingCharge
```

- Creates the off-session PaymentIntent on `og.default_payment_method_id`
  with `metadata={"og_id", "kind"}`.
- On success: `og.ai_credits_usd += amount_usd`, writes a `succeeded`
  `BillingCharge(kind=kind)`, flushes, returns the row.
- On `stripe.StripeError` or a non-`succeeded` intent: writes a `failed`
  row, flushes, raises `AutoReloadError`. It does **not** touch
  `autoreload_failed_at`; that is the caller's decision.

`auto_reload_if_needed` keeps its guards (on-hold, threshold, enabled,
card present), calls the helper with `kind="autoreload"`, and on
`AutoReloadError` sets `autoreload_failed_at` before re-raising. Its
observable behaviour and its existing tests do not change.

### Endpoint

`POST /billing/credits/purchase`

Request:

```json
{"amount_usd": 10.0, "enable_autoreload": false}
```

Rules, in order:

1. `require_manager`; `assert_paid_plan(db, company_id, "ai_credits")` →
   402 `ai_credits_requires_paid_plan` for free groups.
2. `amount_usd` not in `AI_CREDIT_PACKS_USD` → 400.
3. `autoreload_failed_at` set → 409 `{"code": "billing_on_hold"}`. The
   retry flow clears it; a fresh purchase must not silently bypass it.
4. No `stripe_customer_id` or `default_payment_method_id` → 402
   `{"code": "no_payment_method"}`.
5. Lock the row (`with_for_update`), call `charge_saved_card(kind="purchase")`.
   On `AutoReloadError` commit (the failed row must persist) and return 402
   `{"code": "card_declined", "message": <stripe message>}`.
   `autoreload_failed_at` stays untouched: nothing automatic failed.
6. If `enable_autoreload`: `autoreload_enabled = True`,
   `autoreload_amount_usd = amount_usd`. Threshold is left as is.
7. Commit, fire the operator alert (section 8), return
   `AutoReloadStatus` (it already carries `current_balance_usd`).

`BillingCharge.kind` gains the value `purchase`. The frontend
`BillingChargeRow.kind` union gains it too.

## 4. Migration 0035

```
ALTER ownership_groups ALTER autoreload_enabled SET DEFAULT false
DROP CONSTRAINT billing_charges_kind_check; ADD ... kind IN ('autoreload','purchase','invoice_item_storage','invoice_item_employees')
```

Existing rows are not updated. The model's `server_default` and the
`CheckConstraint` text change to match. Downgrade reverses both; rows
with `kind='purchase'` would block the downgrade constraint, which is the
correct signal.

## 5. Frontend

### Schedule page (`frontend/src/pages/manager/Schedule.tsx`)

Credit strip:

- Normal: `AI credits: $12.40 balance`.
- Knob above zero (demo): `AI credits: $12.40 balance · $2.00 included this month`.
- Balance zero and `purchase_required`: red strip, `AI credits: $0.00 balance`,
  a **Buy credits** button.
- On hold: the existing red banner with Retry payment / Update card stays
  the primary affordance; the strip shows the balance without a buy
  button.

`handleGenerate` already routes a blocked AI run to the modal; that stays.

Purchase modal (replaces the current billing modal, same state variables):

- Title "AI credits". One sentence of context that depends on
  `purchaseReason` (`ai` or `schedules`): both say the balance pays for
  AI Generate and for schedules beyond the included 50.
- Three pack buttons from `packs_usd`, each labelled `$10`, `$25`, `$50`.
  Clicking one charges immediately; the button shows a spinner and the
  others disable while the request is in flight.
- Checkbox above the buttons: "Auto-reload this amount when my balance
  runs out". Sends `enable_autoreload`.
- Error line under the buttons for 402/409 responses, using the `code` to
  pick copy: declined, no card (with the Manage billing link), on hold.
- Below a rule: the existing auto-reload status grid and Edit flow,
  unchanged, so threshold and refill stay adjustable.
- Footer: Manage billing (portal), Close.
- On success: refresh `creditStatus`, `scheduleQuota`, and `autoReload`;
  close the modal.

`frontend/src/api/billing.ts` gains
`purchaseCredits(amount_usd, enable_autoreload): Promise<AutoReloadStatus>`.

### Landing (`frontend/src/pages/Landing.tsx`)

- Pricing tile: the `$2.00` stat becomes `From $10` with caption
  `AI credit packs`. The other three stats stay.
- The sentence about Rotation, Max Hours and Random being always included
  moves from the muted footnote to the line directly under the price,
  reworded: "Rotation, Max Hours and Random scheduling are always
  included. AI Generate runs on prepaid credit packs."
- `basePlanDesc` no longer says "generous free tiers"; it names what is
  included: 50 schedules, 0.5 GB, 1K employees.
- `aiOverageNote` becomes: "Claude Sonnet 5 token cost ($2/M in, $10/M
  out) at 130%, debited from prepaid packs of $10, $25 or $50." Fixes the
  stale $3/$15 while the line is being rewritten.
- Example bill: the AI row reads `~$1.50 in tokens` → `$1.95 from credits`;
  the hard-coded total `$20.30` becomes `$22.25`. The four other rows are
  unchanged.

### i18n (all 19 files under `frontend/src/i18n/`)

Removed from `schedule`: `freeRemaining`, `freeTierUsed`,
`purchasedRemaining`, `buyAiCredits`, `creditAmount`, `purchase`,
`currentBalance`, `monthlyUsage` (the last five are unreferenced today).
`redirectingToPayment` stays; it is still used elsewhere on the page.

Added to `schedule`: `balanceLabel`, `includedThisMonth`,
`purchaseModalTitle`, `purchaseModalBody`, `packButton` (with `{amount}`),
`autoReloadOptIn`, `purchaseDeclined`, `purchaseNoCard`,
`purchaseOnHold`, `purchaseSuccess`.

Reworded in `schedule`: `creditsExhaustedMsg`,
`scheduleQuotaExhaustedMsg`, `autoReloadDescription`.

Reworded in `landing`: `basePlanDesc`, `aiCredits` (→ "AI credit packs"),
`normalStrategiesNote`, `aiOverageNote`, `exampleAICost`.

`TranslationKeys` is `typeof en`, so every locale must change in the same
commit or the build fails. Translations are written by the implementer;
`ar` and `ur` are RTL and must use logical Tailwind utilities in any new
markup (`logicalDirection.test.ts` enforces this).

## 6. Tests

Backend, `tests/test_billing.py` (edited) and `tests/test_credit_purchase.py`
(new), all against the Postgres test database like the rest of the suite:

- `check_and_record_usage` at the default knob: first generation of a
  month is charged at full markup; a later one too; `included_remaining_usd`
  is 0. With `INCLUDED_LLM_USD` monkeypatched to 2.0 the existing
  within/over split tests still pass.
- `check_and_record_usage` with auto-reload off and a balance below the
  charge: no exception, usage row written, balance floored at zero by the
  subsequent debit.
- `check_and_record_usage` with auto-reload on and a declined card: no
  exception, `autoreload_failed_at` set, failed `BillingCharge` written.
- `check_ai_credits`: balance 0 → `can_generate=False`,
  `purchase_required=True`; balance > 0 → allowed; on hold →
  `can_generate=False`, `purchase_required=False`; knob > 0 and balance 0
  → allowed.
- `POST /schedules/generate` in AI mode with balance 0 → 402 with
  `code=ai_credits_required` (extends `tests/test_plan_generation_gate.py`
  or the existing schedules tests, whichever already exercises the gate).
- `POST /billing/credits/purchase`: success adds the pack and writes a
  `purchase` row; bad amount → 400; free plan → 402; no card → 402
  `no_payment_method`; on hold → 409; declined → 402 `card_declined`,
  failed row written, `autoreload_failed_at` still null, balance
  unchanged; `enable_autoreload=true` flips the flag and sets the refill
  amount.
- `charge_saved_card` unit tests replace nothing: the existing
  `auto_reload_*` tests continue to pass unchanged and prove the refactor.
- Operator alert (section 8): sent with the right amounts on a purchase
  and on an auto-reload refill; only a log line when
  `OPERATOR_ALERT_EMAIL` is empty; a Resend exception leaves the purchase
  committed and the response 200.

Frontend: `npm run build` (type-checks every locale against `en`) and
`npm test` (vitest, including the logical-direction sweep). No new
component tests; the repo has none for this page.

## 7. Documentation

- `CLAUDE.md` conventions gain one bullet: AI spend always debits
  purchased credits; `INCLUDED_LLM_USD` is 0 by default and exists for
  demo environments; every successful credit charge goes through
  `charge_saved_card` so the operator alert fires.
- The issue's comment about wiring purchases to the Anthropic account is
  answered in the PR body: not possible via API; the alert is the bridge.

## 8. Operator alert

`backend/services/operator_alerts.py`:

```python
async def send_credit_purchase_alert(og, amount_usd, kind, total_prepaid_usd) -> bool
```

- Always logs one line:
  `[OPERATOR] credit_charge og=<id> kind=<kind> paid=<x> anthropic_equiv=<y> total_prepaid=<t> total_anthropic_equiv=<u>`
  so a CloudWatch metric filter can pick it up later.
- If `OPERATOR_ALERT_EMAIL` or `RESEND_API_KEY` is empty, returns False
  after the log line.
- Otherwise sends via Resend, `from FROM_EMAIL`, `to [OPERATOR_ALERT_EMAIL]`:
  - Subject: `[WizScheduler] AI credit purchase $10.00 — top up Anthropic by $7.69`
    (or `AI credit auto-reload …` for `kind="autoreload"`).
  - Body: group name and id, kind, amount paid, the Anthropic-side
    equivalent `amount / LLM_OVERAGE_MARKUP`, and the outstanding prepaid
    balance across all ownership groups (`SUM(ai_credits_usd)`) with its
    Anthropic-side equivalent, so the operator can compare that liability
    against the Console balance.
- Does not go through `check_and_log_email`; that cap is per customer
  tenant and this mail is not the customer's.
- Exceptions are logged and swallowed and the function never raises, so
  a mail failure cannot roll back or fail a charge. In the purchase
  endpoint it runs after the commit; inside the auto-reload path it runs
  after the flush and before the caller's commit, which is safe for the
  same reason.

Callers: the purchase endpoint (section 3, step 7) and every path that
completes an auto-reload: `check_and_record_usage`,
`deduct_credits_for_schedule_overage`, and `retry_autoreload`. The
simplest placement is inside `auto_reload_if_needed` after the helper
succeeds and the flush completes, plus one call in the purchase endpoint.
The alert reads `SUM(ai_credits_usd)` in its own short session so it does
not hold the caller's row lock.

## Data flow, end to end

```
new paid subscriber ─ AI Generate
   └ check_ai_credits: balance 0 → purchase_required
       └ Schedule page opens purchase modal
           └ POST /billing/credits/purchase {10, enable_autoreload}
               ├ charge_saved_card(kind="purchase") → Stripe PaymentIntent
               ├ balance += 10 ; BillingCharge(purchase, succeeded)
               ├ commit
               └ send_credit_purchase_alert → operator mail: "top up Anthropic by $7.69"
   └ AI Generate runs
       └ check_and_record_usage: charge = cost × 1.30
           ├ auto-reload only if enabled and balance would dip under threshold
           └ deduct_credits_for_overage: balance −= charge (floor 0)
```
