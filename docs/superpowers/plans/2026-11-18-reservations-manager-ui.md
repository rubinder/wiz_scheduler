# Reservations Manager UI Implementation Plan (step 2 of 11)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Manager screens for reservations: a rules editor with a live slot
preview, an SVG floor-plan editor, and a host view (today's floor, the
day's bookings, staff booking, approvals), in all 19 locales, hidden
unless the feature switch is on.

**Architecture:** React pages under `frontend/src/pages/manager/` call a
new `frontend/src/api/reservations.ts` (all network access stays in
`api/`). Pure helpers in `frontend/src/utils/reservations.ts` hold the
geometry and grouping logic so they're unit-tested without a DOM. The
frontend never decides availability: slots always come from
`GET .../slots` (the server's `find_slots`). A tiny ungated backend
endpoint tells the frontend whether reservations are enabled.

**Tech Stack:** React 18, react-router 6, Tailwind, TypeScript, vitest
(`npm test`, which pins `TZ=Asia/Tokyo`), FastAPI for the one endpoint.

**Spec:** `docs/superpowers/specs/2026-11-17-reservation-agent-design.md`
(section 5 "Manager and host UI"; section 12 step 2). Backend API from
`docs/superpowers/plans/2026-11-18-reservations-core.md` Task 5.

## Global Constraints

- Network calls only in `frontend/src/api/` (`fetch-outside-api` rule in
  `frontend/src/architecture.test.ts`); use `apiFetch` from `api/client.ts`.
- No availability evaluation in the frontend (`frontend-availability-eval`
  rule): never compute bookable times client-side; show the server's slots.
- Never `new Date("YYYY-MM-DD")` (`date-only-string-parse` rule): build
  dates from parts. Times shown are the location's local time; the API
  returns ISO strings with offsets — display their wall-clock part with
  `Intl.DateTimeFormat(..., { timeZone: location.timezone })`.
- Every user-visible string goes through `useLanguage().t`, added to
  `en.ts` and translated in all 18 other locale files (they are typed
  against `en`). Add the new `reservations` section to `CHECKED_SECTIONS`
  in `src/i18n/appStringsTranslated.test.ts` in the last task.
- No new npm dependency.
- Archived tables never appear in pickers; the server already hides them.

## Review Focus

1. **Viewer in a different timezone than the restaurant** (tests run in
   `Asia/Tokyo`, the location is `America/New_York`): every time shown is
   the restaurant's wall time. Test in Task 1's helpers.
2. **A save rejected by the server** (`table_has_bookings`, `floor_invalid`
   codes, `slot_unavailable` on booking): the page shows a translated
   message and keeps the user's unsaved edits. Tested via the error-to-key
   helper in Task 1.
3. **Dragging a table off the canvas** clamps it inside; snapping is to a
   0.5-unit grid. Test in Task 3.
4. **Feature switch off**: no nav entry, and the routes show nothing
   rather than erroring. Test in Task 1 (nav filter).
5. **Empty floor** (new location): host view and editor render an empty
   state, not a crash. Test in Task 4's grouping helper (empty input).

---

## File Structure

```
backend/routers/reservations.py           # + GET /reservations/config (ungated)
tests/reservations/test_api.py            # + config test
frontend/src/api/reservations.ts          # typed client for every reservations route
frontend/src/types/reservations.ts        # FloorIn/Out, RulesSchema, Slot, Reservation types
frontend/src/utils/reservations.ts        # pure helpers (time display, errors, geometry, grouping)
frontend/src/utils/reservations.test.ts
frontend/src/pages/manager/ReservationRules.tsx
frontend/src/pages/manager/FloorPlan.tsx
frontend/src/pages/manager/HostView.tsx
frontend/src/components/layout/navItems.ts      # + group "groupReservations"
frontend/src/components/layout/navItems.test.ts
frontend/src/App.tsx                             # + 3 routes
frontend/src/i18n/*.ts                           # + nav keys, + `reservations` section
frontend/src/i18n/appStringsTranslated.test.ts   # + "reservations"
```

---

### Task 1: Client, types, helpers, navigation

**Files:**
- Modify: `backend/routers/reservations.py`, `tests/reservations/test_api.py`, `frontend/src/components/layout/navItems.ts`, `frontend/src/components/layout/navItems.test.ts`, `frontend/src/App.tsx`, all 19 `frontend/src/i18n/*.ts` (nav keys only)
- Create: `frontend/src/api/reservations.ts`, `frontend/src/types/reservations.ts`, `frontend/src/utils/reservations.ts`, `frontend/src/utils/reservations.test.ts`

**Interfaces:**
- Produces (backend): `GET /api/v1/reservations/config` → `{"enabled": bool}`, `require_manager` only (NOT behind `require_reservations_enabled`).
- Produces (frontend):
  - Types mirroring the backend schemas exactly (read `backend/schemas/reservations.py` and `backend/reservations/floor.py`/`rules.py`).
  - `api/reservations.ts`: `getConfig()`, `getFloor(locationId)`, `saveFloor(locationId, floor)`, `getRules(locationId)`, `saveRules(locationId, rules)`, `getSlots(locationId, partySize, date: string)`, `listReservations(locationId, date: string)`, `createReservation(locationId, body)`, `updateReservation(id, body)`, `setReservationStatus(id, status)`, `approveReservation(id)`, `declineReservation(id)`.
  - `utils/reservations.ts`:
    - `formatLocalTime(iso: string, timeZone: string, locale: string) -> string` (hour:minute in the restaurant's zone)
    - `localDateString(now: Date, timeZone: string) -> string` ("YYYY-MM-DD" of `now` in that zone)
    - `errorKey(detail: unknown) -> string` maps server error codes to i18n keys under `reservations.errors.*`: `slot_unavailable`, `hold_expired`, `party_too_large`, `invalid_transition`, `not_found`, `invalid_party_size`, `table_has_bookings`, `duplicate_label`, `duplicate_id`, `bad_seats`, `bad_combination`, `unknown_section`, `unknown_id`, `duplicate_section_key`; anything else → `reservations.errors.generic`.
  - `managerNav` gains a group `{ labelKey: "groupReservations", children: [hostView "/manager/reservations", floorPlan "/manager/reservations/floor", reservationRules "/manager/reservations/rules"] }` with a new NavItem flag `requiresReservations?: boolean`, and the existing visibility filter hides those items when reservations are disabled (follow how `requiresPreferenceSubmissions` is threaded).
  - Routes in `App.tsx` for the three paths (pages may be stubs returning null until Tasks 2–4).

- [ ] **Step 1: Write failing tests**
  - backend: `test_config_reports_flag_without_gating(client, manager_token)` → 200 `{"enabled": false}` with flag off, `true` with it on.
  - `utils/reservations.test.ts`:
    - `formatLocalTime("2026-12-04T19:00:00-05:00", "America/New_York", "en")` contains `7:00`, though the test process runs in Asia/Tokyo.
    - `localDateString(new Date(Date.UTC(2026, 11, 5, 3, 0)), "America/New_York") === "2026-12-04"`.
    - `errorKey({ code: "table_has_bookings" }) === "reservations.errors.table_has_bookings"`; `errorKey("boom") === "reservations.errors.generic"`.
  - `navItems.test.ts`: the reservations group is hidden when the reservations flag is false and shown when true.
- [ ] **Step 2: Run** `backend/.venv/bin/python -m pytest tests/reservations/test_api.py -q` and `cd frontend && npm test` — Expected: FAIL.
- [ ] **Step 3: Implement** per Interfaces; nav labels translated in all 19 locales.
- [ ] **Step 4: Run** both — Expected: PASS; `cd frontend && npx tsc --noEmit` clean.
- [ ] **Step 5: Commit** `feat(reservations-ui): client, helpers and navigation (#254)`

---

### Task 2: Rules editor

**Files:**
- Create: `frontend/src/pages/manager/ReservationRules.tsx`
- Modify: all 19 locale files (`reservations.rules.*`, `reservations.errors.*`, `reservations.common.*`)

**Interfaces:**
- Consumes: `getRules`, `saveRules`, `getSlots`, `errorKey`, `formatLocalTime`, location list from `api/locations.ts`.
- Page: location picker (active locations); form sections for service periods (name, weekday checkboxes, first/last seating), slot interval, duration bands (max party, minutes, churn minutes), self-book and max party sizes, restaurant capacity (max diners seated at once, blank = no cap), pacing (max covers starting per slot, blank = none), horizon days, cutoff minutes, closures (date list), date overrides (date + periods; empty = closed), hold timeout, pending hold hours, persona (Polly voice id text, greeting, facts list), staff transfer number. Save calls `saveRules`; a 422 shows the server's validation message per field when given, else a generic error, and keeps edits.
- Preview panel: party size + date → `getSlots` → list of times rendered with `formatLocalTime` in the location's timezone. Preview reflects saved rules only (label says so).

- [ ] **Step 1: Write failing test** in `utils/reservations.test.ts`: `rulesFormToPayload` and `payloadToRulesForm` round-trip a rules object, converting blank capacity/pacing inputs to `null` and "HH:MM" strings unchanged. (Add both functions to `utils/reservations.ts`.)
- [ ] **Step 2: Run** `cd frontend && npm test` — Expected: FAIL.
- [ ] **Step 3: Implement** the helpers and the page; translations in all locales.
- [ ] **Step 4: Run** `npm test` and `npx tsc --noEmit` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(reservations-ui): rules editor with slot preview (#254)`

---

### Task 3: Floor plan editor

**Files:**
- Create: `frontend/src/pages/manager/FloorPlan.tsx`
- Modify: `frontend/src/utils/reservations.ts`, `frontend/src/utils/reservations.test.ts`, all 19 locale files (`reservations.floor.*`)

**Interfaces:**
- Consumes: `getFloor`, `saveFloor`, `errorKey`.
- Produces in `utils/reservations.ts`:
  - `CANVAS = { width: 40, height: 25 }` (floor units; a 2-top is about 2×2).
  - `snap(value: number, step = 0.5) -> number`
  - `clampTable(t: {x, y, w, h}) -> {x, y}` keeps the whole table inside `CANVAS`.
  - `moveTable(t, dx, dy) -> {x, y}` = clamp(snap(x+dx), snap(y+dy)).
  - `nextLabel(existing: string[]) -> string` ("T1", "T2", … first unused).
- Page: location picker; SVG canvas (`viewBox` 0 0 40 25) with tables as rects (rounded) labelled with label and seats, rotated by `rotation`; pointer-drag to move (uses `moveTable`, converting pixel deltas through the SVG's screen CTM); click selects; side panel edits label, section, min/max seats, combinable, width, height, rotation (0/90/180/270), delete (archives on save). Sections panel (add/rename/remove, keys generated client-side). Combinations panel (name + multi-select of combinable tables). "Add table" places a 2×2 table at the first free grid spot. Save sends the whole floor; server errors show via `errorKey`, with `table_has_bookings` listing the labels; unsaved changes kept; a "you have unsaved changes" banner while dirty.

- [ ] **Step 1: Write failing tests**: `snap(1.26) === 1.5`; `clampTable({x: 39, y: -1, w: 2, h: 2})` → `{x: 38, y: 0}`; `moveTable({x: 1, y: 1, w: 2, h: 2}, 0.3, 30)` → `{x: 1.5, y: 23}`; `nextLabel(["T1", "T3"]) === "T2"`.
- [ ] **Step 2: Run** `npm test` — Expected: FAIL.
- [ ] **Step 3: Implement** helpers and page; translations.
- [ ] **Step 4: Run** `npm test`, `npx tsc --noEmit` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(reservations-ui): floor plan editor (#254)`

---

### Task 4: Host view

**Files:**
- Create: `frontend/src/pages/manager/HostView.tsx`
- Modify: `frontend/src/utils/reservations.ts`, `frontend/src/utils/reservations.test.ts`, all 19 locale files (`reservations.host.*`, status names), `frontend/src/i18n/appStringsTranslated.test.ts`

**Interfaces:**
- Consumes: `getFloor`, `listReservations`, `getSlots`, `createReservation`, `updateReservation`, `setReservationStatus`, `approveReservation`, `declineReservation`, helpers.
- Produces in `utils/reservations.ts`:
  - `groupBySeating(reservations: Reservation[], timeZone: string) -> Array<{ time: string; items: Reservation[] }>` ordered by start, `[]` for empty input; cancelled/declined/expired excluded.
  - `tableStates(tables: {label}[], reservations: Reservation[], now: Date) -> Record<string, "free" | "seated" | "upcoming">`: `seated` if a `seated` reservation includes the table; `upcoming` if a `booked` one includes it and starts within 60 minutes of `now`; else `free`. (Display only; never used to decide bookability.)
- Page: location + date pickers (date defaults to `localDateString(now, tz)`); left: floor SVG (read-only, colored by `tableStates`, refreshed every 60 s); right: bookings grouped by seating time with party, name, phone, tables, status, notes and actions (Seat, Complete, No-show, Cancel; allowed per status: booked → seat/no-show/cancel, seated → complete); "Needs approval" section for `pending_approval` with Approve/Decline; "New booking" drawer: party size + date → slot buttons from `getSlots`; then name, phone (E.164 hint), email, notes → `createReservation`; shows the confirmation code on success; `slot_unavailable` refreshes slots and shows the message.

- [ ] **Step 1: Write failing tests**: `groupBySeating([], tz)` is `[]`; two bookings at 19:00 and one at 18:30 (New York) group into `["18:30", "19:00"]` with the times in restaurant time under `TZ=Asia/Tokyo`; a cancelled one is excluded; `tableStates` gives `seated`, `upcoming` (starts in 45 min) and `free` (starts in 90 min).
- [ ] **Step 2: Run** `npm test` — Expected: FAIL.
- [ ] **Step 3: Implement** helpers, page; translations; add `"reservations"` to `CHECKED_SECTIONS`.
- [ ] **Step 4: Run** `npm test`, `npx tsc --noEmit`, `npm run build` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(reservations-ui): host view with staff booking and approvals (#254)`
