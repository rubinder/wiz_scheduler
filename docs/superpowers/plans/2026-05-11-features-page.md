# Features Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a public `/features` page that shows screenshots + descriptions of all 15 manager screens, linked from a CTA button in the landing page's "See It in Action" section.

**Architecture:** Frontend-only React page that reads its content (title, description per screen) from i18n. Screenshots are captured once via a Playwright script that logs in as the seeded demo manager and saves PNGs to `frontend/public/screenshots/`. The landing page gets a new CTA button below the existing demo video.

**Tech Stack:** React 18 + TypeScript + Vite + Tailwind, React Router v6, Playwright (new devDep) for one-shot screenshot capture, existing i18n (`useLanguage` hook, locale files in `frontend/src/i18n/`).

**Note on TDD:** This feature is pure presentational UI — no business logic to unit test. Verification is via TypeScript typecheck + production build + manual visual check. Each task ends with the appropriate verification command.

**Spec:** `docs/superpowers/specs/2026-05-11-features-page-design.md`

---

## Task 1: Add Playwright devDependency and capture script

**Files:**
- Modify: `frontend/package.json`
- Create: `frontend/scripts/capture-screenshots.mjs`
- Create: `frontend/public/screenshots/.gitkeep`

- [ ] **Step 1: Install Playwright as a frontend devDependency**

Run from repo root:

```bash
cd frontend && npm install --save-dev playwright
```

Expected: `playwright` added to `devDependencies` in `frontend/package.json`. `package-lock.json` updated.

- [ ] **Step 2: Install Playwright's bundled Chromium**

```bash
cd frontend && npx playwright install chromium
```

Expected: Chromium downloaded to `~/Library/Caches/ms-playwright/` (or platform equivalent). Logs success.

- [ ] **Step 3: Create the screenshots output directory with a .gitkeep**

Create `frontend/public/screenshots/.gitkeep` (empty file). This ensures the directory exists in git even before screenshots are committed.

- [ ] **Step 4: Create the capture script**

Create `frontend/scripts/capture-screenshots.mjs` with the following content:

```javascript
#!/usr/bin/env node
// Capture screenshots of all 15 manager pages.
//
// Prerequisites:
//   1. Backend running:  cd backend && uvicorn main:app --reload
//   2. Frontend running: cd frontend && npm run dev
//   3. Seed run:         cd backend && python seed.py
//
// Run:
//   cd frontend && node scripts/capture-screenshots.mjs

import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUT_DIR = resolve(__dirname, "..", "public", "screenshots");

const FRONTEND_URL = process.env.FRONTEND_URL || "http://localhost:5173";
const MANAGER_EMAIL = process.env.MANAGER_EMAIL || "abc@example.com";
const MANAGER_PASSWORD = process.env.MANAGER_PASSWORD || "example";

const PAGES = [
  { slug: "dashboard", path: "/manager/dashboard" },
  { slug: "company", path: "/manager/company" },
  { slug: "regions", path: "/manager/regions" },
  { slug: "locations", path: "/manager/locations" },
  { slug: "roles", path: "/manager/roles" },
  { slug: "role-equivalents", path: "/manager/role-equivalents" },
  { slug: "employees", path: "/manager/employees" },
  { slug: "hour-restrictions", path: "/manager/hour-restrictions" },
  { slug: "day-blackouts", path: "/manager/day-blackouts" },
  { slug: "employee-onboarding", path: "/manager/employee-onboarding" },
  { slug: "employee-association", path: "/manager/employee-association" },
  { slug: "shift-templates", path: "/manager/shift-templates" },
  { slug: "schedule", path: "/manager/schedule" },
  { slug: "export-schedules", path: "/manager/export-schedules" },
  { slug: "data-privacy", path: "/manager/data-privacy" },
];

async function main() {
  await mkdir(OUT_DIR, { recursive: true });

  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    deviceScaleFactor: 2,
  });
  const page = await context.newPage();

  console.log(`[login] ${FRONTEND_URL}/login as ${MANAGER_EMAIL}`);
  await page.goto(`${FRONTEND_URL}/login`, { waitUntil: "networkidle" });
  await page.fill('input[type="email"]', MANAGER_EMAIL);
  await page.fill('input[type="password"]', MANAGER_PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/manager\/dashboard/, { timeout: 15000 });
  console.log("[login] success");

  for (const { slug, path } of PAGES) {
    const url = `${FRONTEND_URL}${path}`;
    const out = resolve(OUT_DIR, `${slug}.png`);
    process.stdout.write(`[capture] ${slug} ... `);
    try {
      await page.goto(url, { waitUntil: "networkidle", timeout: 20000 });
      await page.waitForTimeout(500); // settle animations
      await page.screenshot({ path: out, fullPage: true });
      console.log(`saved ${out}`);
    } catch (err) {
      console.log(`FAILED: ${err.message}`);
      await browser.close();
      process.exit(1);
    }
  }

  await browser.close();
  console.log("[done] all 15 screenshots captured");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
```

