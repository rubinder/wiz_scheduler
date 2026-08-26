# Sidebar Nav Grouping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Regroup the manager sidebar from 20 flat links into 9 top-level rows with one level of expandable, indented children.

**Architecture:** `NavItem` gains an optional `requiresEmployees` flag and a sibling `NavGroup` type holding `children`. The flat arrays become one `NavEntry[]`. The group containing the active route auto-expands. No routing changes, no page changes — only how existing routes are presented.

**Tech Stack:** React 18, TypeScript, react-router-dom, Tailwind. Tests in pytest (the frontend has no test runner — see Global Constraints).

**Spec:** `docs/superpowers/specs/2026-08-26-scheduling-preferences-design.md` (§ Frontend → Sidebar)

## Global Constraints

- **Do not install new dependencies.** CLAUDE.md forbids it without explicit instruction. The frontend has **no test runner** — no vitest, no jest, no `@testing-library`, and no `test` script in `frontend/package.json`. Do not add one.
- **The frontend gates are `npx tsc --noEmit` and `npm run build`,** both run from `frontend/`. Both must pass before every commit.
- **Backend tests run with `backend/.venv/bin/python -m pytest`** from the repo root. The system Anaconda python lacks this project's dependencies and will fail on imports, looking like broken code.
- **All 19 locale files must carry every new key.** `frontend/src/i18n/LanguageContext.tsx` types translations as `Record<Language, Translations>` derived from `en.ts`, so a key added to `en.ts` alone **fails the TypeScript build**. The locales are: `ar bn de en es fr hi id ja mr pcm pt ru ta te tr ur vi zh`.
- **Preserve the `hasEmployees` behaviour exactly.** Today `Sidebar.tsx:61` hides Employee Availability and Employee Association until the company has at least one employee. That behaviour must survive this refactor unchanged.
- **This PR ships before the preferences feature** and adds no new routes.

---

### Task 1: Route-integrity guard

The safety net, written first. A nav refactor's characteristic failure is a sidebar link pointing at a route that does not exist — invisible to `tsc`, since both are strings. This test parses both files and fails if they disagree.

Verified against the current tree: 23 nav targets, 23 routes, zero mismatches.

**Files:**
- Test: `tests/test_sidebar_routes.py`

**Interfaces:**
- Consumes: nothing
- Produces: nothing consumed by later tasks. It is a guard that must stay green through Tasks 2–4.

- [ ] **Step 1: Write the test**

```python
"""Every sidebar link resolves to a real route.

A nav refactor's characteristic failure is a link pointing at a route that
does not exist. Both sides are plain strings, so TypeScript cannot catch it
and the frontend has no test runner — this is the only automated guard.

Parses the source rather than rendering: App.tsx nests child paths under a
parent `<Route path="/manager">` / `"/employee"`, so a child `path="roles"`
means `/manager/roles`.
"""

import re
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"
_SIDEBAR = _SRC / "components" / "layout" / "Sidebar.tsx"
_APP = _SRC / "App.tsx"


def _nav_targets() -> set[str]:
    return set(re.findall(r'to:\s*"([^"]+)"', _SIDEBAR.read_text()))


def _routes() -> set[str]:
    routes: set[str] = set()
    parent = ""
    for line in _APP.read_text().splitlines():
        absolute = re.search(r'<Route\s+path="(/[^"]+)"', line)
        if absolute:
            parent = absolute.group(1).rstrip("/")
            continue
        relative = re.search(r'<Route\s+path="([^/"][^"]*)"', line)
        if relative:
            routes.add(f"{parent}/{relative.group(1)}")
    return routes


def test_every_sidebar_link_has_a_route():
    missing = sorted(_nav_targets() - _routes())
    assert not missing, (
        f"sidebar links with no matching route in App.tsx: {missing}. "
        "Either the route was renamed or the nav entry has a typo."
    )


def test_the_parser_still_finds_both_sides():
    """Guard the guard: a refactor that changes the shape of either file
    could silently reduce both sets to empty, making the test above pass
    vacuously."""
    assert len(_nav_targets()) >= 20, "parsed too few nav targets — parser is stale"
    assert len(_routes()) >= 20, "parsed too few routes — parser is stale"
```

- [ ] **Step 2: Run it against the un-refactored tree**

Run: `backend/.venv/bin/python -m pytest tests/test_sidebar_routes.py -v`
Expected: **2 passed**. It passes today — that is the point. It must still pass after the refactor.

- [ ] **Step 3: Verify it can fail**

