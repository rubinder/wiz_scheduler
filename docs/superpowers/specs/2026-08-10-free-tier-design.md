# Free Tier — Design Spec

**Status:** Approved (2026-08-10)
**Author:** Rubinder Randhawa
**Target branch:** new branch off `main`

## Goal

Invert the signup funnel from pay-to-enter to pay-to-grow. Today `/auth/register` refuses any registration without a completed Stripe Checkout session, so a prospect cannot see the product without paying. After this change, anyone can register free and run the product at small scale; payment is required to exceed **1 location or 5 employees**, to use **AI schedule generation**, or to use the **7shifts / Deputy integration importers**.

## Plan Definition

Two plans, scoped to the **ownership group** (billing has always been per-OG; limits follow it).

| | Free | Paid |
|---|---|---|
| Locations | 1 | unlimited (metered) |
| Employees | 5 | unlimited (metered) |
| Local schedule generation | ✅ | ✅ |
| AI schedule generation | ❌ | ✅ |
| 7shifts / Deputy import | ❌ | ✅ |
| CSV bulk upload | ✅ (within limits) | ✅ |
| CRUD, export, GDPR, auth | ✅ | ✅ |

Free tier costs $0 in Anthropic spend by construction — the local scheduler is pure Python. This is the reason AI is paid-only: `REGISTER_RATE_LIMIT_PER_HOUR = 3` is the only bot guard on registration, and a free tier that could reach Anthropic would let a signup farm bill us directly.

## Plan State Is Derived, Not Stored

No new column on `ownership_groups`. No backfill migration.

```
paid  ⟺  stripe_subscription_id IS NOT NULL  AND  canceled_at IS NULL
free  ⟺  otherwise
```

**Why derived.** Both fields are already maintained by machinery that exists: `/auth/register` and `confirm_reactivation` set `stripe_subscription_id`; the `customer.subscription.deleted` webhook sets `canceled_at`; `confirm_reactivation` clears it. A stored `plan` column would be a second source of truth that drifts the first time a webhook is missed, replayed out of order, or delivered while the app is mid-deploy. Derivation cannot drift.

**Grandfathering falls out for free.** `create_checkout_session` uses `mode="subscription"`, so `session.subscription` is populated for every account that has ever registered against a configured Stripe. Every existing production OG therefore derives to `paid` on the first request after deploy, with no migration and no churn risk. OGs created before Stripe was configured (seed data, local dev) derive to `free`, which is correct.

**Verify before deploy:** confirm no production OG has `stripe_subscription_id IS NULL AND canceled_at IS NULL` — that combination would silently demote a paying customer to free. Expected count is zero; if it isn't, those rows need inspection, not a blind backfill.

### Companies with no ownership group are ungated

A `Company` whose `ownership_group_id` is `NULL` is treated as **unlimited**, not free — `get_plan_state` returns `plan="paid"` and `assert_can_add` is a no-op.

This follows the convention `require_active_billing` already uses (`if not og_id: return current_user`). No production path creates an OG-less company: `/auth/register` always creates one, and the 7shifts importer goes through `_get_or_create_ownership_group`. The state exists only in seed data and `tests/conftest.py`, whose `seed_company` fixture deliberately omits it. Treating it as free would impose a 5-employee cap on the entire existing test suite and on `seed.py` demo data, for no production benefit.

## New Module: `backend/services/plan.py`

Single owner of limit arithmetic, so no caller invents its own.

```python
FREE_PLAN_MAX_LOCATIONS  # config, default 1
FREE_PLAN_MAX_EMPLOYEES  # config, default 5

class PlanState(TypedDict):
    plan: Literal["free", "paid"]
    canceled_at: datetime | None
    locations: dict   # {count, limit}   limit=None when paid
    employees: dict    # {count, limit}
    over_limit: bool
    can_generate_local: bool
    can_generate_ai: bool
    block_reason: str | None   # machine-readable code, None when unblocked

async def get_plan_state(db, ownership_group_id) -> PlanState

async def assert_can_add(db, company_id, *, locations=0, employees=0) -> None
    """Raise 402 if adding N would exceed the free plan. No-op when paid."""
```

**Concurrency.** Count-then-insert is not atomic: two managers on the same OG both sitting at 4/5 employees could both pass the check and both insert. `assert_can_add` therefore takes a row lock on the `ownership_groups` row (`SELECT … FOR UPDATE`) inside the caller's transaction before counting, serializing adds per OG. Contention is per-ownership-group and only on the free path — `assert_can_add` returns before locking when the plan is paid — so this costs paid tenants nothing.