- [ ] **Step 5: Verify the script file is syntactically valid**

```bash
cd frontend && node --check scripts/capture-screenshots.mjs
```

Expected: no output (success).

- [ ] **Step 6: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/scripts/capture-screenshots.mjs frontend/public/screenshots/.gitkeep
git commit -m "feat(features): add playwright capture script for manager screenshots"
```

---

## Task 2: Run the capture script to generate screenshots

**Files:**
- Generate: `frontend/public/screenshots/<slug>.png` × 15

- [ ] **Step 1: Ensure backend is running**

In a separate terminal (or background):

```bash
cd backend && uvicorn main:app --reload
```

Wait until logs show `Application startup complete.` Verify with:

```bash
curl -s http://localhost:8000/docs >/dev/null && echo "backend OK"
```

Expected: `backend OK`

- [ ] **Step 2: Seed demo data**

```bash
cd backend && python seed.py
```

Expected: Idempotent — logs creation or "already exists" for each entity. Creates manager user `abc@example.com` with password `example`.

- [ ] **Step 3: Ensure frontend dev server is running**

In a separate terminal:

```bash
cd frontend && npm run dev
```

Wait until logs show `Local:   http://localhost:5173/`. Verify with:

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:5173/ ; echo
```

Expected: `200`

- [ ] **Step 4: Run the capture script**

```bash
cd frontend && node scripts/capture-screenshots.mjs
```

Expected output:
```
[login] http://localhost:5173/login as abc@example.com
[login] success
[capture] dashboard ... saved /...path.../dashboard.png
[capture] company ... saved /...path.../company.png
... (15 lines total)
[done] all 15 screenshots captured
```

- [ ] **Step 5: Verify all 15 PNGs exist and are non-empty**

```bash
ls -la frontend/public/screenshots/*.png | wc -l
```

Expected: `15`

```bash
find frontend/public/screenshots -name "*.png" -size -1k
```

Expected: empty output (no files under 1 KB; any tiny file would indicate a blank/failed capture).

- [ ] **Step 6: Commit the screenshots**

```bash
git add frontend/public/screenshots/*.png
git commit -m "feat(features): capture manager page screenshots"
```

---

## Task 3: Add new i18n keys to `en.ts`

**Files:**
- Modify: `frontend/src/i18n/en.ts`

- [ ] **Step 1: Add the `landing.exploreDashboard` and `landing.featuresLink` keys**

In `frontend/src/i18n/en.ts`, find the `landing:` block (starts around line 554) and add two new keys near `viewDemo`. After the line containing `viewDemo: "View Demo",`, add:

```typescript
    exploreDashboard: "Explore every manager screen →",
    featuresLink: "Manager tour",
```

- [ ] **Step 2: Add a new `features` block at the top level of the default export**

In `frontend/src/i18n/en.ts`, find the closing `}` of the `landing` block. After the comma that follows the `landing` block's closing `}`, add the entire `features` block below. Place it before the next existing top-level key (whichever follows `landing`):

```typescript
  features: {
    pageTitle: "Every screen a manager uses, end to end",
    pageIntro:
      "Take a guided tour of the Wiz Scheduler manager experience: from configuring your company and locations, to defining roles and shift templates, to generating an AI-optimized weekly schedule and exporting it.",
    backToHome: "← Back to home",
    tocTitle: "Tour the screens",
    ctaTitle: "Ready to try Wiz Scheduler?",
    ctaDesc: "Start with the free tier — no credit card required.",
    ctaBtn: "Create your account",
    pages: {
      dashboard: {
        title: "Dashboard",
        desc: "Your home base. See usage at a glance: billing period status, schedules generated, AI credits used, and storage. A quick jumping-off point for the screens you visit most.",
      },
      company: {
        title: "Company",
        desc: "Configure your ownership group: company name, logo, branding, and high-level settings that apply across every region and location.",
      },
      regions: {
        title: "Regions",
        desc: "Group locations by region for reporting and management. Helpful for multi-state, multi-country, or franchise operators who want a layer above the individual location.",
      },
      locations: {
        title: "Locations",
        desc: "Manage every physical site you schedule for. Each location has its own timezone, address, region, and roster of eligible employees — and is scheduled independently by the AI.",
      },
      roles: {
        title: "Roles",
        desc: "Define the job titles employees can be scheduled into. Roles are fully customizable per company — there are no hardcoded role names, so Wiz Scheduler adapts to any industry vocabulary.",
      },
      "role-equivalents": {
        title: "Role Equivalents",
        desc: "Map roles that can cover for each other. Lets the scheduler treat “Barista” and “Baker” as interchangeable when filling a shift, while respecting skill-level differences.",
      },
      employees: {
        title: "Employees",
        desc: "The master roster. Set each person’s assigned roles, skill level, locations they can work at, hour limits, and availability. Inline editing makes bulk updates fast.",
      },
      "hour-restrictions": {
        title: "Hour Restrictions",
        desc: "Enforce per-employee weekly hour caps and minimums. Useful for student visas, part-time agreements, and overtime budgets. The AI will never schedule outside these bounds.",
      },
      "day-blackouts": {
        title: "Day Blackouts",
        desc: "Block off entire days when a location is closed or an employee is unavailable. Holidays, vacations, training days — the scheduler respects them all automatically.",
      },
      "employee-onboarding": {
        title: "Employee Onboarding",
        desc: "Invite new hires by email. They self-serve their availability and personal details — you stay focused on running the business, not chasing data entry.",
      },
      "employee-association": {
        title: "Employee Association",
        desc: "Mark which employees work well together (or shouldn’t be paired). The AI uses these affinities and anti-affinities as soft preferences when assigning shifts.",
      },
      "shift-templates": {
        title: "Shift Templates",
        desc: "Define each location’s recurring weekly shift pattern: which roles are needed, how many of each, on which days, and at what times. The blueprint the AI fills in.",
      },
      schedule: {
        title: "Schedule",
        desc: "Generate optimized weekly schedules with one click. Pick an algorithmic strategy (Rotation, Max Hours, Random) or AI. Review per-location results, edit inline, and publish.",
      },
      "export-schedules": {
        title: "Export Schedules",
        desc: "Download published schedules as CSV or PDF, or push directly to 7shifts and Deputy. Keep your existing payroll and POS workflows unchanged.",
      },
      "data-privacy": {
        title: "Data Privacy",
        desc: "GDPR-friendly tools for data export, erasure, and consent. Manager-side view of every data subject request and its status.",
      },
    },
  },
```

- [ ] **Step 3: Run TypeScript check (other locales will fail — that's expected and fixed in Task 4)**

```bash
cd frontend && npx tsc --noEmit 2>&1 | head -30
```

Expected: errors for each of the 18 non-English locale files saying they are missing `features` and the new `landing` keys. The errors should NOT come from `Landing.tsx`, `Features.tsx`, or `en.ts` itself. This proves the type was extended correctly.

- [ ] **Step 4: Commit (note: don't fix the type errors yet — Task 4 does that)**

```bash
git add frontend/src/i18n/en.ts
git commit -m "feat(features): add english i18n keys for manager tour page"
```

---

## Task 4: Mirror new keys into all 18 non-English locale files

**Files:**
- Modify: `frontend/src/i18n/{de,es,fr,zh,hi,ar,bn,ru,pt,ur,id,ja,pcm,mr,te,tr,ta,vi}.ts`

This task adds the same English text to every non-English locale file so the codebase compiles. Per the spec, machine translation is out of scope.

- [ ] **Step 1: For each of the 18 locale files, add the same two `landing` keys**

For every file in `frontend/src/i18n/` matching `{de,es,fr,zh,hi,ar,bn,ru,pt,ur,id,ja,pcm,mr,te,tr,ta,vi}.ts`, locate the `landing:` block and add (in the same place as Task 3 Step 1) the two keys:

```typescript
    exploreDashboard: "Explore every manager screen →",
    featuresLink: "Manager tour",
```

- [ ] **Step 2: For each of the same 18 files, add the same `features` block**

Add the same `features` block from Task 3 Step 2 (with the same English values) at the top level after the `landing` block. The content is byte-for-byte identical to the English version.

- [ ] **Step 3: Verify TypeScript check passes**

```bash
cd frontend && npx tsc --noEmit
```

Expected: no errors (exit 0).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/i18n/de.ts frontend/src/i18n/es.ts frontend/src/i18n/fr.ts frontend/src/i18n/zh.ts frontend/src/i18n/hi.ts frontend/src/i18n/ar.ts frontend/src/i18n/bn.ts frontend/src/i18n/ru.ts frontend/src/i18n/pt.ts frontend/src/i18n/ur.ts frontend/src/i18n/id.ts frontend/src/i18n/ja.ts frontend/src/i18n/pcm.ts frontend/src/i18n/mr.ts frontend/src/i18n/te.ts frontend/src/i18n/tr.ts frontend/src/i18n/ta.ts frontend/src/i18n/vi.ts
git commit -m "feat(features): mirror english placeholders into non-english locales"
```

---

## Task 5: Create the `Features.tsx` page component

**Files:**
- Create: `frontend/src/pages/Features.tsx`

- [ ] **Step 1: Create the page component**

Create `frontend/src/pages/Features.tsx`:

```tsx
import { Link } from "react-router-dom";
import { useLanguage } from "../i18n/LanguageContext";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import LanguageSelector from "../components/shared/LanguageSelector";
import { text, bg, border } from "../theme";

const PAGE_SLUGS = [
  "dashboard",
  "company",
  "regions",
  "locations",
  "roles",
  "role-equivalents",
  "employees",
  "hour-restrictions",
  "day-blackouts",
  "employee-onboarding",
  "employee-association",
  "shift-templates",
  "schedule",
  "export-schedules",
  "data-privacy",
] as const;

type Slug = (typeof PAGE_SLUGS)[number];

export default function Features() {
  const { t } = useLanguage();
  useDocumentTitle("Manager Tour");

  return (
    <div className="min-h-screen">
      {/* Nav */}
      <nav
        className={`fixed top-0 inset-x-0 z-50 bg-white/50 backdrop-blur-2xl border-b ${border.default}`}
      >
        <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
          <Link to="/" className="flex items-center gap-3">
            <img src="/favicon.svg" alt="" className="w-8 h-8" />
            <span className={`text-xl font-bold ${text.primary} tracking-wide`}>
              Wiz Scheduler
            </span>
          </Link>
          <div className="flex items-center gap-4">
            <LanguageSelector />
            <Link
              to="/"
              className={`text-sm ${text.secondary} hover:${text.heading} transition-colors`}
            >
              {t.features.backToHome}
            </Link>
            <Link to="/register" className="glass-btn-primary text-sm">
              {t.register.registerBtn}
            </Link>
          </div>
        </div>
      </nav>

      {/* Hero */}
      <section className="pt-32 pb-12 px-6">
        <div className="max-w-4xl mx-auto text-center">
          <h1
            className={`text-4xl sm:text-5xl font-extrabold ${text.heading} leading-tight mb-6`}
          >
            {t.features.pageTitle}
          </h1>
          <p
            className={`text-lg ${text.muted} max-w-2xl mx-auto leading-relaxed`}
          >
            {t.features.pageIntro}
          </p>
        </div>
      </section>

      {/* Body: sticky TOC (desktop) + screen rows */}
      <section className="px-6 pb-20">
        <div className="max-w-7xl mx-auto lg:grid lg:grid-cols-[220px_1fr] lg:gap-10">
          {/* TOC */}
          <aside className="hidden lg:block">
            <div className="sticky top-24">
              <h2
                className={`text-xs font-semibold uppercase tracking-wider ${text.muted} mb-3`}
              >
                {t.features.tocTitle}
              </h2>
              <nav className="flex flex-col gap-1">
                {PAGE_SLUGS.map((slug) => (
                  <a
                    key={slug}
                    href={`#${slug}`}
                    className={`text-sm ${text.secondary} hover:${text.heading} transition-colors py-1`}
                  >
                    {t.features.pages[slug].title}
                  </a>
                ))}
              </nav>
            </div>
          </aside>

          {/* Rows */}
          <div className="flex flex-col gap-10">
            {PAGE_SLUGS.map((slug, idx) => {
              const reverse = idx % 2 === 1;
              return (
                <article
                  id={slug}
                  key={slug}
                  className={`glass-card p-6 lg:p-8 scroll-mt-24 grid grid-cols-1 lg:grid-cols-2 gap-6 lg:gap-10 items-center ${
                    reverse ? "lg:[&>div:first-child]:order-2" : ""
                  }`}
                >
                  <div className="rounded-xl overflow-hidden border border-sage/20 bg-sage/5 aspect-[16/10]">
                    <ScreenshotImage slug={slug} title={t.features.pages[slug].title} />
                  </div>
                  <div>
                    <h3
                      className={`text-2xl font-bold ${text.heading} mb-3`}
                    >
                      {t.features.pages[slug].title}
                    </h3>
                    <p className={`${text.muted} leading-relaxed`}>
                      {t.features.pages[slug].desc}
                    </p>
                  </div>
                </article>
              );
            })}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="py-20 px-6">
        <div className="max-w-3xl mx-auto text-center">
          <h2 className={`text-4xl font-bold ${text.heading} mb-4`}>
            {t.features.ctaTitle}
          </h2>
          <p className={`${text.muted} mb-8 text-lg`}>{t.features.ctaDesc}</p>
          <div className="flex items-center justify-center gap-4">
            <Link
              to="/register"
              className="glass-btn-primary px-8 py-3 text-base"
            >
              {t.features.ctaBtn}
            </Link>
            <Link to="/" className="glass-btn-secondary px-8 py-3 text-base">
              {t.features.backToHome}
            </Link>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className={`border-t ${border.default} py-8 px-6`}>
        <div className="max-w-6xl mx-auto flex items-center justify-between">
          <div className={`flex items-center gap-2 ${text.muted} text-sm`}>
            <img src="/favicon.svg" alt="" className="w-5 h-5" />
            <span>Wiz Scheduler</span>
          </div>
          <div className={`text-xs ${text.muted}`}>Suggestival LLC</div>
        </div>
      </footer>
    </div>
  );
}

