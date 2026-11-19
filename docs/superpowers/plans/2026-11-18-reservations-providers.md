# Booking Providers Implementation Plan (step 9 of 11)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The agent books through a `BookingProvider` interface.
WizScheduler's own reservations are the first provider. A mock "external"
provider, with its own inventory, latency, failures and idempotency keys,
proves the seam. Each location's configuration selects which provider it
uses.

**Architecture:** `backend/reservations/providers/` holds three modules:

- `base.py`: the protocol, the value types and the provider errors.
- `wizscheduler.py`: wraps `availability.find_slots` and `booking.*`, with
  no behaviour change.
- `mock_external.py`: its own `mock_external_*` tables, behaving like a
  third-party API.

The agent's tools and core call `provider_for(conv)` and never call
`booking.*` directly. A conversation records its provider when it is
created, so a mid-conversation rules change can't switch providers under
an open hold. Ids the agent stores (`held_reservation_id`, staged
modify/cancel ids) are provider-scoped refs. The host view reads external
bookings through the provider and marks them "external".

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, React, i18n. No new
dependencies.

**Spec:** `docs/superpowers/specs/2026-11-17-reservation-agent-design.md`:
§1 goal 4, the §6 "Providers" paragraph, §12 step 9, and §14 "Mock
external provider keeps its own inventory".

## Global Constraints

- **The protocol is exactly** `find_slots`, `hold`, `book`, `modify`,
  `cancel` and `lookup`, async, each taking `(db, ctx, ...)` where `ctx`
  carries company, location, timezone, rules and now. Values are
  provider-neutral dataclasses:
  - `ProviderSlot(start, end, party_size, ref)`
  - `ProviderBooking(ref, code, start, end, party_size, status, diner_name, diner_phone, external: bool)`
- **Errors:** providers raise `ProviderError` subclasses:
  - `SlotUnavailable`
  - `NotFound`
  - `InvalidTransition`
  - `PartyTooLarge`
  - `Transient`, meaning a retryable failure; the mock's injected failures
    raise this
  The agent maps them to the same diner replies as today. `Transient` →
  "Sorry, I couldn't reach the booking system — let me try that again"
  once, then a handoff.
- **WizScheduler stays the same.** With the WizScheduler provider, every
  existing test and eval recording passes unchanged. That provider is a
  thin adapter. `find_slots` stays the single availability evaluator
  (CLAUDE.md).
- **Mock external inventory** lives in its own tables, with its own 8-char
  ids and 6-char codes. It never touches `reservations`, tables or the
  exclusion constraint.
  - Capacity is set per location (`max_covers_per_slot`, default 40) over
    15-minute slots within the location's opening periods (reusing the
    rules' periods).
  - Injected latency comes from `latency_ms` (default 0, max 3000).
  - A failure rate comes from `failure_rate` (0.0–0.5, default 0).
  - Idempotency: `book`, `modify` and `cancel` take an idempotency key
    derived from `(conversation id, action, seq)`. A replay returns the
    first result and never creates a second booking.
- **Selection:**
  - `RulesSchema.provider: Literal["wizscheduler","mock_external"] = "wizscheduler"`
    and `RulesSchema.mock_external: MockExternalSettings`.
  - Admin-only in the rules editor.
  - Switching provider never moves existing bookings.
- **Tenancy and data:** mock tables are company-keyed with RLS (copy
  0063). They are covered by diner-data export/erase (that location only),
  retention (the same window as reservations), and group deletion.
- **Host view:** external bookings appear in the day list and timeline,
  read-only, with an "External" badge. Staff actions are disabled on them.
  No table is assigned.

## Review Focus

1. **A retry of the same confirm after a transient failure must not
   double-book on the mock.** Idempotency key test in Task 2, and an
   end-to-end eval in Task 4.
2. **A location switched from WizScheduler to the mock while a
   conversation holds a WizScheduler table** finishes that conversation on
   WizScheduler. Test in Task 1/3.
3. **A diner's confirmation code from one provider isn't looked up in the
   other.** A lookup goes to the conversation's provider. A mock code at a
   WizScheduler location returns not found. Test in Task 3.
4. **Mock bookings never consume WizScheduler capacity, and vice versa.**
   `find_slots` results are unchanged by mock rows. Test in Task 2.
5. **Erasing a diner removes their mock bookings at that location only.**
   Test in Task 3.

---

### Task 1: Provider protocol and the WizScheduler adapter (refactor)

**Files:**
- `backend/reservations/providers/__init__.py` with `provider_for(conv)`
  and `provider_named(name)`
