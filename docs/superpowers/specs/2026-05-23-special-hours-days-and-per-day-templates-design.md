# Special Hours Days + Per-Day Template Overrides — Design

**Status:** Approved by user 2026-05-23. Ready for implementation planning.

**Branch:** `feat-special-hours-days`

## Goal

Let managers configure days with non-standard operating hours (holidays, partial-day closures, special events) at the per-location, per-date granularity, and have the scheduler use a day-specific ShiftTemplate for those days instead of the regular weekly recurring one.

End-to-end:
1. Manager opens a new **Special Hours** tab in the sidebar.
2. Clicks **+ Add special hours** → modal: Date, Location, Open, Close, Label, **Starting draft template** dropdown.
3. On save, the system clones the chosen recurring `ShiftTemplate` into a one-day variant with the special open/close times applied, then links it to the new `SpecialHoursDay` row.
4. When the manager later generates a schedule for a week containing that date, the scheduler picks the day-specific template for that one day and the regular recurring template for the others.
5. The Schedule page surfaces a badge on the affected day so the manager sees the override is in effect.

## Non-goals (deferred to a follow-on PR)

- **Google Business Profile API integration.** OAuth, managed-access application, hours import, and showing officially-posted operating hours from Google. Deferred to a second PR. This PR ships the scheduling value with all-manual entry; Google adds polish on top.
- Recurring special-hours patterns (e.g. "every Sunday during Lent"). One-shot per-date only.
- Multi-day special-hours spans as a single row. Manager creates one entry per date (the Duplicate UX makes that cheap across locations; not across dates).
- Migrating any existing in-the-wild "exception day" data (there is none — this is greenfield).

---

## 1. Data model

### 1.1 Modify `shift_templates` (existing table)

Add one nullable column:

```
specific_date  Date  nullable
```

- `NULL` = recurring weekly template. Existing behavior. Every existing row remains `NULL`.
- Set = one-day override applying only to that calendar date.
- Partial index `ix_shift_templates_location_specific_date` on `(location_id, specific_date)` `WHERE specific_date IS NOT NULL` — keeps per-day lookups cheap and lets the recurring rows stay out of the index.

ORM: `specific_date: Mapped[date | None] = mapped_column(Date, nullable=True)`.

### 1.2 New table `special_hours_days`

```
id                  String(8) PK
company_id          String(8) FK → companies        NOT NULL  index
location_id         String(8) FK → locations        NOT NULL  index
date                Date                            NOT NULL
open_time           Time                            NOT NULL
close_time          Time                            NOT NULL
label               String  nullable
shift_template_id   String(8) FK → shift_templates  nullable
created_at          DateTime(tz)  server_default now()

UNIQUE (location_id, date)  name = uq_special_hours_days_location_date
```

- `shift_template_id` is the day-specific template cloned at entry time. Nullable so the row can exist between insert and template-create within a transaction; in practice always set after a successful save.
- `UNIQUE (location_id, date)` prevents two special-hours entries on the same date at the same location.

### 1.3 Migration `0025_add_special_hours_days_and_shift_template_specific_date`

- Adds the `shift_templates.specific_date` column + partial index.
- Creates the `special_hours_days` table.
- `down_revision = "0024"` (the just-merged forgot-password migration).

---

## 2. Entry flow + template cloning

### 2.1 `POST /api/v1/special-hours/`

Request body (Pydantic schema `CreateSpecialHoursDayRequest`):

```json
{
  "location_id": "<wiz_location_id>",
  "date": "2026-12-24",
  "open_time": "09:00",
  "close_time": "14:00",
  "label": "Christmas Eve",
  "draft_template_id": "<existing_shift_template_id>"
}
```

`draft_template_id` is **optional** to support the bulk-duplicate path. When omitted, the server picks the location's recurring template (the one with `specific_date IS NULL`). If the location has zero recurring templates the server returns 400 with `detail.code = "no_recurring_template"` so the frontend can surface a clean error.

### 2.2 Server flow (single transaction)

