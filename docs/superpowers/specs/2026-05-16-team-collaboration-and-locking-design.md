# Team Collaboration + Schedule Lock + Employee UX — Design

**Status:** Approved by user 2026-05-16. Ready for implementation planning.

**Branch:** `feat-team-and-locking`

## Goal

Three independent improvements landing together because they touch the same manager surfaces:

1. **Manager invitations** — let an existing manager invite another user to manage a Company in the same Ownership Group. Today the schema permits multiple users with `user_role='manager'` per Company, but there is no UI or endpoint for adding them.
2. **Schedule temporal lock** — prevent two managers from generating or approving schedules for the same Company simultaneously. Today both endpoints are unguarded; a second manager can stomp on an in-flight generation or approve a row that's still being mutated.
3. **Employee management UX** — a location filter on `/manager/employees`, and a split of the over-1000-LoC `EmployeeAssociation` page into two narrower pages (Availability and Association) with their own sidebar entries.

## Non-goals

- Cross-company manager permissions (managing employees/schedules in a sister Company in the same OG without re-logging in). Considered and rejected during brainstorming — current scope is invite-only.
- Manager role hierarchies / fine-grained permissions. Every user with `user_role='manager'` continues to have the same rights inside their Company.
- Redis or any new infra. Lock storage stays in Postgres.
- Persisting the location filter across page loads.
- Backfilling i18n keys into all 18 non-English locales (English placeholders only, per existing billing-i18n pattern).

---

## 1. Manager Invitations

### Data model

New table `manager_invites`:

```
id                    String(8) PK
ownership_group_id    String(8) FK → ownership_groups   NOT NULL  index
invited_by_user_id    String(8) FK → users              NOT NULL
email                 String                            NOT NULL
token                 String(64) UNIQUE                 NOT NULL  index
status                String(20) server_default 'pending'  -- pending | accepted | expired
created_at            DateTime(tz)  server_default now()
expires_at            DateTime(tz)                      NOT NULL
accepted_at           DateTime(tz)                      nullable
accepted_company_id   String(8) FK → companies          nullable
```

A new table is used rather than reusing `EmployeeInvite` because `EmployeeInvite.employee_id` is `NOT NULL` — a manager has no corresponding `Employee` row.

Invite expiry: 7 days (matches `INVITE_EXPIRE_DAYS` constant in `backend/routers/invites.py`).

### Endpoints (`backend/routers/manager_invites.py`)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/v1/manager-invites/` | `require_manager` | Create invite for an email. Inviter's Company must belong to an OG (i.e. `company.ownership_group_id IS NOT NULL`). Sends invite email via the existing Resend helper pattern in `backend/routers/invites.py:_send_invite_email`. |
| GET  | `/api/v1/manager-invites/info?token=...` | public | Returns `{ email, group_name, expired, companies: [{id, name}] }` for the accept page. |
| POST | `/api/v1/manager-invites/accept` | public | Body: `{ token, company_id, full_name, password }`. Creates `User(company_id=chosen, user_role='manager')`, marks invite `accepted` + stamps `accepted_at`/`accepted_company_id`. Returns `{ access_token, token_type }` like the existing employee-invite accept. |
| GET  | `/api/v1/manager-invites/` | `require_manager` | Lists `pending` and `accepted` invites for the inviter's OG. Read-only audit; resend is out of scope. |

`accept` validations:
- Invite exists and `status == 'pending'`.
- `expires_at > now()`. Otherwise mark `expired` and return 410.
- `company_id` is one of the Companies in the invite's OG. Otherwise 400.
- Email is not already taken within the chosen Company (existing `uq_users_email_company` unique constraint).

### Email template

Reuses the visual style of the existing employee invite email. Subject: `"You've been invited as a manager on WizScheduler"`. Body mentions the OG name and a single button "Set Up Your Account". Stub copy in `backend/services/manager_invite_email.py`; falls back to no-op when `RESEND_API_KEY` is unset, mirroring `invites.py`.

### Frontend

- **New page** `/manager/team` (sidebar entry "Team") with:
  - Table of pending + accepted invites for the OG.
  - "Invite manager" button → modal with email input. POSTs to `/manager-invites/`, then re-fetches the list.
  - i18n key: `nav.team`.
- **New page** `/accept-manager-invite?token=...` (mirror of the existing employee-accept page):
  - Calls `GET /manager-invites/info`.
  - Form: full name, password, Company picker (dropdown of `companies` from the info response).
  - Submit POSTs `/manager-invites/accept`, stashes the returned JWT, redirects to `/manager/dashboard`.

### Tests (`tests/test_manager_invites.py`)

