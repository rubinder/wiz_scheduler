# Features Page — Manager Dashboard Tour

**Date:** 2026-05-11
**Status:** Approved (pending user review)

## Goal

Give prospective customers a way to see every screen a manager will use after logging in, without requiring them to register. The current landing page has a single demo video; this adds a dedicated tour page covering all 15 manager screens with screenshots + explanations.

## Scope

**In scope:**
- New public page at `/features` showing screenshots + descriptions of all 15 manager pages
- CTA button on the existing landing page's "See It in Action" section linking to `/features`
- Playwright script to capture all 15 screenshots from a running dev environment
- English i18n strings for the new page
- Placeholder English values in all 18 other locale files so the site continues to compile/render

**Out of scope:**
- Translating new strings into the other 18 languages (English fallback only)
- Capturing employee-side screenshots (manager-only tour)
- Hosted/automated screenshot regeneration (one-shot manual script)

## Architecture

### Route

- New public route `/features` added to `App.tsx`, sibling of `/privacy-policy`, `/terms`, `/dpa`
- Component: `frontend/src/pages/Features.tsx`

### Landing page change

In `Landing.tsx`, inside the existing `#demo` section (currently lines 152–172), add a CTA button below the video card:

> "Explore every manager screen →"  → links to `/features`

The demo video stays as-is. Button uses the existing `glass-btn-secondary` style for consistency.

Also add a link to the footer link group (next to Privacy/Terms/DPA) for additional discoverability.

### Features page layout

- Top hero: page title + intro paragraph + "← Back to home" link
- Sticky left-rail table of contents on desktop (lg breakpoint and up) — list of 15 page names, each is an anchor link to its section
- Main content: 15 `glass-card` rows, alternating image-left / image-right on desktop. On mobile: stacked, screenshot above text.
- Each row contains: screenshot image (`<img src="/screenshots/<slug>.png" />`), page title, page description (2–4 sentences explaining what a manager uses this page for)
- Bottom CTA: "Ready to try it? → Sign up" using `glass-btn-primary`

Reuses existing `theme.ts` tokens (`text`, `bg`, `border`, `action`) and `glass-card`, `glass-btn-*` utility classes.

### Page list (slug → manager route)

| Slug | Route | Page title |
|---|---|---|
| `dashboard` | `/manager/dashboard` | Dashboard |
| `company` | `/manager/company` | Company |
| `regions` | `/manager/regions` | Regions |
| `locations` | `/manager/locations` | Locations |
| `roles` | `/manager/roles` | Roles |
| `role-equivalents` | `/manager/role-equivalents` | Role Equivalents |
| `employees` | `/manager/employees` | Employees |
| `hour-restrictions` | `/manager/hour-restrictions` | Hour Restrictions |
| `day-blackouts` | `/manager/day-blackouts` | Day Blackouts |
| `employee-onboarding` | `/manager/employee-onboarding` | Employee Onboarding |
| `employee-association` | `/manager/employee-association` | Employee Association |
| `shift-templates` | `/manager/shift-templates` | Shift Templates |
| `schedule` | `/manager/schedule` | Schedule |
| `export-schedules` | `/manager/export-schedules` | Export Schedules |
| `data-privacy` | `/manager/data-privacy` | Data Privacy |

## Screenshot capture

### Tool

- `playwright` added as a `devDependency` in `frontend/package.json`
- Capture script: `frontend/scripts/capture-screenshots.mjs` (Node ESM)

### Prerequisites (the script assumes these are already done)

1. Backend running on `http://localhost:8000`
2. Frontend running on `http://localhost:5173`
3. `python seed.py` has been run — provides a manager login (`abc@example.com` / `example`, from `backend/seed.py:96` + `:93`)

### Script behavior

1. Launch Chromium headless at viewport 1920×1080, deviceScaleFactor=2 (retina-quality PNGs)
2. Navigate to `http://localhost:5173/login`, fill in manager credentials, submit, wait for redirect to `/manager/dashboard`
3. For each of the 15 slugs:
   - Navigate to `http://localhost:5173/manager/<route>`
   - Wait for `networkidle`
   - Take a full-page screenshot → `frontend/public/screenshots/<slug>.png`
