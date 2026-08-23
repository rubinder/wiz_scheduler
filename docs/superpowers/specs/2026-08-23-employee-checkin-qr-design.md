# Employee Check-In with Rotating QR — Design Spec

Issue: [#63](https://github.com/rubinder/wiz_scheduler/issues/63)
Status: approved, not yet implemented

## Goal

An employee arriving at work scans a QR code displayed at their location and
is checked in against the shift they were scheduled for. Managers get a
punctuality history over the retained window. Paid plans only.

The QR code changes on every check-in, so a photograph of it is worthless the
moment the person behind you scans. It is derived through an HMAC keyed by a
server-side secret, so it cannot be recomputed off-site either.

## Threat Model

The original issue proposed deriving the code from company slug, location,
date, and the count of employees already checked in. Every one of those is
known or guessable to someone who is not on site — the count is a small
integer an attacker can simply enumerate — so the code would rotate without
being unforgeable.

The payload is therefore an **HMAC digest** over exactly those four inputs,
keyed by a secret only the server holds. The rotation behaviour the issue
asked for is unchanged; the derivation is no longer reproducible without the
key.

| Attack | Closed by |
|---|---|
| Recompute the code from home | HMAC — the four inputs are public, the key is not |
| Photograph the code, use it later | Single use — the counter moves on the first scan |
| Photograph the code, share it with a colleague off site | Single use — whoever scans second fails |
| Forward the code off site in real time | **Not closed.** One off-site person can burn one code |

The residual real-time forwarding risk is accepted. It is bounded to a single
check-in, and it is *loud*: the code advances, so the employee genuinely
standing at the screen has their scan rejected and has to re-scan. A silent
compromise would be a different matter.

Geofencing and a short TTL were both considered and rejected as out of scope
for this iteration. Neither is precluded by this design.

## Data Model

One new table, `employee_check_ins`, in Alembic migration `0030`.

| Column | Type | Notes |
|---|---|---|
| `id` | `String(8)` PK | `generate_short_id`, per repo convention |
| `company_id` | `String(8)` FK → `companies.id` | Every query filters on it |
| `location_id` | `String(8)` FK → `locations.id` | The location whose code was scanned |
| `employee_id` | `String(8)` FK → `employees.id` | From the caller's JWT, never from the code |
| `shift_id` | `String(8)` FK → `shifts.id`, **nullable** | Null for `no_shift` and `wrong_location` |
| `checked_in_at` | `DateTime(timezone=True)` | Offset derived from `location.timezone` |
| `local_date` | `Date` | The location-local date; see below |
| `counter` | `Integer` | The rotation value this scan consumed |
| `status` | `String` | `matched` / `no_shift` / `wrong_location` / `duplicate` |
| `minutes_from_start` | `Integer`, nullable | Signed; negative early, positive late |
| `created_at` | `DateTime(timezone=True)` | `server_default=now()` |

**Unique constraint on `(location_id, local_date, counter)`.**

This is what makes single-use real, and it is deliberately enforced by the
database rather than by application logic. Two employees scanning the same
displayed code both present counter *N*; the first insert wins and the second
violates the constraint. Without it, two concurrent requests would both read
`COUNT(*) == N`, both validate, and both record — the check-in equivalent of
the race `assert_can_add` takes a row lock to avoid. A unique constraint is
cheaper than a lock and cannot be forgotten by a future caller.

Indexes: `(company_id, location_id, local_date)` for the QR counter lookup and
`(company_id, employee_id, local_date)` for the report.

### `local_date` is not redundant with `checked_in_at`

"First scan of the day" and "the day's rotation counter" are both wall-clock
questions at the location, and a location may sit in any timezone. Deriving
the local date on every read means every consumer has to remember to do it,
and one that forgets is wrong only for locations west of UTC after 19:00 —
precisely the kind of bug that survives a test suite run in UTC. It is stored
once, at write time, by the one code path that has the location in hand.

### `minutes_from_start` is denormalised on purpose

The report reads over six months. Shifts inside that window can be edited,
regenerated, or purged by the existing retention sweeps, any of which would
silently rewrite history if punctuality were recomputed by joining to `shifts`
at read time. The delta is a fact about what happened; it is recorded when it
happens.

### No counter table

The rotation counter *is* `COUNT(*)` of check-ins for that location and local
date, which is exactly the input the issue specified. A separate counter row
would be a second source of truth that can drift from the rows it counts.

## QR Generation and Validation

```
token = base64url(HMAC_SHA256(CHECKIN_QR_SECRET,
                              f"{company_slug}|{location_id}|{local_date}|{counter}"))
```

New setting `CHECKIN_QR_SECRET`, sourced from AWS Secrets Manager in
production following the `DEMO_SEED_PASSWORD` pattern. The service refuses to
serve check-in endpoints if it is unset in production, rather than falling
back to a default — a predictable key is the same as no key.

**`GET /api/v1/check-ins/qr?location_id=`** (manager, paid) returns
`{counter, svg}`. The SVG is rendered server-side by `segno`. The secret, the
input string, and the digest derivation never leave the backend.

**`POST /api/v1/check-ins`** with `{token}` and an employee bearer token. The
server recomputes the expected token for the current counter and compares with
`hmac.compare_digest` — not `==`, which leaks position through timing.

Rotation needs no explicit invalidation step: recording a check-in increments
`COUNT(*)`, so the expected token changes and the spent one stops validating.

### Consequence: the shift-change queue

Two people scanning the same displayed code means the second fails. This is
inherent to "the code moves only when scanned", not a defect, but it is
user-visible at a shift change when several people arrive together. The
employee page must therefore distinguish *this* failure from the others and
say "that code was already used — scan the new one", rather than returning a
generic error that reads as "check-in is broken".

## Flows

### Employee

The QR encodes `{FRONTEND_URL}/employee/check-in?t=<token>`. The employee
points their ordinary phone camera at it and the link opens the app; the page
POSTs the token with the bearer it already holds. If they are not
authenticated they hit the existing login redirect and return to the check-in
URL.

Identity comes from the JWT. The code itself is anonymous — it says *where and
when*, never *who* — which is why the same image can be displayed to everyone.

No in-app camera scanner, so no QR *reader* dependency, no camera permission
prompt, and no fallback path for a denied permission.

### Shift matching

Among that employee's shifts at that location whose `start_time` falls within
`CHECKIN_MATCH_WINDOW_HOURS` (default 6) of the scan, take the nearest.

Matching on the timestamp rather than the calendar date means shifts crossing
midnight need no special case at all: a 22:00–06:00 shift scanned at 21:55
matches on time, and the calendar dates either side of midnight never enter
the query. The issue lists midnight-crossing shifts as a non-happy path; under
this rule they are simply not a special case.

| Situation | Status | `shift_id` | `minutes_from_start` |
|---|---|---|---|
| Shift found in window | `matched` | set | set, signed |
| No shift in window, none elsewhere that day | `no_shift` | null | null |
| No shift here, but one at another location | `wrong_location` | null | null |
| Already checked in today | `duplicate` | set if a shift matched, else null | set if a shift matched, else null |

Every accepted scan is recorded. An employee who turns up unscheduled is
information a manager wants; refusing the scan would discard it. The report
filters to `matched` by default.

First scan of the day wins. Later scans are stored as `duplicate` so the
history stays complete, but they do not overwrite the day's punctuality
number — otherwise a re-scan at 17:00 would make an on-time arrival look
nine hours late. `duplicate` replaces `matched` rather than sitting alongside
it, so filtering the report to `matched` excludes re-scans by construction.

A `duplicate` still consumes a counter and still rotates the code, because it
is a recorded check-in and the issue's rule is that the code moves on every
recorded scan. This is deliberate: the alternative — validating a scan but
declining to rotate — would leave a code that has demonstrably been scanned
still live on the screen.

### Manager

Two new sidebar tabs, both manager-only and paid-only.

**Check-In QR** — a location selector, the QR at display size, and today's
check-in count. Polls `GET /check-ins/qr` every 3 seconds and swaps the image
when `counter` moves. Polling rather than an NDJSON stream: `useScheduleStream`
is built around a request with a known end, whereas this is an open-ended
subscription that would hold a worker open per manager screen all day and need
its own reconnect handling. Up to 3 seconds of staleness costs nothing here,
because the stale code is already spent and correctly fails.

**Check-In Report** — the retained window, filterable by employee, with a
Recharts scatter of `minutes_from_start` against date and a zero reference
line: below the line is early, above is late. A table beneath carries the
underlying rows.

## Configuration

```python
CHECKIN_QR_SECRET: str = ""              # Secrets Manager in prod; refuse if unset
CHECKIN_MATCH_WINDOW_HOURS: int = 6      # how far from a shift start a scan can land
RETENTION_CHECKINS_DAYS: int = 180       # ~6 months, per the issue
```

`RETENTION_CHECKINS_DAYS` is swept by `run_data_retention` alongside the six
existing sweeps, so check-ins do not grow unbounded.

## Gating

`assert_paid_plan(db, company_id, "check_in")` on every check-in endpoint,
**including the employee POST**. What gates the feature is the tenant's plan,
not the caller's role: a free tenant's employee must not be able to check in
just because the endpoint is employee-facing.

## Testing

- HMAC: a valid token accepts; a token for the wrong counter, wrong location,
  wrong date, or signed with a different key rejects.
- Replay: a token that has already been recorded rejects on re-POST.
- Concurrency: two inserts at the same counter — one succeeds, one hits the
  unique constraint and returns the "already used" response, not a 500.
- Matching: nearest shift within the window; a 22:00–06:00 shift scanned at
  21:55 matches; a scan 8 hours out is `no_shift`.
- Each of the four statuses, and first-scan-wins under `duplicate`.
- Timezone: `local_date`, `checked_in_at` and `minutes_from_start` under a
  non-UTC location. This is the trap that produced the availability bug fixed
  in #77 — a suite that only runs UTC locations proves nothing here.
- Retention sweep removes rows past the cutoff and nothing inside it.
- Paid gating on both the manager and the employee endpoints.

## Dependencies

Both are explicitly authorised; CLAUDE.md otherwise forbids adding them.

- `segno` (backend) — pure-Python QR encoder, no transitive dependencies,
  renders SVG directly.
- `recharts` (frontend) — the report graph.

QR encoding is not hand-rolled. Reed–Solomon error correction and mask
selection are subtle, and a bug produces codes that scan wrong or not at all.

## Out of Scope

- Check-out and hours-worked tracking. The nullable `shift_id` and the status
  column leave room for it; nothing here forecloses it.
- Payroll export.
- Geofencing and any location second factor.
- A short TTL on codes.
