# Reservations

Table reservations for restaurants: a floor plan, booking rules, availability,
holds and bookings. Behind `RESERVATIONS_ENABLED` (default off); every route
answers 404 while it is off.

## Modules owned

- `backend/reservations/availability.py`: `find_slots` (loads one location's
  floor, rules and busy tables) and the pure `compute_slots` under it.
- `backend/reservations/booking.py`: `hold`, `confirm`, `book_staff`,
  `approve`, `decline`, `modify`, `cancel`, `set_status`, `lookup`, and the
  `ReservationError` family (each with a `.code`).
- `backend/reservations/floor.py`: `get_floor`, `save_floor`, `FloorInvalid`,
  `TableHasBookings`. `rules.py`: `RulesSchema`, `get_rules`, `save_rules`.
  `queries.py`: read-only day listings. `models.py`: the tables.
- `backend/schemas/reservations.py`; router `reservations`.

## Invariants

- `single-evaluator`: `find_slots` is the only answer to "is this table free
  at this time?". The router, the diner channels and staff booking all call
  it. The frontend never computes a slot.
- Every write takes the location row lock (`SELECT ... FOR UPDATE` on the
  location), sweeps that location's lapsed holds to `expired`, then checks
  availability and writes, so two writers for one location run one at a time.
  The exclusion constraint `ex_reservation_tables_no_overlap` on
  `reservation_tables.during` is the backstop; its violation is
  `SlotUnavailable`. Lock the location before any reservation row.
- `archive-never-delete`: dining tables are archived (`archive_dining_table`),
  never deleted. `save_floor` refuses to archive a table that has an upcoming
  active booking (`TableHasBookings`, 409).
- `location-local-time`: service periods, date overrides and the day a staff
  member browses are the location's local time. The router converts a local
  `date` to a window with the location's `ZoneInfo`; slot arithmetic is
  DST-safe.
- `fail-closed`: every held or pending row has `held_until`; a lapsed hold
  stops blocking tables. `ReservationTable.active` always equals
  `status in ACTIVE_STATUSES`.
- `thin-router`: the router authorizes (`require_manager`, company filter,
  `LocationScope.require`), calls a service and maps `ReservationError`
  subclasses to HTTP statuses, rolling back first. Services flush; the router
  commits. Staff routes pass `apply_cutoff=False`.

## Write entry points added in review

- `booking.begin_location_write(db, company_id, location_id, now)`: locks the
  location row (refusing an archived location) and sweeps lapsed holds. Every
  reservation or floor write calls it first; lock locations before
  reservations.
- `booking.seat_walk_in`: books from now and seats in one write, through the
  same evaluator (`find_slots(at=...)`), never a second one.
- `queries.count_upcoming`: what `archive_location` checks before archiving
  (409 `location_has_reservations`). `queries.effective_status`: read-only
  view that reports lapsed holds as `expired`.

## Voice entry points

- `backend/routers/voice_bridge.py`: the bridge endpoint the Lex code-hook Lambda calls.
- `backend/reservations/agent/voice.py`: voice turn handling and spoken replies.
- `backend/reservations/agent/gate.py`: shared channel helpers (subscription gate, cap, in-flight).
- The voice confirmation check is `voice.action_matches`: a "yes" books only via a `confirm_*` action matching the last reply's `expect`.
- `backend/reservations/voice_numbers.py`: per-location number claim and release.
- `backend/services/connect_numbers.py`: Amazon Connect number API wrapper.
- `infra/voice/bridge/handler.py`: the Lex V2 code-hook Lambda.