1. Verify the manager's Company owns the `location_id`.
2. Reject if a `SpecialHoursDay` already exists for `(location_id, date)` (uniqueness pre-check + 409 with `detail.code = "duplicate"`).
3. Load the draft `ShiftTemplate`:
   - If `draft_template_id` provided: load by id and verify it belongs to the same Company and the same Location, and that its `specific_date IS NULL`.
   - Otherwise: SELECT the location's recurring template (one row where `location_id=:loc AND specific_date IS NULL`). If zero rows → 400 `no_recurring_template`.
4. Deep-copy the draft's `weekly_schedule` JSON.
5. Find the matching day-of-week entry in the source (Python's `date.weekday()`, where 0 = Monday). Extract only that day's entry. If the source has no matching day-of-week, fall back to the first non-empty day (manager can edit later — annotated in the cloned template's `name`).
6. Override `start_time` and `end_time` on every role entry in that day with the request's `open_time` / `close_time` (rendered as `HH:MM:SS` strings, location's local intent).
7. Insert a new `ShiftTemplate`:
   - `name = f"{source.name} — {label or date.isoformat()}"`
   - `company_id = source.company_id`
   - `location_id = source.location_id`
   - `weekly_schedule = [{ day_of_week: <dow>, roles: [...adapted...] }]`  (single-day array)
   - `specific_date = body.date`
8. Insert `SpecialHoursDay` with `shift_template_id = <new_template.id>`.
9. `await db.commit()`. Return the `SpecialHoursDay` row with `shift_template` embedded.

### 2.3 `PUT /api/v1/special-hours/{id}`

Updates `label / open_time / close_time / date` on the SpecialHoursDay. If `open_time` or `close_time` changed, also propagate them to the linked template's `weekly_schedule[0].roles[*].start_time / end_time`. Does **not** re-clone the template — manager edits to the cloned template are preserved across SpecialHoursDay updates. If `date` changed, also updates the linked template's `specific_date`.

### 2.4 `DELETE /api/v1/special-hours/{id}`

Deletes the `SpecialHoursDay` row and the linked `ShiftTemplate` row (the cloned template is one-shot — no other code path references it once the date passes). Returns 204.

### 2.5 `GET /api/v1/special-hours/`

Lists entries for the manager's Company. Query params:
- `location_id` — filter to one location.
- `from_date` / `to_date` — inclusive date window.

Default ordering: `date ASC, location.name ASC`.

---

## 3. Scheduling algorithm changes

### 3.1 New helper `backend/scheduling/template_resolver.py`

```python
async def resolve_templates_for_week(
    db: AsyncSession,
    *,
    location_id: str,
    week_dates: list[date],          # ordered, all dates in the target window
    selected_template_ids: list[str] | None,
) -> dict[date, ShiftTemplate]:
    """For each date in week_dates, return the ShiftTemplate the scheduler
    should use for that date.

    Precedence per date:
      1. ShiftTemplate where location_id=:loc AND specific_date=:date
      2. The recurring template the manager selected via
         selected_template_ids (filtered to this location's templates with
         specific_date IS NULL).
      3. If no selected_template_ids passed: the single recurring template
         for the location, when exactly one exists.

    Raises LocationMissingTemplate when no resolution exists (the location
    has zero recurring templates AND no override on that date).
    """
```

### 3.2 Wire-up in `backend/scheduling/nodes.py::load_location_context`

Change `state.shift_templates[location_id]` from a single `ShiftTemplate` to a `dict[date, ShiftTemplate]` produced by `resolve_templates_for_week`.

Downstream nodes (`build_prompt`, the local scheduler) iterate dates and pull the right template per date.

### 3.3 Prompt builder

`backend/scheduling/prompts.py::_format_role_requirements` currently iterates `weekly_schedule[]` by `day_of_week`. Change to iterate dates in the scheduling window, look up `templates[date]`, render that template's single-day-or-matching-day entry. The prompt the LLM sees lists each calendar date with its actual rules, including overrides.

### 3.4 Local scheduler

`backend/scheduling/local_scheduler.py` today indexes `template.weekly_schedule[dow]` per day. Change to look up `templates[date]`:
- If the resolved template has `specific_date` set, its `weekly_schedule` has length 1 — use `weekly_schedule[0].roles`.
- If it's the recurring template, look up by `dow` as today.

A small helper `_roles_for_date(template, date)` keeps the day-of-week-vs-specific-date branching localised.

### 3.5 `POST /schedules/generate` — no signature change

Still accepts `selected_template_ids`. The per-day override always wins regardless of selection — the override exists *because* the manager wants that day to behave differently. `selected_template_ids` continues to scope the *recurring* template choice.

---

## 4. UI

### 4.1 New page `/manager/special-hours`

Route registered in `App.tsx` inside the `/manager` block. New sidebar entry between `locations` and `roles`:

```ts
{ to: "/manager/special-hours", labelKey: "specialHours" }
```

Page (`frontend/src/pages/manager/SpecialHours.tsx`):
- Top filter bar: Location dropdown (mirroring the filter added on `Employees.tsx` in the team-and-locking PR).
- Optional date range filter.
- Table columns: **Date · Location · Hours · Label · Template · Actions**.
  - Hours formatted in the location's timezone (`HH:MM – HH:MM`).
  - Template column links to the linked `ShiftTemplate` via the existing ShiftTemplates page so the manager can edit the cloned template after creation.
  - Actions per row: `Edit`, `Duplicate`, `Delete`.
- Empty state: "No special hours configured yet."
- `+ Add special hours` button → opens `<SpecialHoursModal>`.

### 4.2 `<SpecialHoursModal>`

Create + edit flows share this component (driven by an optional `editing: SpecialHoursDay | null` prop).

Fields:
- **Date** — `<input type="date">`.
- **Location** — single-select dropdown.
- **Open** — `<input type="time">`.
- **Close** — `<input type="time">`, validated > Open.
- **Label** — optional text input, placeholder "e.g. Christmas Eve, Thanksgiving".
- **Starting draft template** — single-select dropdown of recurring `ShiftTemplate` rows filtered to the chosen Location. Required on create. Hidden on edit (the template is already linked and the manager can edit it directly via the ShiftTemplates page).

Validation:
- Open < Close.
- Date not in the past on create (defensive; edits may be in the past for historical correction).
- If the chosen Location has zero recurring templates: surface inline error from `t.specialHours.noRecurringTemplate` and disable Save.

On Save → `POST /api/v1/special-hours/` (create) or `PUT /api/v1/special-hours/{id}` (edit). Refresh table on success, close modal.

### 4.3 Duplicate-to-other-locations modal

Row-level **Duplicate** button → opens `<DuplicateSpecialHoursModal>`. Body:
- Multi-select list of all Locations the manager can access, **excluding** the source location.
- Confirm button text dynamic: "Duplicate to N locations".

On confirm, frontend issues N parallel `POST /api/v1/special-hours/` calls (each with `location_id = <chosen_loc>`, no `draft_template_id` so the server picks each location's recurring template). Aggregate success and failure responses, surface a per-location summary:
- "Duplicated to North Branch, South Branch."
- "Failed for West Branch: no recurring template — create one and retry."

### 4.4 Schedule page integration

In `Schedule.tsx`:

- After loading the week's draft schedules, also fetch `GET /api/v1/special-hours/?from_date=<mon>&to_date=<sun>`.
- On each per-location card, for each day-of-week header that maps to a date with a special-hours entry, render a small badge: `★ Christmas Eve · 09:00–14:00`.
- The **Generate** button needs no change — the new resolver picks the override automatically server-side. A defensive pre-flight tooltip displays when any day in the selected week has a `SpecialHoursDay` with `shift_template_id IS NULL` (shouldn't happen given the entry flow, but a clean fallback if a future migration ever leaves orphans).

### 4.5 i18n

New keys in `frontend/src/i18n/en.ts`:

```ts
nav: { ..., specialHours: "Special Hours" }

specialHours: {
  title: "Special Hours",
  description: "Days with non-standard operating hours — the scheduler will use the linked template instead of the regular weekly one.",
  addButton: "Add special hours",
  columnDate: "Date",
  columnLocation: "Location",
  columnHours: "Hours",
  columnLabel: "Label",
  columnTemplate: "Template",
  columnActions: "Actions",
  labelPlaceholder: "e.g. Christmas Eve, Thanksgiving",
  openLabel: "Open",
  closeLabel: "Close",
  draftTemplateLabel: "Starting draft template",
  draftTemplateHelp: "We'll clone this template for the special day. You can edit the clone afterwards.",
  duplicateTitle: "Duplicate to other locations",
  duplicateHelp: "Pick the locations to copy this special hours entry to. Each will get its own cloned template.",
  deleteConfirm: "Delete this special hours entry? The cloned template will also be removed.",
  noEntries: "No special hours configured yet.",
  noRecurringTemplate: "{location} has no recurring template — create one before adding special hours.",
  closeAfterOpen: "Close time must be after open time.",
  scheduleBadge: "Special hours",
}
```

Same block (English placeholders) in all 18 non-English locale files, matching the established multi-locale pattern.

---

## 5. Tests

### 5.1 Backend

`tests/test_special_hours.py` (new):
- `POST /` happy path with explicit `draft_template_id` → row + cloned template both created.
- `POST /` with `draft_template_id` omitted → server picks the recurring template.
- `POST /` when location has no recurring template → 400 `no_recurring_template`.
- `POST /` duplicate `(location_id, date)` → 409 `duplicate`.
- `PUT /` updates `open_time` → linked template's role times also update.
- `DELETE /` removes both rows.
- `GET /` with `location_id` filter → only matching rows.
- `GET /` with `from_date / to_date` → only matching dates.
- Manager from a different Company cannot read/write another Company's rows.

`tests/test_template_resolver.py` (new):
- One specific_date row + one recurring → resolver returns specific for that date, recurring for other days.
- No specific_date row → resolver returns recurring for every day.
- Multiple recurring + `selected_template_ids` → returns the selected one.
- Specific date outside the week → ignored.
- Location with zero recurring templates AND no override on the date → raises `LocationMissingTemplate`.

`tests/test_schedule_pipeline.py` additions:
- Generate over a week containing a special-hours date → assert the resulting `state.shift_templates[loc_id]` dict has the override on that day and the recurring template on others.
- Generate with `selected_template_ids` excluding the recurring template that the override was cloned from → override still wins on its date.

### 5.2 Frontend

Manual smoke (no automated frontend test infra exists today):
- Add a special hours entry for tomorrow on Location A using Recurring Template X as draft → table shows the row; ShiftTemplates page shows a new template named "X — tomorrow".
- Edit the entry to change open/close → linked template's roles also update.
- Duplicate to Location B (which has its own recurring template) → both rows show in the table.
- Duplicate to Location C (no recurring template) → error message: "C has no recurring template — create one and retry."
- Open Schedule, pick the week → the special-hours day shows the badge.
- Click Generate → backend resolves the override automatically; the generated schedule reflects the special hours.

---

## 6. Migration / rollout order

1. Apply migration `0025` — adds `shift_templates.specific_date` (nullable, no existing-data backfill needed) and `special_hours_days`.
2. Deploy backend: schemas + endpoints + scheduler resolver + nodes wiring.
3. Deploy frontend: Special Hours page, modal, Schedule-page badge.
4. No data backfill required.

## 7. Open risks

- **Existing customers with multiple recurring templates per location.** `_get_or_create_recurring_template` server-side picks "the one row with `specific_date IS NULL`" assuming there's exactly one. If a Company has multiple recurring templates per location (legitimate use case — different shift patterns per role group), the omit-`draft_template_id` path 400s. Mitigation: that path is only used by the bulk-duplicate flow; the form-driven create always passes `draft_template_id` explicitly. The 400 message must clearly tell the manager to pick a draft template explicitly when duplicating.
- **Editing the cloned template breaks the "open/close mirror" assumption.** The PUT propagation logic assumes the cloned template's `weekly_schedule[0]` is still the day we wrote. If the manager edits the template's `weekly_schedule` to multiple days, the PUT mirror logic should either no-op the propagation or only propagate to `weekly_schedule[0]`. Spec: propagate to `weekly_schedule[0]` only; document the edge case in the code comment.
- **Daylight Saving Time.** `open_time` / `close_time` are stored as wall-clock `Time` values. If a DST transition falls on the special-hours date, the scheduler's existing timezone-aware shift creation already handles DST via `zoneinfo.ZoneInfo(location.timezone)`. No new logic needed; covered by existing tests.