- `providers/base.py`
- `providers/wizscheduler.py`
- `backend/reservations/agent/tools.py` and `agent/core.py`: replace
  direct `booking.*` and `availability.find_slots` calls with provider
  calls
- migration `0064_booking_providers.py`: `agent_conversations.provider`
  String, not null, server default `'wizscheduler'`
- tests

**Interfaces:**
- The refs stored on conversations stay plain ids for WizScheduler, so
  existing rows keep working.
- `provider_for(conv)` reads `conv.provider`.
- New conversations take `rules.provider` (default `wizscheduler`) at
  creation, in recording.

- [ ] Tests:
  - the whole existing reservations suite and the eval replay pass
    unchanged
  - a new test asserts tools/core don't import `booking` for agent
    actions (AST check)
  - a conversation's provider is fixed at creation
- [ ] Commit `refactor(reservations): the agent books through a BookingProvider (#259)`

### Task 2: Mock external provider

**Files:**
- `providers/mock_external.py` (models in `mock_external_models.py`)
- migration `0065_mock_external_provider.py`
- tests

**Interfaces:**
- `MockExternalBooking(id, company_id, location_id, start_at, end_at, party_size, status "held"|"booked"|"cancelled", code, diner_name, diner_phone, held_until, created_at, updated_at)`
- `MockExternalIdempotency(key pk, company_id, response JSONB, created_at)`
- Slot math:
  - The slot length is the rules' duration for the party size.
  - Capacity is the sum of party sizes of held (not expired) and booked
    rows overlapping each 15-minute step, ≤ `max_covers_per_slot`.
  - The opening periods and date overrides come from the rules.
  - Doc comment: it doesn't call `find_slots`, because it is "someone
    else's system" and owns its inventory. CLAUDE.md's single-evaluator
    rule covers WizScheduler inventory only. Update CLAUDE.md's
    Reservations paragraph to say so.
- Latency is `await asyncio.sleep(latency_ms/1000)` per call. The test
  injects a clock or sleep.
- Failures use `random.Random` seeded by `(conv id, seq)` so evals are
  reproducible. A failure raises `Transient` before any write.
- Codes come from the same alphabet as WizScheduler codes, unique per
  location in the mock table.

- [ ] Tests:
  - find/hold/book/modify/cancel/lookup
  - capacity limit
  - hold expiry
  - an idempotent replay of book/modify/cancel returns the first result
    with one row
  - an injected failure writes nothing
  - mock rows don't change WizScheduler `find_slots`
  - RLS on the new tables
- [ ] Commit `feat(reservations): mock external booking provider with its own inventory (#259)`

### Task 3: Selection, host view, data coverage

**Files:**
- `rules.py`: `provider` and `MockExternalSettings`
- `routers/reservations.py`: admin-only provider field on rules PUT; host
  day listing merges external bookings
- `diner_data.py`, `data_retention.py`, `services/billing.delete_og_and_dependents`
- frontend:
  - rules editor provider select (admin) and mock settings
  - host view "External" badge, with staff actions hidden
  - i18n ×19
- tests

**Interfaces:**
- Day listing response items gain `external: bool`, `provider: str`.
  External items have no `table_ids`.
- Lookup at a location goes only to the conversation's provider.
- Diner erase deletes that location's mock bookings for the phone. Export
  includes them, labelled external.

- [ ] Tests:
  - provider switch mid-conversation (the hold stays on WizScheduler)
  - cross-provider code lookup → not found
  - host listing merge
  - non-admin can't change the provider
  - erase/export/retention/group deletion
  - frontend helper tests
  - `npm test`, `tsc`, build
- [ ] Commit `feat(reservations): provider selection, external bookings in the host view (#259)`

### Task 4: Evals on the mock provider

**Files:**
- scenarios `book_via_mock_external.json` and `mock_external_transient_retry.json`
- hand-authored recordings
- seed support for `provider: mock_external` with settings
- checker understands external bookings (end state read through the
  provider)
- tests

- [ ] Tests:
  - replay passes
  - the transient scenario retries once and ends booked with exactly one
    mock booking
  - with `failure_rate=0.5` and a seed that fails twice → handoff, no
    booking
- [ ] Commit `feat(evals): scenarios on the mock external provider (#259)`

### Task 5: Docs

- Decisions log:
  - the provider seam
  - conversation-fixed provider
  - the mock owns its inventory (the single-evaluator rule applies to
    WizScheduler inventory)
  - idempotency keys
  - Transient → retry once then handoff
  - external bookings are read-only in the host view
- Architecture reference entry points.
- CLAUDE.md Reservations paragraph: the provider seam and the
  single-evaluator scope.
- [ ] Commit `docs(reservations): booking providers (#259)`
