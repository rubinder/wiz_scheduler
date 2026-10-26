# Location-scoped managers (#149, #150): design questions

**Status:** draft for review. Nothing below is built yet beyond what "Current state" lists.

## Current state (main, 2026-10-25)

**Built:**
- **Data:** `users.manager_type` (`'admin' | 'regular'`, a plain string) and the `manager_locations` table with a unique `(manager_id, location_id)` constraint (migration 0042, which backfilled every existing manager to `admin`).
- **Permission helpers:** `services/manager_permissions.py`, with `get_accessible_location_ids` and `can_access_location`.
- **Endpoints:**
  - `GET /managers/me/locations`
  - `GET /managers/{id}/locations` (admin or self)
  - admin-only `POST` and `DELETE /managers/{id}/locations/{location_id}`, which refuses to remove a manager's last location
- **Frontend:** `useManagerPermissions` and `api/managers.ts` exist, but nothing imports them.
- **Fixed alongside this spec:** registration and manager-invite acceptance now create `admin` managers (they used to create NULL), migration 0049 fixes the NULL rows, and `/auth/me` returns `manager_type`.

**Not built:**
- No endpoint outside `manager_locations.py` checks location access. All ~109 manager endpoints are company-scoped only.
- No way to create a regular manager or change a manager's type.
- No `GET /managers` list.
- No Team-page UI.
- No location filtering anywhere in the frontend.

## What "regular manager" should mean: questions, with a recommended default for each

| # | Question | Recommended default |
|---|---|---|
| 1 | What does a regular manager see on company-wide pages (Company, Regions, Roles, Role equivalents, Billing and credits, Team, Payroll exports)? | **Hidden** from the sidebar, and the API refuses. Company settings, billing and team management are admin-only. |
| 2 | An employee can work at several locations. When can a regular manager see or edit one? | **See** an employee if *any* of their locations is assigned to the manager. **Edit** only if *all* of them are, so the manager can't change someone's settings for a location they don't run. |
| 3 | Can a regular manager generate schedules (this spends company AI credits), approve them, and edit approved shifts? | **Yes**, for their assigned locations only. Credits are shared at the ownership-group level, as today. |
| 4 | Can a regular manager create, archive or restore locations, or invite managers? | **No**: admin only. A new location goes to admins; an admin then assigns it. |
| 5 | How does someone become a regular manager? | The **manager invite chooses** `admin` or `regular` and, for `regular`, the initial locations. An admin can change a manager's type later with `PUT /managers/{id}`. |
| 6 | Can the last admin be demoted? | **No**: the company must always keep at least one admin. Checked under a row lock. |
| 7 | Do archived locations stay assigned? | **Yes**: restoring a location restores access. Archived locations aren't shown, and a manager whose only locations are archived sees an empty location list, not an error. |
| 8 | Does the scope apply across companies in one ownership group? | Assignments are per company. A regular manager sees only their own company's assigned locations; switching company works as today. |
| 9 | The issue mentions recalculating the billing tier when managers change. | **Drop it.** Billing doesn't depend on manager count (the plan is derived from `ownership_groups`). |
| 10 | The issue says to "filter the sidebar". | The sidebar has no location list. Instead, **every location picker** offers only accessible locations, and a page shows a "Restricted to: …" badge for regular managers. |

## Proposed build, once the answers are settled

1. **Backend scoping:** a `location_scope(current_user)` dependency that returns the accessible location ids (all of them for admins), applied router by router. Order: locations, employees, schedules (generate, drafts, approve, approved edits, exports), shift templates and special hours, availability and preferences, payroll and check-ins. Each router gets a two-manager test (an admin, and a regular manager assigned one of two locations).
2. **Manager admin:** `GET /managers` (list with type and locations); `PUT /managers/{id}` (type, plus a bulk set of locations, atomic); invite payload gains `manager_type` and `location_ids`.
3. **Frontend:**
   - permissions in the auth context (from `/auth/me`, extended with the accessible location ids), so every page updates together
   - the Team page lists managers with their type and an editable location checklist
   - location pickers filtered, the "Restricted to" badge, and admin-only pages hidden for regular managers
4. **Import tests:** the 7shifts and Deputy compatibility tests must keep passing (imports stay admin-only).

## Out of scope

- Per-location roles or permissions finer than admin/regular.
- Auditing who changed an assignment.
