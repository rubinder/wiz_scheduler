#!/usr/bin/env node
// One-off helper: captures /manager/schedule AFTER running the
// rotation strategy across both seeded stores, so the screenshot
// shows a fully-filled weekly schedule instead of an empty page.
//
// Prerequisites are the same as capture-screenshots.mjs PLUS
// EmployeeAvailability for the current week in UTC 09:00-17:00
// (see seed work in the dev session that ran this).
//
// Output: frontend/public/screenshots/schedule.png (1920x1080 @2x)

import { chromium } from "playwright";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(__dirname, "..", "public", "screenshots", "schedule.png");

const FRONTEND_URL = process.env.FRONTEND_URL || "http://localhost:5173";
const MANAGER_EMAIL = process.env.MANAGER_EMAIL || "abc@example.com";
const MANAGER_PASSWORD = process.env.MANAGER_PASSWORD || "example";
const START = process.env.SCHEDULE_START || "2026-05-11";
const END = process.env.SCHEDULE_END || "2026-05-17";

async function main() {
  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    deviceScaleFactor: 2,
  });
  const page = await context.newPage();

  await page.goto(`${FRONTEND_URL}/login`, { waitUntil: "networkidle" });
  await page.fill('input[type="email"]', MANAGER_EMAIL);
  await page.fill('input[type="password"]', MANAGER_PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/manager\/dashboard/, { timeout: 15000 });

  await page.goto(`${FRONTEND_URL}/manager/schedule`, { waitUntil: "networkidle" });
  await page.waitForSelector("input[type='date']");

  const dateInputs = page.locator("input[type='date']");
  await dateInputs.nth(0).fill(START);
  await dateInputs.nth(1).fill(END);

  await page.click("button:has-text('Normal Generate (with Strategies)')");
  await page.waitForSelector("button:has-text('Normal Generate for')");
  await page.click("button:has-text('Normal Generate for')");

  await page.waitForFunction(
    () => !document.body.textContent.includes("No shifts generated"),
    { timeout: 20000 },
  ).catch(() => {});
  await page.waitForTimeout(1500);

  await page.screenshot({ path: OUT, fullPage: true });
  console.log(`saved ${OUT}`);
  await browser.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
