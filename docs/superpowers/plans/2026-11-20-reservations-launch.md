# Reservations Launch Implementation Plan (step 11 of 11)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Everything needed to launch reservations and the AI host, short of
the owner's decisions:
- a switch-on that is a one-line Terraform change;
- a demo restaurant that is seeded and ready the moment the flag flips;
- a held draft PR with the legal text, for legal review.

**Architecture:**
- **Switch:** a new Terraform variable `reservations_enabled` puts
  `RESERVATIONS_ENABLED=true` into the ECS environment by conditional concat,
  the same pattern as `analytics_enabled`.
- **Demo seed:** a separate, idempotent script, `backend/seed_reservations.py`,
  turns the existing demo tenant's first location into a realistic
  restaurant: floor plan, rules, hours and a week of fictional bookings.
- **Demo subscription:** it is comped, through a new `comped` subscription
  status that Stripe never sees.
- **Legal text:** goes on its own branch as a draft PR and is never merged
  by an agent.

**Tech Stack:** FastAPI, SQLAlchemy and Alembic, Terraform. No new
dependencies.

**Spec:** `docs/superpowers/specs/2026-11-17-reservation-agent-design.md`:
- §12 step 11: "Legal draft PR (held), then the production switch-on, then
  a public demo location seeded with a realistic restaurant".
- §11: legal text in a separate draft PR held for legal review;
  `RESERVATIONS_ENABLED` stays off in production until it is approved.

## Global Constraints

- **`RESERVATIONS_ENABLED` stays off in production.** Nothing in this plan
  turns it on. `reservations_enabled` defaults to false, and with default
  vars the Terraform plan is empty and the ECS task definition does not
  change.
- **Legal text:**
  - It lives only in `backend/legal/**`, which is a protected path. It
    changes only on the branch `docs/reservations-legal`, in a **draft** PR
    titled with "DRAFT — legal review".
  - No agent merges it, and no feature PR changes legal wording.
- **Demo data is fictional:**
  - Diner names are invented.
  - Phone numbers are from the 555-0100 to 555-0199 fiction range.
  - Emails use `@example.com`.
- **The seed never touches a non-demo tenant.** Every write uses
  `seed.COMPANY_ID` and the demo's deterministic ids. In production it
  refuses to run without `DEMO_SEED_PASSWORD`, the same guard as
  `backend/seed.py`.
- **A comped group never reaches Stripe.** No webhook, sweep, reconcile,
  invoice item or Checkout path acts on a `comped` subscription. Usage is
  still recorded, as cost, but never invoiced.
- **"Today" is UTC** (CLAUDE.md). The demo bookings' local times derive
  from the location's time zone.

## Review Focus

1. **Running `python -m backend.seed_reservations` twice** leaves exactly
   the same rows. Tested in Task 2.
2. **The reconcile job and the overage sweep, run against a comped group,**
   call Stripe zero times and change nothing. Tested in Task 2.
3. **A public diner on the demo location** after the daily cap gets the
   busy line and costs nothing more. The demo cap is $3, set in its rules.
   Tested in Task 2.
4. **Group deletion and the existing demo prune** still work with the
   reservation rows present, and `delete_og_and_dependents` handles the
   comped subscription. Tested in Task 2.
5. **With `reservations_enabled=false`** the environment list is identical
   to today's. Tested in Task 1.

---

### Task 1: Switch-on wiring (plan-neutral)

**Files:**
- `terraform/variables.tf`: add `reservations_enabled` (bool, default
  false), with a description saying it is flipped only after legal
  approval and together with `agent_reconcile_enabled`.
- `terraform/ecs.tf`: `var.reservations_enabled ? [{ name = "RESERVATIONS_ENABLED", value = "true" }] : []`
  inside the existing `concat`.
- `tests/infra/`: extend the gating test that covers `analytics_enabled`
  and `VOICE_PROVISIONING_ENABLED`.