Employee counts **reuse the existing `count_employees_for_group`** in `billing.py` rather than a parallel query — it already resolves OG → companies → employees, and two different employee counts in one codebase is a latent bug. Add the matching `count_locations_for_group` beside it, in `billing.py`, following the same shape.

### Config naming

`FREE_PLAN_MAX_EMPLOYEES = 5` is deliberately **not** named near the existing `EMPLOYEE_FREE_TIER = 1000`, which is an unrelated metered-overage threshold for paid customers. The two concepts colliding on a similar name is a genuine footgun; the `FREE_PLAN_` prefix keeps them distinct.

## Enforcement

### Write paths — capped uniformly

Every path that creates a location or employee calls `assert_can_add` before writing. Uniform and unbypassable: there is no sequence of API calls that puts a free OG over the line.

| Endpoint | Behavior when it would exceed |
|---|---|
| `POST /employees/` | `402 plan_limit_exceeded` |
| `POST /locations/` | `402 plan_limit_exceeded` |
| `POST /employees/bulk-upload` | `402` — **whole file rejected**, nothing written |
| `POST /locations/bulk-upload` | `402` — **whole file rejected**, nothing written |
| `POST /import/7shifts` | `402 integration_import_requires_paid_plan` (always, on free) |
| `POST /import/deputy` | `402 integration_import_requires_paid_plan` (always, on free) |
| `POST /import/7shifts/availabilities` | same |
| `POST /import/deputy/availabilities` | same |

**Invites are deliberately not a limit path.** `POST /employees/{id}/invite` requires the `Employee` row to already exist, and accepting an invite creates a `User` login against it; manager invites likewise create only a `User`. Neither increases the employee count — the employee was counted when it was created — so adding a check there would refuse logins to staff who are already inside the allowance.

Bulk uploads are **all-or-nothing**, not partial-fill. A CSV that would exceed the allowance is rejected in full with a message naming the limit, checked after parsing and row-count validation but before any insert. Rationale: partial-fill leaves the manager with a silently truncated roster they believe is complete, and the existing `{created, skipped, errors}` response makes that easy to overlook. An explicit refusal is louder and cannot be misread.

The integration importers are paid-only outright rather than limit-checked. They *mirror* an entire external account — create, update, and delete — and 7shifts provisions additional companies under the OG as it goes. Predicting whether a sync lands under the limit means a full dry-run pass over the external payload, and a rejected-halfway sync leaves employees referencing locations that were never created. A tenant capped at one location has no realistic use for a 7shifts mirror anyway.

**402 response shape** (consistent across all of the above):

```json
{
  "code": "plan_limit_exceeded",
  "message": "Free plan allows 5 employees. Upgrade to add more.",
  "limit": "employees",
  "max": 5,
  "current": 5,
  "attempted": 12
}
```

### Generation gate

Replaces `require_active_billing`, whose only caller is `POST /schedules/generate`.

| Plan | Over limit | Local generate | AI generate |
|---|---|---|---|
| paid | — | ✅ | ✅ |
| free | no | ✅ | `402 ai_requires_paid_plan` |
| free | yes | `402 plan_limit_exceeded` | `402 plan_limit_exceeded` |
| free, canceled | no | ✅ | `402 ai_requires_paid_plan` |
| free, canceled | yes | `402 subscription_canceled` | `402 subscription_canceled` |

Rows 4–5 implement cancel→free: a tenant who shrinks below the free limits keeps working on free instead of being frozen, while an over-limit cancellation retains today's 90-day read-only grace and its deletion lifecycle.

**Note on reachability.** Because every write path is now capped, a *free* OG can no longer climb over the limit — rows 3 and 5 are reachable only by a **downgraded** tenant (paid, grew past the limits, then canceled). The gate is narrower than it first appears but must still exist, and its tests must construct that state deliberately.

**Implementation:** a plain call at the top of `generate_schedule`, alongside the existing `check_schedule_quota` and `check_ai_credits` calls — not a FastAPI dependency. The AI-vs-local branch needs `body.use_local`, which a dependency cannot see. This matches the pattern already in that endpoint.

**Scope:** generation only. Approve, reject, export, CRUD, GDPR, and login stay open on every row, consistent with the codebase's existing stance that billing state suspends capability but never data access.

## Signup

`/auth/register`: `stripe_session_id` becomes **optional**.

- Absent → OG created with null Stripe fields → derives to free.
- Present → verified exactly as today (`payment_status == "paid"`) → derives to paid.

