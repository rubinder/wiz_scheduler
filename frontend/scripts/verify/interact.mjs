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

// Abort every API write before it leaves the browser. The attempt is still
// recorded, so we prove the handler fired without creating accounts,
// sending mail, or touching a backend that may not even be running.
const attempted = [];
await page.route("**/*", (route) => {
  const req = route.request();
  if (req.method() === "POST") {
    attempted.push(new URL(req.url()).pathname);
    return route.abort();
  }
  return route.continue();
});

console.log("login form");
await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
await page.fill('input[type="email"]', "abc@example.com");
await page.fill('input[type="password"]', "wrong-on-purpose");
await page.click('button[type="submit"]');
await page.waitForTimeout(1500);
check("login POSTs to the auth endpoint",
  attempted.some((p) => p.includes("/auth/")));

console.log("register form");
await page.goto(`${BASE}/register`, { waitUntil: "networkidle" });
check("register renders an email field",
  await page.locator('input[type="email"]').count() > 0);
check("register renders a submit button",
  await page.locator('button[type="submit"]').count() > 0);

// Register's submit is gated on GDPR consent checkboxes. Toggling them and
// watching the button's disabled state proves React state and handlers are
// live -- without POSTing, which would create accounts on every run.
const submit = page.locator('button[type="submit"]').first();
const boxes = page.locator('input[type="checkbox"]');
const boxCount = await boxes.count();
check("register has consent checkboxes gating submit", boxCount > 0);

if (boxCount > 0) {
  const disabledBefore = await submit.isDisabled();
  for (let i = 0; i < boxCount; i++) await boxes.nth(i).check();
  await page.waitForTimeout(250);
  const disabledAfter = await submit.isDisabled();
  console.log(`  (disabledBefore=${disabledBefore} disabledAfter=${disabledAfter})`);
  // A live form changes state when consent is given. A dead one never does.
  check("register submit responds to consent state (gate is wired)",
    disabledBefore !== disabledAfter || disabledBefore === false);

  // The button being enabled isn't enough to reach onSubmit: the browser's
  // native required-field validation blocks the click before our JS handler
  // ever runs while fullName/password/companyName are still empty. Fill
  // them with throwaway values -- never sent, the route above aborts every
  // POST -- so the click can actually reach onSubmit.
  await page.locator('input[type="text"]').nth(0).fill("Verify Probe");
  await page.locator('input[type="email"]').first().fill("verify-probe@example.com");
  await page.locator('input[type="password"]').first().fill("verify-probe-password");
  await page.locator('input[type="text"]').nth(1).fill("Verify Probe Co");

  attempted.length = 0;
  await submit.click({ timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(1000);
  // The gate opening is not enough — onSubmit must actually fire.
  check("register submit fires a request (onSubmit is attached)",
    attempted.length > 0);
}

console.log("forgot-password form");
await page.goto(`${BASE}/forgot-password`, { waitUntil: "networkidle" });
check("forgot-password renders an email field",
  await page.locator('input[type="email"]').count() > 0);
await submitProves(page, "/forgot-password", "forgot-password");

console.log("landing navigation");
await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
for (const href of ["/login", "/register", "/features",
                    "/privacy-policy", "/terms", "/dpa"]) {
  // The landing page embeds a live YouTube iframe (#demo) that keeps
  // generating background network traffic (ads/telemetry) indefinitely, so
  // "networkidle" is not a safe wait condition here -- it can hang well
  // past any reasonable timeout on repeated visits. Wait on the URL itself,
  // which is what the assertion below actually checks.
  await page.locator(`a[href="${href}"]`).first().click();
  await page.waitForURL((url) => new URL(url).pathname === href, { timeout: 5000 }).catch(() => {});
  check(`landing link ${href} navigates`,
    new URL(page.url()).pathname === href);
  await page.goBack();
  await page.waitForURL((url) => new URL(url).pathname === "/", { timeout: 5000 }).catch(() => {});
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