function ScreenshotImage({ slug, title }: { slug: Slug; title: string }) {
  return (
    <img
      src={`/screenshots/${slug}.png`}
      alt={`Screenshot of the ${title} page`}
      loading="lazy"
      className="w-full h-full object-cover object-top"
      onError={(e) => {
        const img = e.currentTarget;
        const parent = img.parentElement;
        if (!parent) return;
        img.style.display = "none";
        parent.classList.add("flex", "items-center", "justify-center");
        const placeholder = document.createElement("div");
        placeholder.className = "text-sm text-gray-500 px-4 text-center";
        placeholder.textContent = `Screenshot pending: ${title}`;
        parent.appendChild(placeholder);
      }}
    />
  );
}
```

Note: `bg` is imported even though unused in the current draft — remove if your linter rejects it. (Adjust the import line below if needed.)

If your TypeScript config flags unused imports, change the import line to:

```typescript
import { text, border } from "../theme";
```

- [ ] **Step 2: TypeScript check**

```bash
cd frontend && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/Features.tsx
git commit -m "feat(features): add Features page component for manager tour"
```

---

## Task 6: Wire up the `/features` route

**Files:**
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Import the new page**

In `frontend/src/App.tsx`, find the import block (lines 1–28). After the line:

```typescript
import Landing from "./pages/Landing";
```

add:

```typescript
import Features from "./pages/Features";
```

- [ ] **Step 2: Register the public route**

In the same file, find the public routes block (around line 71–77, the routes for `/login`, `/register`, `/privacy-policy`, etc.). After the line:

```tsx
<Route path="/dpa" element={<DataProcessingAgreement />} />
```

add:

```tsx
<Route path="/features" element={<Features />} />
```

- [ ] **Step 3: TypeScript check**

```bash
cd frontend && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Verify the route loads**