Temporarily change one `to:` in `Sidebar.tsx` to `/manager/does-not-exist`, re-run, confirm `test_every_sidebar_link_has_a_route` FAILS naming that path, then revert.

```bash
git diff --exit-code frontend/src/components/layout/Sidebar.tsx   # must be clean after revert
```

- [ ] **Step 4: Commit**

```bash
git add tests/test_sidebar_routes.py
git commit -m "test(nav): guard that every sidebar link resolves to a route"
```

---

### Task 2: Nav data model — groups and an explicit employees flag

Replaces the two flat arrays with one `NavEntry[]`, and removes the positional `.slice(2)`.

**`Sidebar.tsx:61` is the trap in this task.** Today it reads:

```js
: [...baseManagerLinks, ...postEmployeeManagerLinks.slice(2)]
```

That `.slice(2)` drops the *first two entries of `postEmployeeManagerLinks` by position* — Employee Availability and Employee Association — when the company has no employees. This refactor moves those two items into different groups, so the positional slice would silently start hiding the wrong things. Replace it with an explicit per-item flag.

**Files:**
- Modify: `frontend/src/components/layout/Sidebar.tsx:8-62`

**Interfaces:**
- Consumes: nothing
- Produces: `NavItem` (`{ to: string; labelKey: string; requiresEmployees?: boolean }`), `NavGroup` (`{ labelKey: string; children: NavItem[] }`), `NavEntry = NavItem | NavGroup`, the type guard `isGroup(e: NavEntry): e is NavGroup`, and the constants `managerNav: NavEntry[]` / `employeeNav: NavEntry[]`. Task 3 renders these.

- [ ] **Step 1: Replace the interfaces and the three arrays**

Replace lines 8–43 of `Sidebar.tsx` with:

```tsx
interface NavItem {
  to: string;
  labelKey: string;
  /** Hidden until the company has at least one employee. */
  requiresEmployees?: boolean;
}

interface NavGroup {
  labelKey: string;
  children: NavItem[];
}

type NavEntry = NavItem | NavGroup;

function isGroup(entry: NavEntry): entry is NavGroup {
  return (entry as NavGroup).children !== undefined;
}

const managerNav: NavEntry[] = [
  { to: "/manager/dashboard", labelKey: "dashboard" },
  {
    labelKey: "groupOrganization",
    children: [
      { to: "/manager/company", labelKey: "company" },
      { to: "/manager/team", labelKey: "team" },
      { to: "/manager/regions", labelKey: "regions" },
      { to: "/manager/locations", labelKey: "locations" },
      { to: "/manager/special-hours", labelKey: "specialHours" },
    ],
  },
  {
    labelKey: "groupRoles",
    children: [
      { to: "/manager/roles", labelKey: "roles" },
      { to: "/manager/role-equivalents", labelKey: "roleEquivalents" },
    ],
  },
  {
    labelKey: "groupPeople",
    children: [
      { to: "/manager/employees", labelKey: "employees" },
      { to: "/manager/employee-onboarding", labelKey: "employeeOnboarding" },
      {
        to: "/manager/employee-availability",
        labelKey: "employeeAvailability",
        requiresEmployees: true,
      },
    ],
  },
  {
    labelKey: "groupSchedulingRules",
    children: [
      {
        to: "/manager/employee-association",
        labelKey: "employeeAssociation",
        requiresEmployees: true,
      },
      { to: "/manager/hour-restrictions", labelKey: "hourRestrictions" },
      { to: "/manager/day-blackouts", labelKey: "dayBlackouts" },
    ],
  },
  {
    labelKey: "groupScheduling",
    children: [
      { to: "/manager/shift-templates", labelKey: "shiftTemplates" },
      { to: "/manager/schedule", labelKey: "schedule" },
      { to: "/manager/export-schedules", labelKey: "exportSchedules" },
    ],
  },
  {
    labelKey: "groupCheckIn",
    children: [
      { to: "/manager/check-in-qr", labelKey: "checkInQr" },
      { to: "/manager/check-in-report", labelKey: "checkInReport" },
    ],
  },
  { to: "/manager/data-privacy", labelKey: "dataPrivacy" },
];

const employeeNav: NavEntry[] = [
  { to: "/employee/availability", labelKey: "myAvailability" },
  { to: "/employee/check-in", labelKey: "checkIn" },
  { to: "/employee/data-privacy", labelKey: "dataPrivacy" },
];
```

- [ ] **Step 2: Confirm the link set is unchanged**

Run: `backend/.venv/bin/python -m pytest tests/test_sidebar_routes.py -v`
Expected: **2 passed**. Every `to:` still resolves. The file will not compile yet — Task 3 replaces the renderer — but this test parses source text, so it runs regardless.

