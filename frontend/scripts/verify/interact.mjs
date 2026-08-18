import { chromium } from "playwright";

const BASE = process.env.VERIFY_BASE ?? "http://localhost:5173";
const failures = [];

function check(name, cond) {
  if (cond) console.log(`  ok   ${name}`);
  else { console.log(`  FAIL ${name}`); failures.push(name); }
}

async function submitProves(page, path, label) {
  const posts = [];
  const onReq = (r) => { if (r.method() === "POST") posts.push(r.url()); };
  page.on("request", onReq);
  await page.goto(`${BASE}${path}`, { waitUntil: "networkidle" });
  const before = await page.locator("body").innerText();
  await page.fill('input[type="email"]', "verify-probe@example.com");
  // Bounded + caught: some forms disable submit until other required
  // fields (e.g. consent checkboxes) are set, which would otherwise hang
  // this click for the full default timeout and crash the whole run. A
  // click that never becomes actionable still correctly fails the check
  // below (no POST, no visible change) — this only stops it from taking
  // the rest of the suite down with it.
  await page.click('button[type="submit"]', { timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(1500);
  const after = await page.locator("body").innerText();
  page.off("request", onReq);
  // A live handler either talks to the server or renders feedback.
  check(`${label} submit runs a handler (POST or visible feedback)`,
    posts.length > 0 || after !== before);
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
await page.goto(`${BASE}/register`, { waitUntil: "networkidle" });
check("register renders an email field",
  await page.locator('input[type="email"]').count() > 0);
check("register renders a submit button",
  await page.locator('button[type="submit"]').count() > 0);
await submitProves(page, "/register", "register");

console.log("forgot-password form");
await page.goto(`${BASE}/forgot-password`, { waitUntil: "networkidle" });
check("forgot-password renders an email field",
  await page.locator('input[type="email"]').count() > 0);
await submitProves(page, "/forgot-password", "forgot-password");

console.log("landing navigation");
await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
for (const href of ["/login", "/register", "/features",
                    "/privacy-policy", "/terms", "/dpa"]) {
  await page.locator(`a[href="${href}"]`).first().click();
  await page.waitForLoadState("networkidle");
  check(`landing link ${href} navigates`,
    new URL(page.url()).pathname === href);
  await page.goBack({ waitUntil: "networkidle" });
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
