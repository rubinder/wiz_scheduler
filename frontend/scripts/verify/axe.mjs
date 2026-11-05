import { chromium } from "playwright";
import AxeBuilder from "@axe-core/playwright";

const BASE = process.env.VERIFY_BASE ?? "http://localhost:5173";
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];
const paths = process.argv.slice(2).filter((a) => a.startsWith("/"));
if (!paths.length) paths.push("/");

const browser = await chromium.launch();
let total = 0;
try {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  if (process.env.VERIFY_EMAIL && paths.some((p) => /^\/(manager|employee)/.test(p))) {
    try {
      await page.goto(`${BASE}/login`);
      await page.getByLabel(/email/i).fill(process.env.VERIFY_EMAIL);
      await page.getByLabel(/password/i).fill(process.env.VERIFY_PASSWORD ?? "");
      await page.getByRole("button", { name: /log in|sign in/i }).click();
      await page.waitForURL(/\/(manager|employee)/, { timeout: 15000 });
    } catch (e) {
      console.error(`axe: login failed for ${process.env.VERIFY_EMAIL} at ${BASE}/login: ${e.message}`);
      process.exitCode = 2;
      throw e;
    }
  }
  for (const p of paths) {
    await page.goto(`${BASE}${p}`, { waitUntil: "networkidle" });
    const { violations } = await new AxeBuilder({ page }).withTags(TAGS).analyze();
    for (const v of violations) {
      total += 1;
      console.log(JSON.stringify({ path: p, id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.slice(0, 3).map((n) => n.target.join(" ")) }));
    }
    console.log(`${p}: ${violations.length} violation(s)`);
  }
  process.exitCode = total ? 1 : 0;
} catch (e) {
  if (!process.exitCode) console.error(`axe: ${e.message}`);
  process.exitCode = 2;
} finally {
  await browser.close();
}
