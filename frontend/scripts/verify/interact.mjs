import { chromium } from "playwright";

const BASE = process.env.VERIFY_BASE ?? "http://localhost:5173";
const failures = [];

function check(name, cond) {
  if (cond) console.log(`  ok   ${name}`);
  else { console.log(`  FAIL ${name}`); failures.push(name); }
}

const browser = await chromium.launch();
const page = await browser.newPage();
const posted = [];
page.on("request", (r) => {
  if (r.method() === "POST") posted.push(new URL(r.url()).pathname);
});

console.log("login form");
await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
await page.fill('input[type="email"]', "abc@example.com");
await page.fill('input[type="password"]', "wrong-on-purpose");
await page.click('button[type="submit"]');
await page.waitForTimeout(1500);
check("login POSTs to the auth endpoint",
  posted.some((p) => p.includes("/auth/")));

console.log("register form");
posted.length = 0;
await page.goto(`${BASE}/register`, { waitUntil: "networkidle" });
check("register renders an email field",
  await page.locator('input[type="email"]').count() > 0);
check("register renders a submit button",
  await page.locator('button[type="submit"]').count() > 0);

console.log("forgot-password form");
await page.goto(`${BASE}/forgot-password`, { waitUntil: "networkidle" });
check("forgot-password renders an email field",
  await page.locator('input[type="email"]').count() > 0);

console.log("landing navigation");
await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
for (const href of ["/login", "/register", "/features",
                    "/privacy-policy", "/terms", "/dpa"]) {
  check(`landing links to ${href}`,
    await page.locator(`a[href="${href}"]`).count() > 0);
}
for (const anchor of ["#pricing", "#demo"]) {
  check(`landing anchor ${anchor} has a target`,
    await page.locator(`${anchor}`).count() > 0);
}

console.log("keyboard focus is visible");
await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
await page.keyboard.press("Tab");
const outline = await page.evaluate(() => {
  const el = document.activeElement;
  if (!el) return null;
  const s = getComputedStyle(el);
  return `${s.outlineStyle}|${s.outlineWidth}|${s.boxShadow}`;
});
check("first tab stop has a visible focus indicator",
  outline !== null && !/^none\|0px\|none$/.test(outline));

await browser.close();
console.log(failures.length ? `\n${failures.length} FAILURES` : "\nall checks passed");
process.exit(failures.length ? 1 : 0);