Rate limiting, consent recording, slug generation, and the welcome email are untouched.

**Frontend `Register.tsx` drops the billing step entirely.** The optional "start paid at signup" path is removed rather than kept: two entry states double the test matrix and the Stripe-redirect form-state restoration for no funnel benefit, since anyone wanting to pay can upgrade immediately after. The backend keeps accepting `stripe_session_id` so the paid path stays available to API clients and so the change is reversible without a backend deploy.

## Upgrade Path

Two new endpoints, modeled on the existing `reactivate-checkout` / `confirm-reactivation` pair:

- `POST /billing/upgrade-checkout` → Checkout session (`mode="subscription"`, `customer_email` since a free OG has no `stripe_customer_id`). Rejects if already paid.
- `POST /billing/confirm-upgrade` → verifies the session, writes `stripe_customer_id` + `stripe_subscription_id` onto the OG.

They cannot simply reuse the reactivate pair: `confirm_reactivation` asserts `session.customer == og.stripe_customer_id`, which is `None` for an OG that has never touched Stripe, and it clears cancellation-notification timestamps that a never-canceled OG does not have. Shared helper, separate endpoints.

- `GET /billing/plan` → returns `PlanState` for the UI.

## Frontend

The limit is checked in **two places, with different jobs**. The API check is the security boundary and is authoritative — a client-side check is trivially bypassed with curl, so it can never be the only enforcement. The web-app check exists so a free manager is stopped *before* filling in a form or picking a CSV file, rather than losing their input to a 402. Neither replaces the other: the UI check is UX, the API check is truth.

**Shared plan context.** A `usePlan` hook fetches `GET /billing/plan` once and exposes `PlanState` app-wide, refetched after any employee/location create, delete, or import so counts never go stale. Every check below reads from it.

- `Register.tsx` — billing step removed; primary CTA creates a free account.
- `api/billing.ts` — add `getPlan`, `upgradeCheckout`, `confirmUpgrade`.
- Plan/usage indicator: "3 of 5 employees · 1 of 1 location" for free OGs, with an Upgrade link. Hidden for paid.
- **Employees page** — when `employees.count >= 5` on free: the "Add employee" control is disabled with an inline explanation ("Free plan allows 5 employees — upgrade to add more") and the create form will not submit. The bulk-upload control performs a **pre-flight row count** on the selected file and refuses client-side if `count + rows > 5`, naming both numbers, so the user is not made to wait on an upload that will be rejected whole.
- **Locations page** — identical treatment at `locations.count >= 1`.
- **Integrations** — 7shifts and Deputy import actions are disabled on free with an upgrade prompt rather than being hidden, so the capability is discoverable as a paid feature.
- **Schedule page** — upgrade CTA driven by `block_reason`, so a blocked manager is told *which* limit they hit and what clears it. AI-mode toggle disabled on free with an upgrade prompt.
- **Every one of the above still handles the 402 defensively.** Counts can go stale between fetch and submit (a second manager on the same OG adds an employee concurrently), so the API rejection must render as an inline message on all of these forms, not an unhandled error.

## Testing

- Plan derivation across all four OG shapes (no Stripe / subscribed / canceled / reactivated).
- Per-OG counting spanning **multiple companies** in one group — the single most likely place for an off-by-one that leaks free capacity.
- `assert_can_add` boundary: 4→5 employees succeeds, 5→6 refused; 0→1 location succeeds, 1→2 refused.
- Bulk upload all-or-nothing: a file exceeding the allowance writes **zero** rows (assert the count is unchanged, not just that the status is 402).
- All five generation-gate rows, including deliberately constructing the downgraded over-limit state.
- Integration importers refused on free, permitted on paid.
- Register with and without `stripe_session_id`.
- Upgrade flow flips free→paid and unblocks AI generation.
- **Stale-count race:** two concurrent creates at 4/5 employees must leave exactly 5, verifying the `SELECT … FOR UPDATE` guard in `assert_can_add`.
- **Frontend:** controls disabled at the limit; pre-flight CSV row count refuses oversized files without uploading; a 402 arriving despite a passing client check still renders inline rather than throwing.

## Out of Scope

- Changing the paid plan's pricing or metered thresholds ($18/mo base, LLM/storage/employee/schedule overage) — untouched.
- Storage caps or row caps on free OGs. Capping every write path bounds a free OG to 5 employees and 1 location, so the unmetered-growth concern that motivated them no longer applies.
- A self-serve downgrade button. Downgrade happens only via Stripe Portal cancellation, as today.
