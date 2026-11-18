# Reservations Core Implementation Plan (step 1 of 11)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The backend of WizScheduler's per-location reservation system:
floor plans, booking rules, the single availability evaluator, holds and
bookings that cannot double-book, and the manager API, behind
`RESERVATIONS_ENABLED`.

**Architecture:** A new Reservations bounded context in
`backend/reservations/`. `availability.compute_slots` is a pure function
over primitives; `availability.find_slots` loads rows and calls it, and is
the only code that decides what is bookable. `booking.py` serializes writes
per location with a row lock on the location and re-checks `find_slots`;
a Postgres exclusion constraint on `reservation_tables` is the backstop.
Routers are thin and map domain exceptions to HTTP.

**Tech Stack:** FastAPI, SQLAlchemy 2.x (`Mapped`), PostgreSQL
(`tstzrange`, `btree_gist` exclusion constraint), Pydantic v2, Alembic,
pytest with testcontainers Postgres.

**Spec:** `docs/superpowers/specs/2026-11-17-reservation-agent-design.md`
(sections 3, 5, 12 step 1). This plan covers delivery step 1 only. Steps
2–11 each get their own plan, written against the code as it then is.

## Global Constraints

- 8-char string ids via `default=generate_short_id`; every timestamp
  `DateTime(timezone=True)`; every tenant table has `company_id`
  (`String(8)`, FK `companies.id`) so the tenant filter and RLS cover it.
- "Today"/"now" is `datetime.now(timezone.utc)`; never `date.today()`
  (`tests/test_utc_today.py` enforces it).
- Rules are in the location's local wall-clock time (#171); convert with
  `zoneinfo.ZoneInfo(location.timezone)`; stored instants are tz-aware.
- Dining tables are archived, never deleted (`archived_at`), like #126.
- Thin routers: parse, authorize (`require_manager`, `LocationScope`),
  call a service, map a domain exception to a status.
- With `RESERVATIONS_ENABLED` false every reservation route answers 404.
- Migration RLS block is copied from `0053_employee_preference_submissions.py`
  (grants + three policies per table); RLS enabled, not forced.
- No new Python dependency in this step.
- Run backend tests from the repo root with `backend/.venv/bin/python -m pytest`; Docker must be running.

## Review Focus

1. **DST days.** A seating time that doesn't exist on the spring-forward
   day is skipped; on the fall-back day each local time yields one slot
   (`fold=0`). Test in Task 3.
2. **Two people grab the last table at once.** One hold succeeds, the
   other gets `SlotUnavailable` (409), never a 500 from the constraint.
   Test in Task 4.
3. **Expired holds left in the table** must not block a new hold through
   the exclusion constraint. Test in Task 4.
4. **Archiving a table that has future bookings** answers 409
   `table_has_bookings` instead of silently orphaning them. Test in Task 2.
5. **Party sizes nothing fits** (larger than every table and combination,
   or above `max_party_size`) return no slots and a clear 422/409, and
   `party_size < 1` is a 422. Tests in Tasks 3 and 5.

---

## File Structure

```
backend/reservations/__init__.py
backend/reservations/models.py          # FloorSection, DiningTable, TableCombination,
                                        # ReservationRules, Reservation, ReservationTable
backend/reservations/rules.py           # Pydantic RulesSchema + get_rules/save_rules
backend/reservations/floor.py           # get_floor/save_floor (archive, never delete)
backend/reservations/availability.py    # Slot, compute_slots (pure), find_slots
backend/reservations/booking.py         # hold/confirm/book_staff/modify/cancel/
                                        # approve/decline/set_status/lookup + errors
backend/reservations/codes.py           # confirmation code generator
backend/schemas/reservations.py         # request/response models
backend/routers/reservations.py         # manager API
backend/alembic/versions/0057_reservations.py
backend/config.py                       # RESERVATIONS_ENABLED
backend/models/__init__.py              # import reservations.models so metadata sees them
backend/main.py                         # include router
tests/conftest.py                       # CREATE EXTENSION btree_gist; RLS for new tables
tests/reservations/test_models.py
tests/reservations/test_rules.py
tests/reservations/test_floor.py
tests/reservations/test_availability.py
tests/reservations/test_booking.py
tests/reservations/test_api.py
.claude/skills/architecture/SKILL.md    # Reservations context row
.claude/skills/architecture/references/reservations.md
CLAUDE.md                               # one Reservations paragraph under Architecture
```