Confirm frontend dev server is still running. Then:

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:5173/features ; echo
```

Expected: `200`

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat(features): add /features public route"
```

---

## Task 7: Add CTA button and footer link on the Landing page

**Files:**
- Modify: `frontend/src/pages/Landing.tsx`

- [ ] **Step 1: Add CTA button under the demo video**

In `frontend/src/pages/Landing.tsx`, find the `#demo` section (currently lines 152–172). Replace the entire section with this version (which adds a centered CTA button below the video card):

```tsx
      {/* Demo Video */}
      <section id="demo" className="py-20 px-6">
        <div className="max-w-4xl mx-auto">
          <h2 className={`text-3xl font-bold ${text.heading} text-center mb-4`}>{t.landing.demoTitle}</h2>
          <p className={`${text.muted} text-center mb-12 max-w-2xl mx-auto`}>
            {t.landing.demoDesc}
          </p>
          <div className="glass-card p-2 rounded-2xl overflow-hidden">
            <div className="relative w-full" style={{ paddingBottom: "56.25%" }}>
              <video
                className="absolute inset-0 w-full h-full rounded-xl"
                controls
                preload="metadata"
                poster="/demo-poster.jpg"
              >
                <source src="/demo.mp4" type="video/mp4" />
                {t.landing.demoFallback}
              </video>
            </div>
          </div>
          <div className="flex justify-center mt-8">
            <Link to="/features" className="glass-btn-secondary px-8 py-3 text-base">
              {t.landing.exploreDashboard}
            </Link>
          </div>
        </div>
      </section>
```

