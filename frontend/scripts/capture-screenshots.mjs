#!/usr/bin/env node
// Capture screenshots of all 25 manager pages.
//
// One-time setup (after a fresh `npm install`):
//   cd frontend && npx playwright install chromium
//
// Prerequisites for each run:
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
  { slug: "team", path: "/manager/team" },
  { slug: "hour-restrictions", path: "/manager/hour-restrictions" },
  { slug: "day-blackouts", path: "/manager/day-blackouts" },
  { slug: "day-preferences", path: "/manager/day-preferences" },
  { slug: "hour-range-preferences", path: "/manager/hour-range-preferences" },
  { slug: "frequency-caps", path: "/manager/frequency-caps" },
  { slug: "employee-onboarding", path: "/manager/employee-onboarding" },
  { slug: "employee-availability", path: "/manager/employee-availability" },
  { slug: "employee-association", path: "/manager/employee-association" },
  { slug: "shift-templates", path: "/manager/shift-templates" },
  { slug: "check-in-qr", path: "/manager/check-in-qr" },
  { slug: "check-in-report", path: "/manager/check-in-report" },
  { slug: "payroll", path: "/manager/payroll" },
  { slug: "special-hours", path: "/manager/special-hours" },
  { slug: "schedule", path: "/manager/schedule" },
  { slug: "export-schedules", path: "/manager/export-schedules" },
  { slug: "approved-schedules", path: "/manager/approved-schedules" },
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
      // Payroll defaults its range to the current week; the seeded demo's
      // approved/checked-in shifts sit in the week of 2026-08-31, so point
      // the range there or the screenshot shows an empty page.
      if (slug === "payroll") {
        const dateInputs = page.locator('input[type="date"]');
        await dateInputs.nth(0).fill("2026-08-31");
        await dateInputs.nth(1).fill("2026-09-07");
        await page.waitForTimeout(500);
      }
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
  console.log("[done] all 25 screenshots captured");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
