import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = process.env.VERIFY_BASE ?? "http://localhost:5173";
const tag = (process.argv.includes("--tag")
  ? process.argv[process.argv.indexOf("--tag") + 1]
  : "run");

const VIEWPORTS = [
  { name: "mobile", width: 390, height: 844 },
  { name: "tablet", width: 768, height: 1024 },
  { name: "desktop", width: 1440, height: 900 },
];

// en = baseline. ru = Cyrillic (no display face). ar = RTL.
// hi = Devanagari :lang override. ja = CJK system fallback.
const LOCALES = ["en", "ru", "ar", "hi", "ja"];

const ROUTES = [
  ["landing", "/"],
  ["features", "/features"],
  ["login", "/login"],
  ["register", "/register"],
  ["forgot", "/forgot-password"],
  ["terms", "/terms"],
];

const outDir = `.verify/${tag}`;
mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch();
let shots = 0;

for (const vp of VIEWPORTS) {
  for (const locale of LOCALES) {
    // Only the desktop viewport is worth shooting in every locale;
    // mobile/tablet in en only keeps the matrix reviewable.
    if (vp.name !== "desktop" && locale !== "en") continue;

    const ctx = await browser.newContext({
      viewport: { width: vp.width, height: vp.height },
      reducedMotion: "reduce", // deterministic: no mid-animation captures
    });
    const page = await ctx.newPage();
    await page.addInitScript(
      (l) => window.localStorage.setItem("lang", l),
      locale,
    );

    for (const [name, path] of ROUTES) {
      await page.goto(`${BASE}${path}`, { waitUntil: "networkidle" });
      await page.screenshot({
        path: `${outDir}/${name}-${vp.name}-${locale}.png`,
        fullPage: true,
      });
      shots++;
    }
    await ctx.close();
  }
}

await browser.close();
console.log(`${shots} screenshots -> ${outDir}`);