- [ ] **Step 2: Add link to footer**

In the same file, find the footer (currently lines 421–434). The `<div>` containing the legal links has Privacy/Terms/DPA. Update that block to include the Features link. Replace:

```tsx
          <div className={`flex items-center gap-4 text-xs ${text.muted}`}>
            <Link to="/privacy-policy" className="hover:text-gray-700">{t.gdpr.privacyPolicy}</Link>
            <Link to="/terms" className="hover:text-gray-700">{t.gdpr.termsOfService}</Link>
            <Link to="/dpa" className="hover:text-gray-700">{t.gdpr.dpa}</Link>
          </div>
```

with:

```tsx
          <div className={`flex items-center gap-4 text-xs ${text.muted}`}>
            <Link to="/features" className="hover:text-gray-700">{t.landing.featuresLink}</Link>
            <Link to="/privacy-policy" className="hover:text-gray-700">{t.gdpr.privacyPolicy}</Link>
            <Link to="/terms" className="hover:text-gray-700">{t.gdpr.termsOfService}</Link>
            <Link to="/dpa" className="hover:text-gray-700">{t.gdpr.dpa}</Link>
          </div>
```

- [ ] **Step 3: TypeScript check**

```bash
cd frontend && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Landing.tsx
git commit -m "feat(features): link Features page from Landing CTA and footer"
```

