# AI Credits Purchased Separately — Design Spec

Issue: [#64](https://github.com/rubinder/wiz_scheduler/issues/64)
Status: approved, not yet implemented

## Goal

Remove the free AI credits bundled into the $18/month base subscription. AI
spend always debits a purchased balance. A manager with no balance sees a
priced offer, not an error.

## What "credits flow to the Anthropic account" can and cannot mean

The issue asks that purchased credits "flow to the Anthropic account" and be
"actually purchased on demand". **This is not buildable as written**, and the
spec says so up front so nobody later reads the issue title and goes hunting
for a pass-through that was never implemented.

Anthropic's Admin API was checked against live documentation on 2026-08-23.
Its complete surface is: organization members, invites, workspaces, workspace
members, API keys, service accounts, federation issuers and rules, usage and
cost **reports**, rate limits, and compliance. There is **no endpoint that
purchases credits, adds funds, tops up a balance, or reads a remaining
balance**. New API keys cannot even be created programmatically — the docs
state they are Console-only "for security reasons" — so a per-tenant
bring-your-own-Anthropic-account model could not be automated either.

What is buildable, and what this spec describes, is an **internal ledger**:

1. Stripe collects money from the customer.
2. `OwnershipGroup.ai_credits_usd` increases.
3. Real measured token spend debits it at `LLM_OVERAGE_MARKUP`.
4. WizScheduler settles its own Anthropic bill out of that revenue.

The customer's money is WizScheduler revenue. The credits are an accounting
entry, not a transfer to Anthropic.

## Billing math

`INCLUDED_LLM_USD` is **kept and defaults to `0.00`** rather than deleted.
The arithmetic in `check_and_record_usage` stays structurally intact, the diff
stays small and reviewable, and a grant can be reinstated per environment —
for the demo tenant, or a promotional month — without a code change.

The cost of keeping it is a dead branch. `check_and_record_usage` splits three
ways:

```python
if cost_before >= settings.INCLUDED_LLM_USD:      # full charge
elif cost_after > settings.INCLUDED_LLM_USD:      # partially free
else:                                              # entirely free
```

At a tier of `0.00`, `cost_before >= 0` is always true — costs are never
negative — so the first branch always wins and the other two are unreachable.
The middle branch is the one that would misprice silently if it ever ran with
a zero tier, because it computes `overage = cost_after - 0` and charges markup
on a figure it believes is partial.

**A test asserts that branch is unreachable at a zero tier**, rather than
leaving it to rot unread. That is the difference between a deliberate
configuration knob and dead code nobody dares delete.

## API surface

`check_ai_credits` and `GET /schedules/ai-credits` currently return
`included_remaining_usd` and `is_over_included`. **Both are removed**, along with
their `frontend/src/api/billing.ts` types.

The frontend is the only consumer and it lives in this repo, so there is no
external contract to protect. Pinning `included_remaining_usd` at `0` forever
would leave every future reader to discover for themselves that it describes a
concept the product no longer has.

`can_generate` reduces to `purchased_credits_usd > 0`, with the existing
`autoreload_failed_at` block unchanged.

## The purchase flow

New endpoint `POST /billing/buy-credits` takes a pack id, creates a Stripe
Checkout session, and returns its URL for redirect.

Checkout redirect rather than charging the saved card: it handles customers
whose card is missing or expired without a separate branch, and Stripe owns
the payment UI. The cost is that crediting happens out-of-band, which is what
the next section is about.

### Crediting is idempotent, enforced by the database

The balance is credited by two paths, deliberately:

- a new `checkout.session.completed` handler in `backend/routers/webhooks.py`
- `POST /billing/confirm-credit-purchase`, called by the return page

This mirrors the existing `confirm-upgrade` / webhook pairing. Two paths mean
a customer who reloads the return page while the webhook is in flight can be
credited **twice** for one payment.

`BillingCharge.stripe_object_id` already exists and is nullable with no
constraint. A **partial unique index** on it — unique where not null — makes
the second credit fail at the database rather than in application logic, the
same reasoning as the check-in feature's single-use constraint: a guard the
database enforces cannot be forgotten by a future caller.

This repo already has the pattern. Migration `0029` created exactly this shape
on `ownership_groups`:

```python
op.create_index(
    ..., unique=True,
    postgresql_where=sa.text("stripe_customer_id IS NOT NULL"),
)
```

Follow it rather than inventing a variant. Migration `0031`; the current head
is `0030`.

Both paths record `kind="ai_credit_purchase"` and set `stripe_object_id` to
the Checkout session id. The loser of the race catches the integrity error and
returns success — the customer's money arrived and their balance is correct;
that is not an error condition to surface.

### Packs

`$10 / $25 / $50`, defined in `backend/config.py` as `CREDIT_PACKS_USD` so
they are tunable without a deploy of new logic.

Each pack should display roughly how many AI generations it buys, because that
is the number a manager actually reasons about — not dollars of token spend.
**That figure must be derived from measured data**, by averaging `charged_usd`
per generation over recent `TokenUsage` rows, not invented. If the data is too
thin to produce an honest number at implementation time, the packs ship
without it rather than with a guess.

## The paywall

A manager with a zero balance sees a priced offer, not a failure.

- The Schedule page's "$X free remaining" indicator is replaced by the
  purchased balance and a buy-credits CTA.
- At zero balance the AI Generate control is **disabled with the CTA beside
  it**, rather than enabled and erroring on click. A disabled control with a
  reason is a price; an enabled control that 402s is a bug, and customers read
  it as one.
- The deterministic local scheduler stays fully available and unmetered. For
  a customer who buys no credits at all it is the whole product, so
  `landing.normalStrategiesNote`'s existing claim that the non-AI strategies
  are always included becomes load-bearing rather than a footnote.

`AutoReloadDisabled`, `AutoReloadError` and the `autoreload_failed_at` block
keep their current 402 behaviour. Auto-reload remains the mechanism that keeps
an *established* customer running; the paywall is about the customer who has
never bought anything.

## Scope boundary

**In scope:** the billing math, the API surface, the purchase flow, and the
**in-app** frontend — Schedule page and billing UI.

**Out of scope, in a separate PR:** the landing page. `Landing.tsx` and the 19
locale files also need the free tier and the check-in feature advertised, and
touching them once for all three changes avoids two rounds of 19-file
translation work.

**Out of scope entirely, and worth its own issue:** reconciling actual
Anthropic spend against what customers were billed, using the read-only
[Usage and Cost API](https://platform.claude.com/docs/en/manage-claude/usage-cost-api)
(`/v1/organizations/cost_report`). This is genuinely valuable and becomes more
so after this change — the $2 cushion currently absorbs small arithmetic
errors, and removing it means the markup has to be right — but it is a
separate feature and folding it in would double this one.

Also out of scope: changing `LLM_OVERAGE_MARKUP`, schedule-generation
billing, and any grandfathering. Per the issue, existing subscribers lose the
$2 with no notice and no migration.

## Testing

- **The dead branch.** Assert the partially-free branch in
  `check_and_record_usage` is unreachable at `INCLUDED_LLM_USD = 0.00`, and
  that a generation is charged `cost * LLM_OVERAGE_MARKUP` in full.
- **Boundary arithmetic** at a non-zero tier, so the branch still works if a
  grant is reinstated per environment.
- **Idempotency.** Webhook and confirm endpoint racing on one Checkout session
  credit the balance exactly once; the loser returns success, not a 500.
- **A generation landing on exactly a zero balance.**
- **402 paths** unchanged: no card, auto-reload disabled, `autoreload_failed_at`
  set.
- **Paywall state.** Zero balance disables AI generation and surfaces the CTA;
  the local scheduler stays available.
- **Removed fields** are absent from `GET /schedules/ai-credits`, and the
  frontend compiles without them.