- Create invite as manager → row exists with correct fields, email sent (Resend mocked).
- Create invite when Company has no OG → 400.
- Create invite for an email that is already a manager in another Company in the OG → still allowed (a person can be a manager of multiple Companies under separate User rows).
- Accept happy path → User created with correct `company_id` + `user_role='manager'`, invite marked accepted, JWT returned.
- Accept with expired token → 410, invite status flipped to `expired`.
- Accept with `company_id` outside the OG → 400.
- Accept with duplicate email-within-target-Company → 409.
- GET `/info` returns OG name + Companies list; doesn't leak inviter/recipient info beyond the email.

---

## 2. Schedule Temporal Lock

### Data model

New table `schedule_locks`:

```
id                  String(8) PK
company_id          String(8) FK → companies  NOT NULL  UNIQUE
locked_by_user_id   String(8) FK → users      NOT NULL
operation           String(20)                NOT NULL  -- 'generate' | 'approve'
acquired_at         DateTime(tz)  server_default now()
expires_at          DateTime(tz)              NOT NULL
```

`UNIQUE(company_id)` enforces at most one active lock row per Company. The same row is reused over time; on acquire we either insert (no row), or delete-then-insert (expired row), or fail with 409 (active row).

Lock TTL: 5 minutes. Stored as a `SCHEDULE_LOCK_TTL_SECONDS = 300` setting on `backend/config.py` so we can tune without a migration.

### Service (`backend/services/schedule_lock.py`)

```python
class LockHeld(Exception):
    def __init__(self, locked_by_full_name: str, expires_at: datetime):
        self.locked_by_full_name = locked_by_full_name
        self.expires_at = expires_at


async def acquire(
    db: AsyncSession,
    company_id: str,
    user_id: str,
    operation: str,  # 'generate' | 'approve'
) -> ScheduleLock:
    """Acquire the per-Company schedule lock or raise LockHeld.

    1. DELETE FROM schedule_locks WHERE company_id=:cid AND expires_at < now()
    2. INSERT new row with expires_at = now() + TTL
    3. On UniqueViolation, load the existing row + the holder's full_name,
       raise LockHeld(...). Caller renders 409.
    """


async def release(db: AsyncSession, lock_id: str) -> None:
    """Delete the lock row. Idempotent — missing rows are not an error."""
```

`acquire` uses an explicit `BEGIN`/`SAVEPOINT` so the `IntegrityError` from a `UniqueViolation` doesn't poison the outer session. Test coverage exercises the savepoint path.

### Router integration (`backend/routers/schedules.py`)

`POST /generate`:
- After the existing quota/credit pre-checks, before the `async def event_stream()` body, call `lock = await acquire(db, company_id, user.id, 'generate')`.
- On `LockHeld`, return `HTTPException(409, detail={"code": "schedule_locked", "locked_by": e.locked_by_full_name, "expires_at": e.expires_at.isoformat()})`.
- Wrap `event_stream` in a `try` / `finally` that calls `release(db, lock.id)` when the generator terminates (success or exception).

`POST /{schedule_id}/approve`:
- Look up the schedule's `company_id`, then `acquire(...)` at function start with `operation='approve'`.
- `release` in both the success path and exception handlers (no `finally` needed — function is short).

Edits (`PUT /{schedule_id}/shifts`) and reads (`GET /week/{date}`) are not lock-protected.

### Frontend

- **`useScheduleStream.ts`** — when the initial POST returns 409, parse the body for `locked_by` + `expires_at` and emit a `LockedError` to the caller instead of starting the stream.
- **`Schedule.tsx`** — catch `LockedError`, show a toast: `"Schedule activity in progress by {locked_by}. Try again in M:SS."` Re-render the countdown every second from `(expires_at - now)`. Hide the toast and re-enable Generate/Approve buttons when the timer hits zero.
- Same 409 handling on the Approve button in `Schedule.tsx`.
- New i18n keys: `schedule.lockedToastTitle`, `schedule.lockedToastBody` (with `{locked_by}` and `{countdown}` interpolation). English in `en.ts`; copies of the English strings in 18 other locales as placeholders.

### Tests

Backend (`tests/test_schedule_lock.py`):
- Acquire when no row exists → returns lock, row visible.
- Acquire when row exists but `expires_at < now()` → succeeds (stale row replaced).
- Acquire when row exists and not expired → raises `LockHeld` with correct `locked_by_full_name` + `expires_at`.
- `release` after expiry no-ops cleanly.
- `release` on already-deleted row no-ops cleanly.

Backend integration (`tests/test_schedules.py` additions):
- Manually insert a non-expired lock for Company X; POST `/generate` as user in Company X → 409 with correct JSON shape.
- Same for `/approve`.
- Successful `/generate` releases the lock when stream ends (assert row gone).
- Successful `/approve` releases the lock.
- Exception inside generation (mock `run_scheduling_pipeline` to raise) still releases the lock.