---

## Task 8: Final verification

**Files:** none

- [ ] **Step 1: Run a production build**

```bash
cd frontend && npm run build
```

Expected: build succeeds, exit 0. `dist/` directory populated.

- [ ] **Step 2: Manual visual check — landing page**

In a browser, open `http://localhost:5173/`. Verify:

- The "See It in Action" section still shows the demo video
- Below the video, a centered "Explore every manager screen →" button is visible
- Clicking it navigates to `/features`
- The footer contains a "Manager tour" link that also navigates to `/features`

- [ ] **Step 3: Manual visual check — features page (desktop, ≥ lg breakpoint)**

On `/features`:

- Hero title and intro render correctly
- Left rail TOC is sticky, shows all 15 page titles
- Clicking a TOC link scrolls smoothly to that screen's section
- Each of 15 sections shows a real screenshot (not a fallback placeholder) plus title + description
- Sections alternate image-left / image-right
- Bottom CTA renders and "← Back to home" returns to `/`

- [ ] **Step 4: Manual visual check — features page (mobile, < lg breakpoint)**

Resize browser to ~375px wide (DevTools mobile preview). Verify:

- TOC is hidden
- Sections stack vertically (screenshot above text)
- No horizontal scroll
- Top nav remains usable

- [ ] **Step 5: Manual visual check — i18n**

Change the language via the LanguageSelector to any non-English option (e.g., Español). On `/features`, confirm the page still renders (with English text, as expected per spec). Switch back to English.

- [ ] **Step 6: Final commit (only if any fixes were made during verification)**

If steps 1–5 surfaced any issues that required code changes, fix them and commit. If everything passed, skip this step.

---

## Self-Review

**Spec coverage:**
- ✅ Public `/features` route — Task 6
- ✅ Landing page CTA in `#demo` — Task 7
- ✅ Footer link — Task 7
- ✅ Playwright script + 15 screenshots — Tasks 1–2
- ✅ English i18n keys (features namespace + landing additions) — Task 3
- ✅ 18 non-English placeholders — Task 4
- ✅ Features.tsx with TOC + alternating layout + fallback placeholder — Task 5
- ✅ TypeScript typecheck verification — every task
- ✅ Production build verification — Task 8

**Placeholder scan:** No TBDs, no "handle errors appropriately", no "similar to above" references. Every code block contains the actual code an engineer types.

**Type consistency:**
- `Slug` type derived from `PAGE_SLUGS` tuple in Features.tsx — used consistently within that file
- i18n keys `features.pages.<slug>` match the same 15 slugs used in the Playwright script and the Features.tsx tuple
- Translation type is auto-derived from `en.ts` via `typeof import("./en").default`, so adding to en.ts automatically extends the type globally (verified via the deliberate Task 3 → Task 4 typecheck flow)