---

### Task 1: Flag, models, migration

**Files:**
- Create: `backend/reservations/__init__.py`, `backend/reservations/models.py`, `backend/alembic/versions/0057_reservations.py`, `tests/reservations/__init__.py`, `tests/reservations/test_models.py`
- Modify: `backend/config.py`, `backend/models/__init__.py`, `tests/conftest.py`

**Interfaces:**
- Produces:
  - `settings.RESERVATIONS_ENABLED: bool = False`
  - Status constants in `models.py`: `HELD="held"`, `PENDING="pending_approval"`, `BOOKED="booked"`, `SEATED="seated"`, `COMPLETED="completed"`, `NO_SHOW="no_show"`, `CANCELLED="cancelled"`, `EXPIRED="expired"`, `DECLINED="declined"`; `ACTIVE_STATUSES = frozenset({HELD, PENDING, BOOKED, SEATED})`; sources `SOURCE_STAFF="staff"`, `SOURCE_WEB_AGENT="web_agent"`, `SOURCE_VOICE_AGENT="voice_agent"`.
  - `FloorSection(id, company_id, location_id, name: str, sort_order: int)`
  - `DiningTable(id, company_id, location_id, section_id: str|None (FK floor_sections, ondelete SET NULL), label: str, min_seats: int, max_seats: int, combinable: bool, x: float, y: float, w: float, h: float, rotation: float, archived_at: datetime|None)`; unique `(location_id, label)` where `archived_at IS NULL` (partial unique index).
  - `TableCombination(id, company_id, location_id, name: str, table_ids: list[str] (JSONB), min_seats: int, max_seats: int)`
  - `ReservationRules(id, company_id, location_id unique, rules: dict (JSONB), version: int, updated_at)`
  - `Reservation(id, company_id, location_id, party_size: int, start_at, end_at, status: str, held_until: datetime|None, diner_name: str|None, diner_phone: str|None (E.164), diner_email: str|None, notes: str|None, source: str, confirmation_code: str|None, provider: str = "wizscheduler", external_id: str|None, conversation_id: str|None, created_at, updated_at)`; partial unique `(location_id, confirmation_code)` where code not null; check `party_size >= 1`, `end_at > start_at`, status in the nine values.
  - `ReservationTable(id, company_id, reservation_id FK ondelete CASCADE, table_id FK dining_tables, during: TSTZRANGE, active: bool)` with `ExcludeConstraint(("table_id", "="), ("during", "&&"), where=text("active"), using="gist", name="ex_reservation_tables_no_overlap")`.

- [ ] **Step 1: Write the failing tests** in `tests/reservations/test_models.py`

```python
async def test_overlapping_active_rows_for_one_table_are_rejected(db_session, seed_location): ...
    # insert two ReservationTable rows, same table_id, during 19:00-20:30 and 20:00-21:00, active=True
    # second flush raises sqlalchemy.exc.IntegrityError whose orig mentions "ex_reservation_tables_no_overlap"
async def test_inactive_rows_may_overlap(db_session, seed_location): ...
    # same ranges, second row active=False -> flush succeeds
async def test_adjacent_ranges_do_not_overlap(db_session, seed_location): ...
    # [19:00,20:30) and [20:30,22:00) both active -> succeeds
async def test_party_size_must_be_positive(db_session, seed_location): ...
    # Reservation(party_size=0) -> IntegrityError
```