### Migration

`0023_manager_invites_and_schedule_locks` — adds `manager_invites` + `schedule_locks` tables. Indexes: `idx_manager_invites_og_id`, `idx_manager_invites_token` (unique), `idx_schedule_locks_company_id` (unique).

---

## 3. Employees by Location

### Frontend changes (`frontend/src/pages/manager/Employees.tsx`)

- Add `locationFilter` state, default `"all"`.
- Render a filter dropdown above the table (next to the existing Add Employee / Import buttons).
- Options: `All` + one option per `Location` already loaded into component state.
- Apply filter in the render path: `employees.filter(e => locationFilter === "all" || e.location_ids?.includes(locationFilter))`.
- Edit / Add / Bulk Upload flows are unchanged; the filter only affects which rows are rendered.

### i18n

New keys: `employees.filterLocationLabel`, `employees.filterAllLocations`. English in `en.ts`; placeholder English in the other 18 locales.

### No backend changes — page already loads all locations + employees.

### Tests

`frontend/tests/Employees.test.tsx` (Vitest + React Testing Library):
- With 3 employees and 2 locations, "All" shows all 3 rows.
- Filtering by Location A shows only the employees whose `location_ids` include A.
- Filter dropdown re-renders correctly when locations array changes.

---

## 4. Split Availability / Association into two sidebar entries

### File restructure

- **Move** `EmployeeAssociation.tsx`'s availability branch into a new `frontend/src/pages/manager/EmployeeAvailability.tsx`. Includes:
  - The availability list rendering.
  - The "Add availability" form + handlers.
  - 7shifts and Deputy availability import modals.
- **Slim down** `EmployeeAssociation.tsx` to only the affinities tab content: affinities table, level dropdowns, save/delete handlers.
- **Move shared helpers** (`formatDate`, `formatTime`, `isAllDay`, `getLevelOptions`, `getLevelLabel`, `getLevelColor`) into `frontend/src/pages/manager/_employeesShared.ts` to avoid duplication.
- Both pages drop the internal `Tab` state since each is now a standalone page.

### Routing (`frontend/src/App.tsx`)

- Keep route `/manager/employee-association` → slimmed `EmployeeAssociation.tsx` (only affinities).
- Add route `/manager/employee-availability` → new `EmployeeAvailability.tsx`.

### Sidebar (`frontend/src/components/layout/Sidebar.tsx`)

`postEmployeeManagerLinks` becomes:

```ts
const postEmployeeManagerLinks: NavItem[] = [
  { to: "/manager/employee-availability", labelKey: "employeeAvailability" },
  { to: "/manager/employee-association",  labelKey: "employeeAssociation" },
  { to: "/manager/shift-templates",        labelKey: "shiftTemplates" },
  { to: "/manager/schedule",               labelKey: "schedule" },
  { to: "/manager/export-schedules",       labelKey: "exportSchedules" },
  { to: "/manager/data-privacy",           labelKey: "dataPrivacy" },
];
```

The existing `hasEmployees` gate strips the leading link(s) until at least one employee exists. Update the slice from `.slice(1)` to `.slice(2)` so both availability + association links are hidden when no employees exist.

### i18n

New key: `nav.employeeAvailability` ("Employee Availability"). The existing `nav.employeeAssociation` is reused for the slimmed-down Association page.

### Tests

- `frontend/tests/EmployeeAvailability.test.tsx` — page renders, add-availability form submits, 7shifts import button is wired.
- `frontend/tests/EmployeeAssociation.test.tsx` — page renders only affinities content, no availability state.
- Visual regression: snapshot the sidebar in both `hasEmployees=true` and `false` states.

---

## Migration / rollout order

1. Apply migration `0023`.
2. Deploy backend: lock service + endpoints, manager-invites endpoints. The lock acquires are no-ops for existing single-manager-per-OG installs.
3. Deploy frontend: Team page, location filter, split Availability/Association pages, lock-aware toast.
4. No data backfill required.

## Open risks

- **Cross-company manager invites**: an inviter in OG-A could maliciously create users by inviting strangers. Mitigation: existing `require_manager` + the inviter's Company must belong to an OG. Considered acceptable — same trust model as the employee invite flow.
- **Lock starvation**: if one manager runs a 4-minute schedule generation, another manager waits 4+ minutes. Considered acceptable for the 5-minute TTL with `Per Company` scope per the brainstorming decision.
- **EmployeeAssociation file split** is a refactor in a 1145-LoC file. Implementation should preserve every existing handler/effect verbatim during the split and run the full frontend test suite to confirm no regression before merging.