- `docs/superpowers/decisions/reservations-decisions.md`: add a "Switch-on
  order" subsection at the top of the owner checklist:
  1. Legal approves the draft PR, and the owner merges it.
  2. Do Stripe, voice and screening per the existing checklist sections.
  3. Set `reservations_enabled`, `agent_reconcile_enabled` and, if wanted,
     `voice_provisioning_enabled` and `analytics_enabled` to true in one
     PR. CI applies it.
  4. Run the demo seed as a one-off ECS task (Task 2's command).
  5. Smoke test: open `/r/<demo slug>/<demo location>`, book, then
     cancel.

- [ ] Write the gating test: with default vars, `RESERVATIONS_ENABLED`
  does not appear in the ECS environment. Assert the variable's default
  is false.
- [ ] Implement, then run `terraform fmt -check -recursive`, then
  `validate` with the flag off and on (in a scratch copy, as earlier
  steps did).
- [ ] Run `pytest tests/infra -q`.
- [ ] Commit `feat(terraform): reservations_enabled switch, off by default (#259)`.

### Task 2: Demo restaurant and comped AI host

**Files:**
- `backend/reservations/subscription_models.py` and
  `backend/reservations/subscription.py`: add `"comped"` to `STATUSES`
  and to `ANSWERING_STATUSES`, but **not** to the overage or invoice
  status set.
- Migration `0067_reservation_subscription_comped.py`: extend
  `ck_reservation_subscriptions_status`. Check the head first. The
  analytics branch adds 0066, so if 0066 is not on main yet, chain from
  the current head and say so in the report.
- Guard every Stripe path in `services/billing_reservations.py` against
  `comped`: webhook apply, sweep, reconcile, final invoice, Checkout and
  portal. The manager billing page shows "Included" instead of
  subscribe/cancel. Add that string in all 19 locales.
- `backend/seed_reservations.py` (`python -m backend.seed_reservations`).
  Every id is deterministic, with an `rsv…` prefix scheme modelled on
  `seed.py`. It seeds onto `seed.LOCATION_ID`:
  - a location display name that suits a restaurant, kept if one is
    already set;
  - a floor plan of 14 tables, from 2-tops to one 8-top, plus one
    combinable pair;
  - rules with lunch and dinner periods, pacing, a churn of 15 minutes,
    a special-hours closure on the next public holiday,
    `daily_spend_cap_usd = 3` and `provider = "wizscheduler"`;
  - about 40 fictional reservations across the next 7 local days, in
    mixed statuses;
  - a `ReservationSubscription` with status `comped` for the demo group,
    with no Stripe ids.
- `backend/seed.py`: the prune and `delete_og_and_dependents` must cover
  these rows. Reuse the existing deletes, and add any that are missing.
- `tests/reservations/test_seed_reservations.py`
- `tests/reservations/test_comped_subscription.py`

**Interfaces:**
- `async def seed_reservations(db: AsyncSession) -> dict[str, int]`
  returns the counts it upserted. `main()` wraps it with the production
  password guard and commits.

- [ ] Write tests:
  - Running the seed twice gives identical row counts and ids.
  - The seed refuses when `ENV=production` and `DEMO_SEED_PASSWORD` is
    empty.
  - Every diner phone number matches `555-01\d\d`, and every email ends
    in `@example.com`.
  - `find_slots` returns slots for tomorrow's dinner at the demo
    location.
  - A comped group is answered (`active_for_location` is not None).
  - The overage sweep, reconcile and webhook apply leave a comped row
    unchanged. Use a Stripe client stub that fails the test if it is
    called.
  - At the $3 cap, a web turn gets the busy text.
  - `delete_og_and_dependents` removes the seeded rows and the comped
    subscription.
- [ ] Implement. Run the new tests, then `tests/reservations`, the
  billing tests, `tests/test_architecture.py`, and the frontend
  `npm test`, `tsc` and build.
- [ ] Commit `feat(reservations): demo restaurant seed and a comped AI host subscription (#259)`.

### Task 3: Legal draft (separate branch, draft PR, never merged by an agent)

Use a separate worktree off `origin/main`, on branch
`docs/reservations-legal`.

**Files:**
- `backend/legal/privacy-policy.txt`: a new section for diners of a
  restaurant that uses the AI host, covering:
  - What is collected: name, phone, optional email, party size and
    times, chat and call transcripts, the caller number for screening.
  - Why: making and managing the booking; screening spam calls against
    the restaurant's lists and the FTC reported-calls list.
  - Retention: 1 year.
  - Erasure: by confirmation code or phone number through the restaurant.
  - Who the controller is: the restaurant.
  - AI processing: by Anthropic.
  - Voice: through AWS (Connect, Lex, Polly), with PII redaction in
    Contact Lens and Lex logs. No call recordings are kept.
  - Analytics: in an anonymous lake.
- `backend/legal/dpa-processors.json` and `backend/legal/dpa.txt`: add
  Amazon Web Services (Connect, Lex, Polly, Lambda, S3, Glue and Athena)
  for diner voice and analytics, with region us-east-1. Extend Anthropic's
  purpose to diner conversations. Add Stripe for the AI host subscription
  if it is not listed already.
- `backend/legal/terms.txt`: an AI host subscription section:
  - $10 per month, $5 of usage included, then usage at cost × 1.5,
    covering model, speech and call minutes;
  - the restaurant's responsibility for lawful caller notice where the
    law requires it;
  - the per-location daily spend cap;
  - numbers released 7 days after cancellation.
- Update every "Last Updated" date to the PR date.

Copy every fact from code, not from memory: rates, retention days, caps
and the redaction settings. Cite the setting names in the PR body so
legal can check them.

- [ ] If a test pins the processor list (grep tests for
  `dpa-processors`), update it and run it.
- [ ] Commit `docs(legal): DRAFT privacy, DPA and terms for reservations and the AI host (#259)`.
- [ ] Push, then open the PR **as a draft** (`gh pr create --draft`),
  titled `DRAFT — legal review: reservations and AI host privacy, DPA and terms (#259)`.
  The body lists each change and the code fact behind it, and states
  "Do not merge until legal approves; RESERVATIONS_ENABLED stays off
  until then."

### Task 4: Docs

- Add decisions from 81 (on main, the next free number after the analytics
  decisions 71 to 80):
  - the switch variable;
  - the comped status, and why it is not a coupon: the demo group has no
    Stripe customer;
  - the demo's $3 cap;
  - fictional data rules;
  - legal held as a draft.
- Update the owner checklist to point at the switch-on order (Task 1) and
  the legal draft PR number.
- [ ] Commit `docs(reservations): launch decisions and switch-on order (#259)`.

### Task 5: Pre-launch privacy gaps the legal draft surfaced

These are added after the legal draft (PR #284) listed open questions that
are product gaps, not wording.

- **Erasure by confirmation code.** Spec §11 says diner data is erasable
  per reservation "by confirmation code or phone number" from the manager
  UI. Only erasure by phone number exists today. Add erasure by code:
  - It uses the same service and the same scrub as phone erasure.
  - It is scope-checked to the manager's locations.
  - The manager UI gets an input for it, with copy in all 19 locales.
  - Tests:
    - erasure by code scrubs that reservation and its conversation;
    - an unknown code gives 404;
    - a code at another company's location gives 404.
- **Automated-assistant disclosure.**
  - The default voice greeting and the default web chat greeting both say
    the host is an automated assistant. Example: "Hi, you've reached
    {restaurant}'s automated reservations assistant."
  - The restaurant's rules may change the wording, but the rules schema
    rejects a custom greeting that doesn't contain "automated" or
    "assistant" (case-insensitive).
  - Tests: cover both defaults and the validation.
- **Connect flow logs.** Caller numbers appear unredacted in the Connect
  contact flow logs, which are kept 30 days. That breaks the "phone
  numbers are never logged" rule.
  - Reduce the flow log level so customer attributes and caller numbers
    aren't logged, if the provider supports it. Otherwise cut the log
    group's retention to 1 day and document why.
  - This is a Terraform change, so the controller applies it. The
    implementer writes the change as a patch file in the scratchpad.
  - Extend the gating test so the default plan stays unchanged.
- [ ] Commit `fix(reservations): erase by confirmation code, disclose the automated host, keep caller numbers out of flow logs (#259)`.