- [ ] **Step 2: Run** `backend/.venv/bin/python -m pytest tests/reservations/test_models.py -v` — Expected: FAIL (import error, no models).

- [ ] **Step 3: Implement** the flag, `models.py` as in Interfaces, import the module in `backend/models/__init__.py`, and in `tests/conftest.py` run `CREATE EXTENSION IF NOT EXISTS btree_gist` in `setup_database` before `create_all`. Migration `0057_reservations` (`down_revision = "0056"`): `CREATE EXTENSION IF NOT EXISTS btree_gist`, the six tables with constraints and indexes `(company_id, location_id, start_at)` on reservations and `(reservation_id)` on reservation_tables, then the 0053 RLS block for each of the six tables; downgrade drops the tables (not the extension).

- [ ] **Step 4: Run** the model tests, then `backend/.venv/bin/python -m pytest tests/test_migrations.py -v` — Expected: PASS (the upgrade/downgrade/upgrade and "every tenant table has RLS" tests cover the migration).

- [ ] **Step 5: Commit** `feat(reservations): models, migration and flag (#254)`

---

### Task 2: Rules schema and floor service

**Files:**
- Create: `backend/reservations/rules.py`, `backend/reservations/floor.py`, `tests/reservations/test_rules.py`, `tests/reservations/test_floor.py`