4. Print one line per captured file; exit 0 on success, nonzero on any failure
5. Idempotent — running again overwrites all PNGs

### Output

- Directory: `frontend/public/screenshots/` (new)
- 15 PNGs, retina resolution

### Running

Documented in the script header and added to README:

```bash
# Terminal 1
cd backend && uvicorn main:app --reload

# Terminal 2
cd frontend && npm run dev

# Terminal 3 (once-off seed)
cd backend && python seed.py

# Terminal 4 (capture)
cd frontend && node scripts/capture-screenshots.mjs
```

## i18n

### New English keys (in `frontend/src/i18n/en.ts` and types `frontend/src/i18n/types.ts`)

Under a new `features` namespace:

- `pageTitle` — "Every screen a manager uses, end to end"
- `pageIntro` — "Tour the full Wiz Scheduler manager experience: from configuring your company and locations, to defining roles and shift templates, to generating an AI-optimized weekly schedule and exporting it."
- `backToHome` — "← Back to home"
- `tocTitle` — "Tour the screens"
- `ctaTitle` — "Ready to try Wiz Scheduler?"
- `ctaDesc` — "Start with the free tier — no credit card required."
- `ctaBtn` — "Create your account"

And one `{title, desc}` pair per slug, under `features.pages.<slug>`:

- `pages.dashboard.title`, `pages.dashboard.desc`
- `pages.company.title`, `pages.company.desc`
- ... (15 total)

New `landing.exploreDashboard` key added for the CTA button on the existing landing page: "Explore every manager screen →"

New footer link key `landing.featuresLink`: "Manager tour"

### Other 18 locale files

Each non-English locale file (`de`, `ta`, `te`, `ar`, `bn`, `es`, `ur`, `tr`, `zh`, `hi`, `fr`, `ru`, `pt`, `vi`, `mr`, `pcm`, `id`, `ja`) gets the same new keys with English text as the value. Future translation pass can replace these. This keeps TypeScript happy without claiming we've translated content we haven't.

### Type definition

`frontend/src/i18n/types.ts` updated to declare the new `features` namespace and the new `landing` keys.

## Error handling

- **Missing screenshot file at runtime:** `<img>` tags use `loading="lazy"` and have an `onError` fallback that swaps to a styled placeholder div with the page name. Prevents broken-image icons if a slug is added to i18n before its PNG exists.
- **Playwright script:** any navigation/timeout error → log slug + error message, exit nonzero. No retry loop — the user re-runs.
- **Stale screenshots:** acceptable — re-run the capture script.

## Testing

- **Manual:** start dev servers, run the capture script, verify all 15 PNGs land in `frontend/public/screenshots/`, then load `/features` in a browser and check:
  - Both desktop and mobile breakpoints render correctly
  - TOC anchor links jump to the right sections
  - "Back to home" and bottom CTA work
  - Landing page "See It in Action" section shows the new CTA below the video
- **Type check:** `npx tsc --noEmit` in frontend passes (catches i18n typing errors)
- **Build:** `npm run build` succeeds
- **No backend/automated tests** — this is pure frontend UI + a manual capture script

## File changes summary

**New files:**
- `frontend/src/pages/Features.tsx`
- `frontend/scripts/capture-screenshots.mjs`
- `frontend/public/screenshots/*.png` (15 files — generated)
- `docs/superpowers/specs/2026-05-11-features-page-design.md` (this file)

**Modified files:**
- `frontend/src/App.tsx` — add `/features` route
- `frontend/src/pages/Landing.tsx` — add CTA button in `#demo` section + footer link
- `frontend/src/i18n/types.ts` — declare new keys
- `frontend/src/i18n/en.ts` — English values
- `frontend/src/i18n/{de,ta,te,ar,bn,es,ur,tr,zh,hi,fr,ru,pt,vi,mr,pcm,id,ja}.ts` — English placeholder values
- `frontend/package.json` + `frontend/package-lock.json` — add `playwright` devDep