- [ ] **Step 3: Do not commit yet**

`Sidebar.tsx` still references the deleted `baseManagerLinks` at line ~58 and will fail `tsc`. Task 3 finishes the change; commit at the end of Task 3.

---

### Task 3: Render nested groups with auto-expand

**Files:**
- Modify: `frontend/src/components/layout/Sidebar.tsx:45-93`

**Interfaces:**
- Consumes: `NavEntry`, `NavGroup`, `NavItem`, `isGroup`, `managerNav`, `employeeNav` from Task 2.
- Produces: the finished component. Task 4 supplies the `groupOrganization` / `groupRoles` / `groupPeople` / `groupSchedulingRules` / `groupScheduling` / `groupCheckIn` label keys it reads.

- [ ] **Step 1: Add the `useLocation` import**

Change line 2 from `import { NavLink } from "react-router-dom";` to:

```tsx
import { NavLink, useLocation } from "react-router-dom";
```

- [ ] **Step 2: Replace the component body**

Replace from `export default function Sidebar() {` to the end of the file:

```tsx
export default function Sidebar() {
  const { user } = useAuth();
  const { t } = useLanguage();
  const { pathname } = useLocation();
  const isManager = user?.user_role === "manager";
  const [hasEmployees, setHasEmployees] = useState(false);
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (!isManager) return;
    listEmployees()
      .then((emps) => setHasEmployees(emps.length > 0))
      .catch(() => setHasEmployees(false));
  }, [isManager]);

  const entries = isManager ? managerNav : employeeNav;

  // Auto-expand whichever group owns the active route, so a deep link never
  // lands the user inside a collapsed tree. Only ever opens a group — it must
  // not re-close one the user opened by hand.
  useEffect(() => {
    const owner = entries.find(
      (e) => isGroup(e) && e.children.some((c) => pathname === c.to),
    );
    if (owner) {
      setOpenGroups((prev) => ({ ...prev, [(owner as NavGroup).labelKey]: true }));
    }
  }, [pathname, entries]);

  const label = (key: string) => t.nav[key as keyof typeof t.nav];
  const visible = (item: NavItem) => !item.requiresEmployees || hasEmployees;

  const linkClass = ({ isActive }: { isActive: boolean }) =>
    `block px-3 py-2 rounded-lg text-sm font-medium transition-all ${
      isActive ? nav.active : nav.inactive
    }`;

  return (
    <aside className={`w-64 ${bg.sidebar} border-r ${border.default} ${text.primary} min-h-screen flex flex-col`}>
      <div className={`p-5 border-b ${border.default}`}>
        <h1 className="text-xl font-bold tracking-wide flex items-center gap-2">{t.common.appName} <img src="/favicon.svg" alt="" className="w-7 h-7" /></h1>
      </div>
      <nav className="flex-1 p-4 space-y-1">
        {entries.map((entry) => {
          if (!isGroup(entry)) {
            return (
              <NavLink key={entry.to} to={entry.to} className={linkClass}>
                {label(entry.labelKey)}
              </NavLink>
            );
          }

          const children = entry.children.filter(visible);
          if (children.length === 0) return null;

          const isOpen = openGroups[entry.labelKey] ?? false;
          return (
            <div key={entry.labelKey}>
              <button
                type="button"
                aria-expanded={isOpen}
                onClick={() =>
                  setOpenGroups((prev) => ({ ...prev, [entry.labelKey]: !isOpen }))
                }
                className={`w-full flex items-center justify-between px-3 py-2 rounded-lg text-sm font-medium transition-all ${nav.inactive}`}
              >
                <span>{label(entry.labelKey)}</span>
                <span aria-hidden="true" className="text-xs">{isOpen ? "▾" : "▸"}</span>
              </button>
              {isOpen && (
                <div className="ml-3 pl-2 border-l border-gray-200 space-y-1 mt-1">
                  {children.map((child) => (
                    <NavLink key={child.to} to={child.to} className={linkClass}>
                      {label(child.labelKey)}
                    </NavLink>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </nav>
      {user && (
        <div className={`p-4 border-t ${border.default} text-sm ${text.muted}`}>
          {user.full_name ?? user.email}
        </div>
      )}
    </aside>
  );
}
```

- [ ] **Step 3: Confirm it fails to compile — for the right reason**

Run: `cd frontend && npx tsc --noEmit`
Expected: **FAIL**, complaining that `groupOrganization` (and the other five group keys) are not assignable to `keyof typeof t.nav`. That is Task 4's job. Any *other* error is a mistake in this task — fix it before moving on.