**Interfaces:**
- Consumes: Task 1 models.
- Produces:
  - `rules.ServicePeriod(BaseModel)`: `name: str`, `weekdays: list[int]` (0=Monday..6), `first_seating: time`, `last_seating: time`; validator: `first_seating <= last_seating` (no period crosses midnight), weekdays unique and in 0..6.
  - `rules.DurationBand(BaseModel)`: `max_party: int`, `minutes: int` (30..360), `churn_minutes: int` (0..120): the table's reset time after a party of this size leaves.
  - `rules.DateOverride(BaseModel)`: `date: date`, `periods: list[ServicePeriod]` (weekdays ignored), `note: str = ""`. An override replaces that date's weekly periods; an empty `periods` list closes the date.
  - `rules.Persona(BaseModel)`: `voice_id: str = "Joanna"`, `greeting: str = ""`, `facts: list[str] = []`.
  - `rules.RulesSchema(BaseModel)` with defaults: `service_periods: list[ServicePeriod] = []`, `slot_interval_minutes: int = 15` (5..60), `durations: list[DurationBand] = [(2,90,10),(4,105,15),(6,120,15),(99,150,20)]` as (max_party, minutes, churn_minutes) (validator: strictly increasing `max_party`), `max_concurrent_covers: int | None = None` (restaurant capacity: diners seated at once, across all tables), `max_covers_per_slot: int | None = None` (pacing: covers whose seating starts at the same slot time), `date_overrides: list[DateOverride] = []` (unique dates), `max_self_book_party: int = 8`, `max_party_size: int = 20` (>= `max_self_book_party`), `booking_horizon_days: int = 60` (1..365), `cutoff_minutes: int = 60` (0..1440), `closures: list[date] = []`, `section_cover_limits: dict[str, int] = {}`, `hold_timeout_minutes: int = 5` (1..30), `pending_hold_hours: int = 24` (1..72), `persona: Persona = Persona()`, `staff_transfer_number: str | None = None` (E.164 regex `^\+[1-9]\d{6,14}$`). Methods `band_for(party_size: int) -> DurationBand` (first band with `max_party >= party_size`, else the last band) and `periods_for(d: date) -> list[ServicePeriod]` (the override's periods if `d` has one, else weekly periods whose `weekdays` contain `d.weekday()`; `[]` if `d` is in `closures`).
  - `async rules.get_rules(db, company_id, location_id) -> tuple[RulesSchema, int]` (defaults and version 0 when no row).
  - `async rules.save_rules(db, company_id, location_id, rules: RulesSchema) -> int` (upsert, version + 1, returns new version; not committed).
  - `floor.FloorIn` / `floor.FloorOut` (Pydantic): `sections: [{id: str|None, key: str, name, sort_order}]`, `tables: [{id: str|None, label, section_key: str|None, min_seats, max_seats, combinable, x, y, w, h, rotation}]`, `combinations: [{id: str|None, name, table_labels: list[str]}]`. Sections are referenced by `key` (client-chosen) so new sections and tables can be saved together; combinations by table label.
  - `async floor.get_floor(db, company_id, location_id) -> FloorOut` (active tables only).
  - `async floor.save_floor(db, company_id, location_id, floor: FloorIn, now: datetime | None = None) -> FloorOut`: creates/updates by id; tables missing from the payload are archived; sections and combinations missing are deleted; combination seat range = sum of member min/max seats; raises `TableHasBookings(table_ids)` if a table to archive has an active `ReservationTable` row ending after `now`; raises `FloorInvalid(code, message)` for duplicate labels, `min_seats > max_seats`, a combination naming an unknown or non-combinable table, or fewer than 2 tables.

- [ ] **Step 1: Write the failing tests**

```python
# test_rules.py
def test_defaults_are_valid_and_duration_bands_apply():
    r = RulesSchema(); assert r.duration_for(2) == 90; assert r.duration_for(5) == 120; assert r.duration_for(500) == 150
def test_period_crossing_midnight_is_rejected(): # first_seating=22:00, last_seating=01:00 -> ValidationError
def test_max_party_size_below_self_book_is_rejected(): ...
def test_bad_transfer_number_is_rejected(): # "555-1234" -> ValidationError
async def test_save_rules_bumps_version(db_session, seed_location): # 0 -> 1 -> 2, get_rules returns the saved values
# test_floor.py
async def test_save_floor_creates_sections_tables_and_combination(db_session, seed_location):
    # 2 sections, 3 tables (T1 2-2 combinable, T2 2-4 combinable, T3 4-6), combo "T1+T2" -> seats 4..6
async def test_omitted_table_is_archived_not_deleted(db_session, seed_location):
    # save 3, save again without T3 -> T3.archived_at set, row still exists, get_floor lists 2
async def test_archiving_a_table_with_future_booking_raises(db_session, seed_location):
    # insert active ReservationTable for T3 ending tomorrow -> TableHasBookings
async def test_duplicate_label_and_bad_combination_raise_floor_invalid(db_session, seed_location): ...
```

- [ ] **Step 2: Run** `backend/.venv/bin/python -m pytest tests/reservations/test_rules.py tests/reservations/test_floor.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `rules.py` and `floor.py` per Interfaces.
- [ ] **Step 4: Run** the same command — Expected: PASS.
- [ ] **Step 5: Commit** `feat(reservations): rules schema and floor plan service (#254)`

---

### Task 3: The availability evaluator

**Files:**
- Create: `backend/reservations/availability.py`, `tests/reservations/test_availability.py`

**Interfaces:**
- Consumes: `RulesSchema`, models, `ACTIVE_STATUSES`.
- Produces:
  - `@dataclass(frozen=True) TableSpec(id: str, label: str, section_id: str | None, min_seats: int, max_seats: int)`
  - `@dataclass(frozen=True) ComboSpec(id: str, name: str, table_ids: tuple[str, ...], min_seats: int, max_seats: int)`
  - `@dataclass(frozen=True) Busy(reservation_id: str, table_id: str, start: datetime, end: datetime, party_size: int, section_id: str | None)` — `end` already includes the churn time of that party's band, so the table is unavailable until it is reset.
  - `@dataclass(frozen=True) Slot(start: datetime, end: datetime, table_ids: tuple[str, ...], section_id: str | None)` — `start`/`end` aware, in the location's zone.
  - `compute_slots(rules: RulesSchema, tz_name: str, tables: list[TableSpec], combos: list[ComboSpec], busy: list[Busy], party_size: int, window_start: datetime, window_end: datetime, now: datetime) -> list[Slot]` — pure.
  - `async find_slots(db, company_id: str, location_id: str, party_size: int, window_start: datetime, window_end: datetime, now: datetime | None = None, exclude_reservation_id: str | None = None) -> list[Slot]` — loads active tables, combinations, rules, and busy rows (`ReservationTable.active`, each extended by the churn of its reservation's party band, whose reservation status is in `ACTIVE_STATUSES` and, for `held`/`pending_approval`, `held_until > now`), excluding `exclude_reservation_id`; calls `compute_slots`.

**Algorithm `compute_slots`** (the part the signature doesn't determine):
1. Return `[]` if `party_size < 1` or `party_size > rules.max_party_size`.
2. For each local date overlapping the window, take `rules.periods_for(date)`; for each period, step from `first_seating` to `last_seating` inclusive by `slot_interval_minutes`. Build each candidate with `datetime.combine(d, t, tzinfo=ZoneInfo(tz))` (`fold=0`); skip it if converting to UTC and back changes the wall time (nonexistent on spring-forward). Deduplicate by instant.
3. Keep candidates with `window_start <= start < window_end`, `start >= now + cutoff_minutes`, and local date `<= now`'s local date + `booking_horizon_days`.
4. `band = band_for(party_size)`; dining `end = start + band.minutes`; occupied until `end + band.churn_minutes`.
5. A table is free when no `Busy` on it overlaps `[start, end + band.churn_minutes)` (each Busy already carries its own churn).
6. Choose per candidate: the free single table with `min_seats <= party_size <= max_seats` and the smallest `max_seats` (tie: label); otherwise the free combination (all members free) that fits, smallest `max_seats` (tie: name). None -> no slot at that time.
7. Restaurant capacity: if `max_concurrent_covers` is set, the peak of seated covers (distinct reservations' `party_size`, dining time only, churn excluded) at any instant in `[start, end)` plus this party must not exceed it; if `max_covers_per_slot` is set, covers of distinct reservations starting exactly at `start` plus this party must not exceed it. Failing either drops the candidate time.
8. Section cover limit: if the chosen seating's section has a limit in `section_cover_limits`, the sum of `party_size` of Busy rows in that section overlapping `[start, end)` plus this party must not exceed it, otherwise try the next candidate seating in order.
9. One `Slot` per start time, ordered by start.

- [ ] **Step 1: Write the failing tests** (pure tests build `TableSpec`/`Busy` directly; zone `America/New_York`; one DB test for `find_slots`)

```python
def test_slots_follow_period_interval_and_duration(): # period 17:00-21:00, interval 30 -> 9 slots; party 2 ends at +90 min
def test_cutoff_and_horizon_trim_the_window(): # now 16:30 local, cutoff 60 -> first slot 17:30; date beyond horizon -> []
def test_closure_date_has_no_slots(): ...
def test_churn_blocks_the_table_until_reset(): # only table busy 18:00-19:30 + churn 15 (Busy.end 19:45) -> 19:30 not offered, 19:45 offered
def test_restaurant_capacity_caps_seated_covers(): # max_concurrent_covers 10, busy parties 4+4 overlapping 19:00 on other tables -> party 4 at 19:00 refused, party 2 offered
def test_pacing_caps_covers_starting_per_slot(): # max_covers_per_slot 6, one party of 4 starts 19:00 -> party 4 at 19:00 refused, at 19:15 offered
def test_party_of_nine_uses_a_combination_when_capacity_allows(): # T3(4-6)+T4(4-6) combo 8-12 -> party 9 seated on the combo; capacity 10 with 2 seated -> refused
def test_date_override_replaces_weekly_hours_and_empty_override_closes(): # override 12:00-14:00 -> only lunch slots; override [] -> none
def test_smallest_fitting_table_is_chosen_then_combination(): # T2(2-4), T3(4-6): party 4 -> T2; T2 busy -> T3; both busy, combo T1+T2... -> combo
def test_party_above_max_party_size_gets_nothing(): ...
def test_party_size_zero_gets_nothing(): ...
def test_spring_forward_skips_nonexistent_times(): # 2027-03-14, period 01:00-03:30 interval 30 -> no 02:00 or 02:30
def test_fall_back_yields_each_local_time_once(): # 2026-11-01, period 00:30-02:30 -> 01:00 and 01:30 appear once each
def test_section_cover_limit_caps_concurrent_covers(): # limit 6, busy party 4 in section -> party 4 not offered there
async def test_find_slots_ignores_expired_holds_and_cancelled(db_session, seed_location): ...
async def test_find_slots_excludes_given_reservation(db_session, seed_location): ...
```

- [ ] **Step 2: Run** `backend/.venv/bin/python -m pytest tests/reservations/test_availability.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `availability.py` per Interfaces and Algorithm.
- [ ] **Step 4: Run** the same command — Expected: PASS.
- [ ] **Step 5: Commit** `feat(reservations): availability evaluator (#254)`

---

### Task 4: Holds and bookings

**Files:**
- Create: `backend/reservations/codes.py`, `backend/reservations/booking.py`, `tests/reservations/test_booking.py`

**Interfaces:**
- Consumes: `find_slots`, `get_rules`, models.
- Produces:
  - `codes.new_code() -> str`: 6 characters from `"ABCDEFGHJKLMNPQRSTUVWXYZ23456789"` via `secrets.choice`.
  - `@dataclass(frozen=True) DinerContact(name: str, phone: str, email: str | None = None)`; phone must match the E.164 regex from Task 2.
  - Exceptions (all subclass `ReservationError(Exception)` with `code: str`): `SlotUnavailable` ("slot_unavailable"), `HoldExpired` ("hold_expired"), `PartyTooLarge` ("party_too_large"), `InvalidTransition` ("invalid_transition"), `ReservationNotFound` ("not_found").
  - `async hold(db, company_id, location_id, party_size, start: datetime, *, source: str, conversation_id: str | None = None, now: datetime | None = None) -> Reservation` — status `held` with `held_until = now + hold_timeout_minutes`, or `pending_approval` with `held_until = now + pending_hold_hours` when `party_size > max_self_book_party`. Raises `PartyTooLarge` above `max_party_size`, `SlotUnavailable` when `find_slots` for `[start, start + 1 min)` has no slot starting exactly at `start`.
  - `async confirm(db, company_id, reservation_id, diner: DinerContact, now=None) -> Reservation` — `held` and `held_until > now` -> `booked`, sets diner fields and a unique `confirmation_code`; expired -> `HoldExpired`. A `pending_approval` row gets the diner fields and a code but stays pending.
  - `async book_staff(db, company_id, location_id, party_size, start, diner: DinerContact, notes: str | None = None, now=None) -> Reservation` — hold then confirm with `source="staff"`; staff bookings above `max_self_book_party` are `booked` directly (no approval).
  - `async approve(db, company_id, reservation_id, now=None) -> Reservation` (pending -> booked, `held_until = None`); `async decline(...)` (pending -> declined, tables inactive).
  - `async modify(db, company_id, reservation_id, *, party_size: int | None = None, start: datetime | None = None, notes: str | None = None, now=None) -> Reservation` — for `booked`/`pending_approval`; re-runs `find_slots(..., exclude_reservation_id=id)` and moves its table rows; raises `SlotUnavailable` and leaves the original untouched.
  - `async cancel(db, company_id, reservation_id, now=None) -> Reservation`.
  - `async set_status(db, company_id, reservation_id, status: str, now=None) -> Reservation` — allowed: `booked -> seated | no_show | cancelled`, `seated -> completed`. Else `InvalidTransition`.
  - `async lookup(db, company_id, location_id, code: str, phone: str) -> Reservation | None` — case-insensitive code, exact phone.
  - Every write function: takes `SELECT ... FROM locations WHERE id = :location_id FOR UPDATE`, then marks `held`/`pending_approval` rows of that location whose `held_until <= now` as `expired` with their table rows `active = False`, then does its work; keeps `ReservationTable.active` equal to `status in ACTIVE_STATUSES`; translates an `IntegrityError` on `ex_reservation_tables_no_overlap` into `SlotUnavailable`. Functions flush; callers commit.

- [ ] **Step 1: Write the failing tests**

```python
async def test_hold_then_confirm_books_with_code(db_session, seeded_floor): # status booked, len(code)==6, table row active
async def test_confirm_after_hold_timeout_raises_hold_expired(db_session, seeded_floor): # now + 6 min
async def test_large_party_goes_pending_and_holds_table(db_session, seeded_floor): # party 10 with max_self_book 8
async def test_party_above_max_raises(db_session, seeded_floor): ...
async def test_expired_hold_does_not_block_new_hold(db_session, seeded_floor): # hold the only table, advance 6 min, hold again -> succeeds, first is "expired"
async def test_concurrent_holds_for_last_table_one_wins(seeded_floor): # two sessions via get_test_session_factory, asyncio.gather -> exactly one SlotUnavailable
async def test_modify_to_unavailable_time_keeps_original(db_session, seeded_floor): ...
async def test_status_transitions(db_session, seeded_floor): # booked->seated->completed ok; completed->seated raises InvalidTransition
async def test_cancel_frees_the_table(db_session, seeded_floor): # same slot holdable again
async def test_lookup_needs_code_and_phone(db_session, seeded_floor): # right code wrong phone -> None; lowercase code -> found
```

`seeded_floor` is a fixture in `tests/reservations/conftest.py`: `seed_location` with timezone `America/New_York`, one section, tables T1 (2–2), T2 (2–4) combinable, T3 (4–6), combination T1+T2, rules with one daily period 17:00–21:00. Tests pick dates a week ahead of `now`.

- [ ] **Step 2: Run** `backend/.venv/bin/python -m pytest tests/reservations/test_booking.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `codes.py` and `booking.py` per Interfaces (retry `new_code` on the unique-index collision, up to 5 times).
- [ ] **Step 4: Run** the same command — Expected: PASS.
- [ ] **Step 5: Commit** `feat(reservations): holds and bookings with per-location lock (#254)`

---

### Task 5: Manager API, architecture docs

**Files:**
- Create: `backend/schemas/reservations.py`, `backend/routers/reservations.py`, `tests/reservations/test_api.py`, `.claude/skills/architecture/references/reservations.md`
- Modify: `backend/main.py`, `.claude/skills/architecture/SKILL.md` (contexts table), `CLAUDE.md` (Architecture section)

**Interfaces:**
- Consumes: everything above.
- Produces (all under `/api/v1/reservations`, `require_manager`, location checked with `LocationScope.require` and company ownership; 404 when `RESERVATIONS_ENABLED` is false, via a router-level dependency `require_reservations_enabled`):

| Method | Path | Body / query | Service | Success |
|---|---|---|---|---|
| GET | `/locations/{location_id}/floor` | | `get_floor` | 200 `FloorOut` |
| PUT | `/locations/{location_id}/floor` | `FloorIn` | `save_floor` | 200 `FloorOut` |
| GET | `/locations/{location_id}/rules` | | `get_rules` | 200 `{rules, version}` |
| PUT | `/locations/{location_id}/rules` | `RulesSchema` | `save_rules` | 200 `{rules, version}` |
| GET | `/locations/{location_id}/slots` | `party_size: int (>=1)`, `date: date` (local) | `find_slots` over that local day | 200 `[{start, end, table_ids}]` |
| GET | `/locations/{location_id}/reservations` | `date: date` (local) | list by local day, all statuses | 200 `[ReservationOut]` |
| POST | `/locations/{location_id}/reservations` | `{party_size, start, diner: {name, phone, email?}, notes?}` | `book_staff` | 201 `ReservationOut` |
| PATCH | `/reservations/{reservation_id}` | `{party_size?, start?, notes?}` | `modify` | 200 |
| POST | `/reservations/{reservation_id}/status` | `{status: seated\|completed\|no_show\|cancelled}` | `set_status` / `cancel` | 200 |
| POST | `/reservations/{reservation_id}/approve` | | `approve` | 200 |
| POST | `/reservations/{reservation_id}/decline` | | `decline` | 200 |

- Error mapping: `SlotUnavailable` 409, `HoldExpired` 410, `PartyTooLarge` 422, `InvalidTransition` 409, `ReservationNotFound` 404, `TableHasBookings` 409 `{"code": "table_has_bookings", "table_ids": [...]}`, `FloorInvalid` 422 `{"code", "message"}`; body shape `{"detail": {"code": ..., "message": ...}}` as other routers use.
- `ReservationOut`: id, location_id, party_size, start, end (ISO with offset), status, table labels, diner name/phone/email, notes, source, confirmation_code, held_until.

- [ ] **Step 1: Write the failing tests** in `tests/reservations/test_api.py` (use `client`, `manager_token`, `regular_token`, `monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", True)`)

```python
async def test_routes_404_when_flag_off(client, manager_token, seed_location): ...
async def test_floor_and_rules_round_trip(client, manager_token, seed_location): ...
async def test_staff_booking_then_slot_disappears(client, manager_token, seed_location): ...
async def test_double_booking_answers_409(client, manager_token, seed_location): ...
async def test_party_size_zero_is_422(client, manager_token, seed_location): ...
async def test_archiving_table_with_booking_is_409(client, manager_token, seed_location): ...
async def test_regular_manager_outside_scope_gets_404(client, regular_token, other_location): ...
async def test_other_company_location_is_404(client, manager_token, db_session): ...
async def test_status_flow_and_approve_decline(client, manager_token, seed_location): ...
```

- [ ] **Step 2: Run** `backend/.venv/bin/python -m pytest tests/reservations/test_api.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** schemas, router, registration in `main.py`; add the Reservations row to the SKILL.md contexts table (owns `backend/reservations/`, routers `reservations`; entry points `availability.find_slots`, `booking.hold`/`confirm`/`book_staff`/`modify`/`cancel`/`lookup`); write `references/reservations.md` (single evaluator, lock + exclusion constraint, archive-never-delete tables, local-time rules); add a short Reservations paragraph to CLAUDE.md's Architecture section stating `find_slots` is the only availability evaluator.
- [ ] **Step 4: Run** `backend/.venv/bin/python -m pytest tests/reservations tests/test_architecture.py tests/test_utc_today.py -v` — Expected: PASS. Then the full suite `backend/.venv/bin/python -m pytest -q` — Expected: all pass (about 10 minutes).
- [ ] **Step 5: Commit** `feat(reservations): manager API and architecture docs (#254)`

---

## Later plans (one each, written when the previous step has merged)

2. Manager UI (floor editor, rules editor, host view, 19 locales)
3. AgentCore, web channel, structured confirmation, recording, conversations view; diner data in GDPR export and 1-year retention
4. Evaluation suite and first baseline
5. Agent subscription billing and usage metering (+ CLAUDE.md billing rule change)
6. Voice on Amazon Connect (Terraform, number provisioning, bridge)
7. Call screening (number lists, FTC ingest, screening Lambda)
8. Voice evals and tuning round
9. Mock external provider
10. Analytics pipeline (`pyarrow`, Glue, Athena, dashboard)
11. Legal draft PR, production switch-on, public demo location