---

### Task 4: Group labels across all 19 locales

**Files:**
- Modify: all 19 of `frontend/src/i18n/{ar,bn,de,en,es,fr,hi,id,ja,mr,pcm,pt,ru,ta,te,tr,ur,vi,zh}.ts`

**Interfaces:**
- Consumes: the six group `labelKey` values from Task 2.
- Produces: `t.nav.groupOrganization`, `.groupRoles`, `.groupPeople`, `.groupSchedulingRules`, `.groupScheduling`, `.groupCheckIn`.

- [ ] **Step 1: Add the English keys**

In `frontend/src/i18n/en.ts`, inside the `nav: {` block (starts line 62), add:

```ts
    groupOrganization: "Organization",
    groupRoles: "Roles & Skills",
    groupPeople: "People",
    groupSchedulingRules: "Scheduling Rules",
    groupScheduling: "Scheduling",
    groupCheckIn: "Check-In",
```

`groupRoles` is deliberately **not** "Roles" — a group labelled identically to its own child (`roles: "Roles"`) reads as a bug.

- [ ] **Step 2: Confirm the build now fails on the other 18 locales**

Run: `cd frontend && npx tsc --noEmit`
Expected: **FAIL** — 18 errors, one per locale file, each missing the six keys. This is the typing doing its job.

- [ ] **Step 3: Add the keys to the remaining 18 locales**

Translate per locale — do not paste English into all of them. Match the tone of each file's existing `nav` entries. As a worked example, `es.ts`:

```ts
    groupOrganization: "Organización",
    groupRoles: "Roles y Habilidades",
    groupPeople: "Personas",
    groupSchedulingRules: "Reglas de Programación",
    groupScheduling: "Programación",
    groupCheckIn: "Registro de Entrada",
```

- [ ] **Step 4: Verify the whole frontend**

```bash
cd frontend && npx tsc --noEmit && npm run build
```
Expected: `tsc` silent (exit 0), build succeeds.

- [ ] **Step 5: Verify the guard and the backend are untouched**

```bash
backend/.venv/bin/python -m pytest tests/ -q
```
Expected: all pass, including `tests/test_sidebar_routes.py`.

- [ ] **Step 6: Manual check — the one thing no test covers**

Run `npm run dev` and confirm, as a manager:
1. Nine top-level rows; groups collapsed except the one owning the current page.
2. Clicking a group header expands and collapses it.
3. Deep-linking to `/manager/day-blackouts` lands with **Scheduling Rules** already expanded.
4. **The `hasEmployees` behaviour:** with a company that has zero employees, Employee Availability and Employee Association are both hidden — and because People and Scheduling Rules still have other children, neither group disappears.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/layout/Sidebar.tsx frontend/src/i18n/
git commit -m "feat(nav): group the manager sidebar into expandable sections

Twenty flat links become nine top-level rows with one level of indented
children. The group owning the active route auto-expands.

Replaces the positional \`postEmployeeManagerLinks.slice(2)\` with an explicit
\`requiresEmployees\` flag per item. The slice hid Employee Availability and
Employee Association by array position; this change moves both into different
groups, which would have silently started hiding the wrong entries."
```

---

## Self-Review

**Spec coverage.** The spec's Sidebar section specifies: `NavItem` gains `children` (Task 2 — implemented as a sibling `NavGroup` type, which is cleaner in TypeScript than an optional field that is only valid when `to` is absent); parent rows expand an indented list (Task 3); active group auto-expands (Task 3); `hasEmployees` carries over unchanged (Task 2 flag + Task 4 Step 6 manual check); the exact nine-group layout including the People/Scheduling-rules swap (Task 2); group labels across 19 locales (Task 4); ships as its own PR before the feature (stated in Global Constraints). The three `★` preference rows are correctly **absent** — they arrive with the feature PR.

**Placeholder scan.** No TBDs. Every code step carries the literal code. The one instruction that is not verbatim code — "translate per locale" in Task 4 Step 3 — carries a worked example and cannot be mechanised, since inventing 18 translations here would be worse than having the implementer do it against each file's existing tone.

**Type consistency.** `NavItem`, `NavGroup`, `NavEntry`, `isGroup`, `managerNav`, `employeeNav` are defined in Task 2 and consumed under those exact names in Task 3. The six `group*` keys are introduced in Task 2's data, fail the build in Task 3 Step 3, and are satisfied in Task 4 — the ordering is deliberate, so the type system proves the keys are needed before they are added.
