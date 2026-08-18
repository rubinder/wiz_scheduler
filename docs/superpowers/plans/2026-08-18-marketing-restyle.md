# Marketing & Auth Restyle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the default cream/sage/gold glassmorphism on the public marketing and auth surface with "The Rota" — an identity built from the printed back-of-house weekly schedule — changing zero behaviour.

**Architecture:** A new palette and three self-hosted typefaces land in `tailwind.config.ts` and `index.css`, exposed through a new `marketing` namespace in `theme.ts` so the ~30 logged-in pages keep their current tokens untouched. Five new components in `src/components/marketing/` supply the shared chrome, the auth shell, and the signature hero. Pages are then rewritten JSX-and-className only. `motion` powers one orchestrated hero sequence; `React.lazy` keeps its cost off the app bundle.

**Tech Stack:** React 18, TypeScript 5.6 (strict), Vite 6, Tailwind 3.4, `motion` (new), Playwright 1.59 (already installed, currently unused).

**Spec:** `docs/superpowers/specs/2026-08-18-marketing-restyle-design.md` — read it first; this plan argues from it.

## Global Constraints

Every task's requirements implicitly include this section.

- **No behaviour changes.** Do not edit any `useState`, `useEffect`, `useRef`, event handler, API call, route path, or form field name. Edits are limited to JSX structure and `className`. The sole exceptions are `App.tsx` (lazy routes) and the new components.
- **No i18n key changes.** Copy lives in 19 locale files (`src/i18n/`). Never add, rename, or remove a key. Never change an English string. The only editable text is hardcoded literals absent from the i18n layer.
- **The four editable literals** are the overage badges in `Landing.tsx` at lines 255, 275, 295, 315: `AI` / `GEN` / `DB` / `#` → `AI` / `RUNS` / `DATA` / `TEAM`.
- **No `.glass-*` classes on any marketing or auth page.** They stay defined in `index.css` because the app still uses them. No `backdrop-blur`, no `bg-white/60`, no radial-gradient `body` background on these routes.
- **Logical properties only** for directional spacing: `ps-`/`pe-`, `ms-`/`me-`, `border-s`/`border-e`, `start-`/`end-`, `text-start`/`text-end`. Never `pl-`/`pr-`/`ml-`/`mr-`/`left-`/`right-`/`text-left`/`text-right`. RTL is live — `LanguageContext.tsx:78` sets `document.documentElement.dir`.
- **Palette, exact values:** `ink #1A1815`, `newsprint #E9E4D8`, `paper #F5F2EA`, `rule #C9C0B0`, `marker #FF5A1F`, `clear #1F7A5C`.
- **`marker` is prohibited on small body text** (≈3:1 on `newsprint`). Permitted for large display type, rules, and fills. Once per viewport.
- **Only one new runtime dependency is authorised:** `motion`. Do not install anything else. Fonts are self-hosted static files, not a package.
- **`npm run build` must pass** (`tsc` strict + Vite) at the end of every task.

---

## File Structure

**Created**

| Path | Responsibility |
|---|---|
| `frontend/scripts/verify/shoot.mjs` | Playwright screenshot matrix — viewports × locales |
| `frontend/scripts/verify/interact.mjs` | Playwright interaction pass — proves forms still work |
| `frontend/scripts/verify/contrast.mjs` | WCAG AA checker over the token pairs |
| `frontend/scripts/fetch-fonts.mjs` | Mirrors Google Fonts woff2 into `public/fonts/`, emits `@font-face` CSS |
| `frontend/public/fonts/*.woff2` | Self-hosted subsets |
| `frontend/src/styles/fonts.css` | Generated `@font-face` blocks + `:lang()` overrides |
| `frontend/src/components/marketing/SectionRule.tsx` | Ruled section divider + heading; the page's structural grammar |
| `frontend/src/components/marketing/MarketingNav.tsx` | Shared public nav |
| `frontend/src/components/marketing/MarketingFooter.tsx` | Shared public footer |
| `frontend/src/components/marketing/AuthLayout.tsx` | Split shell for all 6 auth pages |
| `frontend/src/components/marketing/RotaHero.tsx` | The signature element |
| `frontend/src/components/marketing/rotaData.ts` | The fixture the hero renders; keeps data out of the view |

**Modified:** `tailwind.config.ts`, `src/index.css`, `src/theme.ts`, `index.html`, `package.json`, `src/App.tsx`, `src/pages/Landing.tsx`, `Features.tsx`, `Login.tsx`, `Register.tsx`, `ForgotPassword.tsx`, `ResetPassword.tsx`, `AcceptInvite.tsx`, `AcceptManagerInvite.tsx`, `PrivacyPolicy.tsx`, `TermsOfService.tsx`, `DataProcessingAgreement.tsx`

---

## Task 1: Verification harness

There is no frontend test suite — `tests/` is 30 pytest files, all backend, and Playwright is installed but unused. Build the harness **first** so every later task has a real test cycle instead of an opinion.

**Files:**
- Create: `frontend/scripts/verify/shoot.mjs`, `frontend/scripts/verify/interact.mjs`, `frontend/scripts/verify/contrast.mjs`
- Modify: `frontend/package.json` (scripts only), `frontend/.gitignore`

**Interfaces:**
- Consumes: nothing.
- Produces: `npm run verify:shoot -- --tag <name>` writing PNGs to `frontend/.verify/<name>/`; `npm run verify:interact` exiting non-zero on failure; `npm run verify:contrast` exiting non-zero on a WCAG AA failure.

- [ ] **Step 1: Add the npm scripts**

In `frontend/package.json`, add to `"scripts"`:

```json
"verify:shoot": "node scripts/verify/shoot.mjs",
"verify:interact": "node scripts/verify/interact.mjs",
"verify:contrast": "node scripts/verify/contrast.mjs"
```

- [ ] **Step 2: Ignore the output directory**

Append to `frontend/.gitignore`:

```
.verify/
```

- [ ] **Step 3: Write the screenshot matrix**

Create `frontend/scripts/verify/shoot.mjs`:

```js
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
```

- [ ] **Step 4: Verify the language key matches the app**

Run: `grep -rn "localStorage" frontend/src/i18n/LanguageContext.tsx`
Expected: a `getItem`/`setItem` call naming the persistence key. If it is not `"lang"`, correct the `addInitScript` line in `shoot.mjs` to use the real key. Do not change `LanguageContext.tsx`.

- [ ] **Step 5: Write the interaction pass**

Create `frontend/scripts/verify/interact.mjs`. This is the evidence that the restyle changed nothing:

```js
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
```

- [ ] **Step 6: Write the contrast checker**

Create `frontend/scripts/verify/contrast.mjs`:

```js
const TOKENS = {
  ink: "#1A1815",
  newsprint: "#E9E4D8",
  paper: "#F5F2EA",
  rule: "#C9C0B0",
  marker: "#FF5A1F",
  clear: "#1F7A5C",
};

// [foreground, background, minimum ratio, why]
const PAIRS = [
  ["ink", "newsprint", 4.5, "body text on page ground"],
  ["ink", "paper", 4.5, "body text on raised surfaces"],
  ["clear", "paper", 4.5, "compliant/success text on the receipt"],
  ["clear", "newsprint", 4.5, "compliant/success text on page ground"],
  ["marker", "newsprint", 3.0, "large display type and fills only"],
  ["marker", "ink", 3.0, "accent on the dark demo band"],
  ["newsprint", "ink", 4.5, "reversed text on the dark demo band"],
  ["rule", "newsprint", 1.3, "grid lines must be visible, not invisible"],
];

const srgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
const lum = (hex) =>
  srgb(hex)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
    .reduce((a, c, i) => a + c * [0.2126, 0.7152, 0.0722][i], 0);
const ratio = (a, b) => {
  const [l1, l2] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (l1 + 0.05) / (l2 + 0.05);
};

let failed = 0;
for (const [fg, bg, min, why] of PAIRS) {
  const r = ratio(TOKENS[fg], TOKENS[bg]);
  const ok = r >= min;
  if (!ok) failed++;
  console.log(
    `${ok ? "ok  " : "FAIL"}  ${fg} on ${bg}  ${r.toFixed(2)}:1 (need ${min}) — ${why}`,
  );
}
console.log(failed ? `\n${failed} contrast failures` : "\nall pairs pass");
process.exit(failed ? 1 : 0);
```

- [ ] **Step 7: Run the contrast checker — it needs no dev server**

Run: `cd frontend && npm run verify:contrast`
Expected: every pair prints `ok`. **If any pair fails, stop and report it — do not proceed and do not quietly adjust a hex value.** The palette is approved in the spec; a failure is a design decision for the user, not an implementation detail.

- [ ] **Step 8: Capture the "before" baseline**

Start the dev server (`npm run dev`), wait for `:5173`, then run:

```bash
cd frontend && npm run verify:shoot -- --tag before && npm run verify:interact
```

Expected: 30 screenshots in `.verify/before/`, and `verify:interact` exits 0. **`verify:interact` passing here is the contract.** It must still pass at the end of every subsequent task; that is what "functionality unchanged" means in this plan.

- [ ] **Step 9: Commit**

```bash
git add frontend/scripts/verify frontend/package.json frontend/.gitignore
git commit -m "test(frontend): add Playwright screenshot, interaction, and contrast verification harness"
```

---

## Task 2: Self-hosted typography

**Files:**
- Create: `frontend/scripts/fetch-fonts.mjs`, `frontend/src/styles/fonts.css`, `frontend/public/fonts/*.woff2`
- Modify: `frontend/tailwind.config.ts`, `frontend/src/index.css`, `frontend/index.html`

**Interfaces:**
- Consumes: Task 1's `verify:shoot`.
- Produces: Tailwind families `font-display`, `font-body`, `font-data`; CSS vars `--font-display`, `--font-body`, `--font-data`.

**Verified font facts — do not re-derive:**

| Face | Role | Subsets available |
|---|---|---|
| Archivo (variable, `wdth` 62–125, `wght` 100–900) | display | latin, latin-ext, vietnamese — **no Cyrillic** |
| Source Sans 3 (variable, `wght` 200–900) | body | latin, latin-ext, vietnamese, cyrillic, cyrillic-ext, greek, greek-ext |
| IBM Plex Mono (400/500/600) | data | latin, latin-ext, vietnamese, cyrillic, cyrillic-ext |

All three are SIL Open Font License; self-hosting is permitted.

**Resolved decision (amends the spec's open question):** Archivo carries no Cyrillic, so the `ru` locale would render headlines in a system font. Instead, `:lang(ru)` maps the **display role to Source Sans 3 at weight 900**. It is already loaded, covers Cyrillic fully, and staying in-family preserves far more coherence than dropping to Arial.

- [ ] **Step 1: Write the font mirroring script**

Create `frontend/scripts/fetch-fonts.mjs`:

```js
import { mkdirSync, writeFileSync } from "node:fs";

const UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

const FAMILIES = [
  { css: "Archivo:wdth,wght@62..125,100..900", local: "Archivo" },
  { css: "Source+Sans+3:wght@200..900", local: "SourceSans3" },
  { css: "IBM+Plex+Mono:wght@400;500;600", local: "IBMPlexMono" },
];

mkdirSync("public/fonts", { recursive: true });
let out = "/* Generated by scripts/fetch-fonts.mjs — do not edit by hand. */\n";

for (const fam of FAMILIES) {
  const res = await fetch(
    `https://fonts.googleapis.com/css2?family=${fam.css}&display=swap`,
    { headers: { "User-Agent": UA } },
  );
  if (!res.ok) throw new Error(`${fam.local}: CSS fetch failed ${res.status}`);
  let css = await res.text();

  const urls = [...css.matchAll(/url\((https:\/\/fonts\.gstatic\.com\/[^)]+)\)/g)]
    .map((m) => m[1]);
  const seen = new Map();

  for (const url of urls) {
    if (seen.has(url)) continue;
    const file = `${fam.local}-${seen.size}.woff2`;
    const bin = await fetch(url, { headers: { "User-Agent": UA } });
    if (!bin.ok) throw new Error(`${file}: download failed ${bin.status}`);
    writeFileSync(`public/fonts/${file}`, Buffer.from(await bin.arrayBuffer()));
    seen.set(url, file);
    console.log(`  ${file}`);
  }

  for (const [url, file] of seen) css = css.replaceAll(url, `/fonts/${file}`);
  out += `\n/* ===== ${fam.local} ===== */\n${css}`;
}

mkdirSync("src/styles", { recursive: true });
writeFileSync("src/styles/fonts.css", out);
console.log("wrote src/styles/fonts.css");
```

- [ ] **Step 2: Run it**

Run: `cd frontend && node scripts/fetch-fonts.mjs`
Expected: woff2 files listed for all three families, then `wrote src/styles/fonts.css`.
If the network is unavailable, **stop and report it** — do not fall back to a CDN `<link>`. The spec rejected third-party font loading over the GDPR consideration raised by the DPA and privacy pages.

- [ ] **Step 3: Confirm the mirror is real, not empty**

Run: `ls -la frontend/public/fonts/ && grep -c "@font-face" frontend/src/styles/fonts.css`
Expected: at least 12 `.woff2` files, each larger than 5 KB, and a `@font-face` count matching them.

- [ ] **Step 4: Append the script-coverage overrides**

Append to `frontend/src/styles/fonts.css`:

```css
/* ── Role variables ─────────────────────────────────────────────
   Latin / Latin-ext / Vietnamese get the full three-face system.  */
:root {
  --font-display: "Archivo", "Source Sans 3", system-ui, sans-serif;
  --font-body: "Source Sans 3", system-ui, sans-serif;
  --font-data: "IBM Plex Mono", ui-monospace, "SFMono-Regular", monospace;
  --display-stretch: 75%;
  --display-tracking: -0.02em;
}

/* Cyrillic: Archivo has no Cyrillic coverage. Keep the display role
   in-family on Source Sans 3 at 900 rather than dropping to a system
   face, and stop condensing — Source Sans 3 has no wdth axis. */
:root:lang(ru) {
  --font-display: "Source Sans 3", system-ui, sans-serif;
  --display-stretch: 100%;
  --display-tracking: -0.005em;
}

/* Scripts with no self-hosted coverage fall through to per-script
   system stacks. Condensing and negative tracking are cancelled:
   they damage legibility in these scripts. Readability outranks
   identity — never the reverse. */
:root:lang(ar), :root:lang(ur) {
  --font-display: "SF Arabic", "Geeza Pro", "Segoe UI", "Noto Sans Arabic", sans-serif;
  --font-body: "SF Arabic", "Geeza Pro", "Segoe UI", "Noto Sans Arabic", sans-serif;
  --display-stretch: 100%;
  --display-tracking: 0;
}
:root:lang(hi), :root:lang(mr) {
  --font-display: "Kohinoor Devanagari", "Nirmala UI", "Noto Sans Devanagari", sans-serif;
  --font-body: "Kohinoor Devanagari", "Nirmala UI", "Noto Sans Devanagari", sans-serif;
  --display-stretch: 100%;
  --display-tracking: 0;
}
:root:lang(bn) {
  --font-display: "Kohinoor Bangla", "Nirmala UI", "Noto Sans Bengali", sans-serif;
  --font-body: "Kohinoor Bangla", "Nirmala UI", "Noto Sans Bengali", sans-serif;
  --display-stretch: 100%;
  --display-tracking: 0;
}
:root:lang(ta) {
  --font-display: "Tamil Sangam MN", "Nirmala UI", "Noto Sans Tamil", sans-serif;
  --font-body: "Tamil Sangam MN", "Nirmala UI", "Noto Sans Tamil", sans-serif;
  --display-stretch: 100%;
  --display-tracking: 0;
}
:root:lang(te) {
  --font-display: "Kohinoor Telugu", "Nirmala UI", "Noto Sans Telugu", sans-serif;
  --font-body: "Kohinoor Telugu", "Nirmala UI", "Noto Sans Telugu", sans-serif;
  --display-stretch: 100%;
  --display-tracking: 0;
}
:root:lang(ja) {
  --font-display: "Hiragino Sans", "Yu Gothic", "Noto Sans JP", sans-serif;
  --font-body: "Hiragino Sans", "Yu Gothic", "Noto Sans JP", sans-serif;
  --display-stretch: 100%;
  --display-tracking: 0;
}
:root:lang(zh) {
  --font-display: "PingFang SC", "Microsoft YaHei", "Noto Sans SC", sans-serif;
  --font-body: "PingFang SC", "Microsoft YaHei", "Noto Sans SC", sans-serif;
  --display-stretch: 100%;
  --display-tracking: 0;
}

/* The display role is condensed only where a wdth axis exists. */
.font-display {
  font-stretch: var(--display-stretch);
  letter-spacing: var(--display-tracking);
}

/* Numbers must align. Every figure in this product is tabular. */
.font-data, table tbody td, .tabular {
  font-variant-numeric: tabular-nums;
}
```

- [ ] **Step 5: Import the stylesheet**

At the very top of `frontend/src/index.css`, **above** the `@tailwind` directives:

```css
@import "./styles/fonts.css";
```

- [ ] **Step 6: Register the families with Tailwind**

In `frontend/tailwind.config.ts`, inside `theme.extend`, add alongside the existing `colors` key:

```ts
      fontFamily: {
        display: ["var(--font-display)"],
        body: ["var(--font-body)"],
        data: ["var(--font-data)"],
      },
```

- [ ] **Step 7: Preload the two faces above the fold**

In `frontend/index.html`, immediately after the `<link rel="icon" ...>` line, add preloads for the **latin** subset of Archivo and Source Sans 3. Read the generated filenames out of `src/styles/fonts.css` — the `latin` block is the last `@font-face` for each family:

```html
    <link rel="preload" as="font" type="font/woff2" crossorigin
          href="/fonts/Archivo-LATIN.woff2" />
    <link rel="preload" as="font" type="font/woff2" crossorigin
          href="/fonts/SourceSans3-LATIN.woff2" />
```

Replace `Archivo-LATIN.woff2` and `SourceSans3-LATIN.woff2` with the real generated filenames. Do not preload IBM Plex Mono — it is below the fold on every page.

- [ ] **Step 8: Prove the fonts actually load**

Start the dev server, then run:

```bash
cd frontend && node -e '
import("playwright").then(async ({ chromium }) => {
  const b = await chromium.launch();
  const p = await b.newPage();
  await p.goto("http://localhost:5173/login", { waitUntil: "networkidle" });
  const fonts = await p.evaluate(() =>
    [...document.fonts].map(f => `${f.family} ${f.status}`));
  console.log([...new Set(fonts)].join("\n"));
  await b.close();
});'
```

Expected: `Archivo loaded`, `Source Sans 3 loaded`. If any reads `unloaded`, the `@font-face` URLs are wrong — fix `fonts.css`, do not proceed.

- [ ] **Step 9: Build**

Run: `cd frontend && npm run build`
Expected: exits 0.

- [ ] **Step 10: Commit**

```bash
git add frontend/scripts/fetch-fonts.mjs frontend/src/styles/fonts.css \
        frontend/public/fonts frontend/src/index.css \
        frontend/tailwind.config.ts frontend/index.html
git commit -m "feat(frontend): self-host Archivo, Source Sans 3, IBM Plex Mono with per-script fallbacks"
```

---

## Task 3: Palette tokens

**Files:**
- Modify: `frontend/tailwind.config.ts`, `frontend/src/theme.ts`

**Interfaces:**
- Consumes: Task 2's font families.
- Produces: Tailwind colours `ink`, `newsprint`, `paper`, `rule`, `marker`, `clear`; and `marketing` exported from `theme.ts` with the exact keys listed in Step 2. **Every later task imports `marketing` and uses nothing else for colour.**

- [ ] **Step 1: Add the colours**

In `frontend/tailwind.config.ts`, inside `theme.extend.colors`, **add** these alongside the existing `cream` / `sage` / `accent` keys. Do not remove the existing ones — the ~30 app pages still use them:

```ts
        ink: "#1A1815",
        newsprint: "#E9E4D8",
        paper: "#F5F2EA",
        rule: "#C9C0B0",
        marker: "#FF5A1F",
        clear: "#1F7A5C",
```

- [ ] **Step 2: Add the `marketing` namespace**

Append to `frontend/src/theme.ts`, before the `export const theme = {` block:

```ts
// ── Marketing & auth surface ("The Rota") ───────────────────────
// Deliberately separate from the app tokens above: the public
// surface is restyled first and the app inherits this later.
// No .glass-* class may appear on a page that imports this.

export const marketing = {
  /** Page ground. Applied by the marketing shell, not by body. */
  page: "bg-newsprint text-ink",
  /** Raised surface: form panels, the hero grid, the receipt. */
  surface: "bg-paper",
  /** The dark band — used exactly once per page, on the demo. */
  inverse: "bg-ink text-newsprint",

  text: {
    display: "font-display text-ink",
    body: "font-body text-ink",
    /** Secondary prose. 70% ink keeps AA on newsprint. */
    muted: "font-body text-ink/70",
    /** Eyebrows, captions, table headers. */
    meta: "font-data text-ink/60 uppercase tracking-[0.14em] text-xs",
    /** Figures, times, prices. Always tabular. */
    data: "font-data tabular-nums text-ink",
    accent: "text-marker",
    clear: "text-clear",
  },

  rule: {
    /** Standard 1px divider. */
    line: "border-rule",
    /** Section boundary — heavier, structural. */
    heavy: "border-ink/25",
    /** Grid interior lines in the rota and the receipt. */
    grid: "border-rule/70",
  },

  btn: {
    /** The one action per view. */
    primary:
      "inline-flex items-center justify-center font-display uppercase " +
      "tracking-wide px-6 py-3 bg-ink text-newsprint border border-ink " +
      "hover:bg-marker hover:border-marker transition-colors " +
      "focus-visible:outline focus-visible:outline-2 " +
      "focus-visible:outline-offset-2 focus-visible:outline-marker " +
      "disabled:opacity-40 disabled:pointer-events-none",
    /** Everything else. */
    secondary:
      "inline-flex items-center justify-center font-display uppercase " +
      "tracking-wide px-6 py-3 bg-transparent text-ink border border-ink/40 " +
      "hover:border-ink transition-colors " +
      "focus-visible:outline focus-visible:outline-2 " +
      "focus-visible:outline-offset-2 focus-visible:outline-marker " +
      "disabled:opacity-40 disabled:pointer-events-none",
    /** Inline text link. */
    link:
      "font-body underline underline-offset-4 decoration-rule " +
      "hover:decoration-marker hover:text-marker transition-colors " +
      "focus-visible:outline focus-visible:outline-2 " +
      "focus-visible:outline-offset-2 focus-visible:outline-marker",
  },

  input:
    "w-full font-body bg-paper text-ink border border-rule px-3 py-2 " +
    "placeholder:text-ink/40 focus:outline-none focus:border-ink " +
    "focus-visible:outline focus-visible:outline-2 " +
    "focus-visible:outline-offset-1 focus-visible:outline-marker " +
    "transition-colors",

  label: "block font-data text-xs uppercase tracking-[0.14em] text-ink/60 mb-1.5",

  alert: {
    error: "border-s-2 border-marker bg-marker/5 text-ink px-4 py-3 font-body text-sm",
    success: "border-s-2 border-clear bg-clear/5 text-ink px-4 py-3 font-body text-sm",
    info: "border-s-2 border-rule bg-ink/[0.03] text-ink px-4 py-3 font-body text-sm",
  },
};
```

- [ ] **Step 3: Export it**

In the `export const theme = {` object at the bottom of `theme.ts`, add `marketing,` to the list of keys.

- [ ] **Step 4: Build**

Run: `cd frontend && npm run build`
Expected: exits 0. TypeScript will catch a malformed object here.

- [ ] **Step 5: Confirm the app tokens are untouched**

Run: `git diff frontend/src/theme.ts | grep "^-" | grep -v "^---"`
Expected: **no output.** This task is purely additive. Any removed line is a regression on the 30 app pages.

- [ ] **Step 6: Commit**

```bash
git add frontend/tailwind.config.ts frontend/src/theme.ts
git commit -m "feat(frontend): add Rota palette and marketing token namespace"
```

---

## Task 4: Shared marketing chrome

Prove the shell on the three legal pages first — they are the smallest (53, 53, 77 lines) and have the least to break.

**Files:**
- Create: `frontend/src/components/marketing/SectionRule.tsx`, `MarketingNav.tsx`, `MarketingFooter.tsx`
- Modify: `frontend/src/pages/PrivacyPolicy.tsx`, `TermsOfService.tsx`, `DataProcessingAgreement.tsx`

**Interfaces:**
- Consumes: `marketing` from Task 3.
- Produces:
  - `<MarketingShell>{children}</MarketingShell>` — nav + `newsprint` ground + footer
  - `<SectionRule eyebrow?: string; title?: string; id?: string; />`
  - `<MarketingNav />`, `<MarketingFooter />`

- [ ] **Step 1: Write `SectionRule.tsx`**

Create `frontend/src/components/marketing/SectionRule.tsx`:

```tsx
import { marketing as m } from "../../theme";

interface Props {
  /** Short structural label. Encodes what the section IS. */
  eyebrow?: string;
  /** Section heading. Comes from i18n — never hardcode copy here. */
  title?: string;
  /** Anchor target, e.g. "pricing" for the #pricing link. */
  id?: string;
}

/**
 * The page's structural grammar: a full-bleed rule with an optional
 * eyebrow and heading sitting on it. Replaces the centred
 * `text-3xl font-bold` heading that every section used to share.
 */
export default function SectionRule({ eyebrow, title, id }: Props) {
  return (
    <div id={id} className="scroll-mt-20">
      <div className={`border-t ${m.rule.heavy}`} />
      {(eyebrow || title) && (
        <div className="pt-6 pb-10 flex flex-col gap-3 md:flex-row md:items-baseline md:gap-8">
          {eyebrow && (
            <span className={`${m.text.meta} shrink-0 md:w-40`}>{eyebrow}</span>
          )}
          {title && (
            <h2
              className={`${m.text.display} font-display text-3xl md:text-5xl font-semibold leading-[1.05]`}
            >
              {title}
            </h2>
          )}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Write `MarketingNav.tsx`**

Create `frontend/src/components/marketing/MarketingNav.tsx`. The links and their i18n keys are copied from the current `Landing.tsx:83-101`; do not invent new ones:

```tsx
import { Link } from "react-router-dom";
import LanguageSelector from "../shared/LanguageSelector";
import { useLanguage } from "../../i18n/LanguageContext";
import { marketing as m } from "../../theme";

export default function MarketingNav() {
  const { t } = useLanguage();

  return (
    <nav className={`sticky top-0 z-50 bg-newsprint border-b ${m.rule.line}`}>
      <div className="max-w-[92rem] mx-auto px-6 h-16 flex items-center justify-between gap-6">
        <Link to="/" className="flex items-center gap-3 min-w-0">
          <img src="/favicon.svg" alt="" className="w-7 h-7 shrink-0" />
          <span
            className={`${m.text.display} font-display text-lg font-semibold uppercase tracking-[0.06em] truncate`}
          >
            {t.common.appName}
          </span>
        </Link>
        <div className="flex items-center gap-5 shrink-0">
          <LanguageSelector />
          <Link to="/login" className={`${m.btn.link} text-sm`}>
            {t.login.signIn}
          </Link>
          <Link to="/register" className={`${m.btn.primary} !px-4 !py-2 text-sm`}>
            {t.register.registerBtn}
          </Link>
        </div>
      </div>
    </nav>
  );
}
```

- [ ] **Step 3: Write `MarketingFooter.tsx`**

Create `frontend/src/components/marketing/MarketingFooter.tsx`. Links and keys are copied from the current `Landing.tsx:424-440`:

```tsx
import { Link } from "react-router-dom";
import { useLanguage } from "../../i18n/LanguageContext";
import { marketing as m } from "../../theme";

export default function MarketingFooter() {
  const { t } = useLanguage();

  const links = [
    { to: "/features", label: t.landing.featuresLink },
    { to: "/privacy-policy", label: t.gdpr.privacyPolicy },
    { to: "/terms", label: t.gdpr.termsOfService },
    { to: "/dpa", label: t.gdpr.dpa },
  ];

  return (
    <footer className={`border-t ${m.rule.heavy} mt-24`}>
      <div className="max-w-[92rem] mx-auto px-6 py-10 flex flex-col gap-6 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2.5">
          <img src="/favicon.svg" alt="" className="w-5 h-5" />
          <span className={`${m.text.meta} !text-ink`}>{t.common.appName}</span>
        </div>
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
          {links.map((l) => (
            <Link key={l.to} to={l.to} className={`${m.btn.link} text-sm`}>
              {l.label}
            </Link>
          ))}
        </div>
        <span className={m.text.meta}>Suggestival LLC</span>
      </div>
    </footer>
  );
}
```

- [ ] **Step 4: Add the shell wrapper**

Append to `frontend/src/components/marketing/MarketingNav.tsx`:

Add `import MarketingFooter from "./MarketingFooter";` to the top of the file, then append:

```tsx
export function MarketingShell({ children }: { children: React.ReactNode }) {
  return (
    <div className={`min-h-screen ${m.page} font-body`}>
      <MarketingNav />
      {children}
      <MarketingFooter />
    </div>
  );
}
```

- [ ] **Step 5: Apply the shell to the three legal pages**

For each of `PrivacyPolicy.tsx`, `TermsOfService.tsx`, `DataProcessingAgreement.tsx`:

1. Add `import { MarketingShell } from "../components/marketing/MarketingNav";` and `import { marketing as m } from "../theme";`
2. Wrap the returned JSX in `<MarketingShell>…</MarketingShell>`.
3. Remove any existing local nav or footer markup that `MarketingShell` now provides.
4. Replace `glass-card` with `${m.surface} border ${m.rule.line}`.
5. Replace `text.heading` / `text.body` / `text.muted` with `m.text.display` / `m.text.body` / `m.text.muted`.
6. Convert every `pl-`/`pr-`/`ml-`/`mr-`/`text-left`/`text-right` to its logical equivalent.
7. Give the body copy a readable measure: `max-w-[68ch]`.

**Do not touch any `t.*` reference, any prop, or any component that is not presentational.**

- [ ] **Step 6: Verify no physical properties slipped in**

Run:

```bash
cd frontend && grep -nE "\b(pl-|pr-|ml-|mr-|text-left|text-right)" \
  src/components/marketing/*.tsx src/pages/PrivacyPolicy.tsx \
  src/pages/TermsOfService.tsx src/pages/DataProcessingAgreement.tsx
```

Expected: **no output.** Any hit breaks the Arabic and Urdu layouts.

- [ ] **Step 7: Build and screenshot**

```bash
cd frontend && npm run build && npm run verify:shoot -- --tag chrome
```

Expected: build exits 0. Open `.verify/chrome/terms-desktop-ar.png` and confirm the nav and footer mirror correctly. Open `terms-desktop-ru.png` and confirm the heading renders in Source Sans 3 rather than a system font.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/marketing frontend/src/pages/PrivacyPolicy.tsx \
        frontend/src/pages/TermsOfService.tsx \
        frontend/src/pages/DataProcessingAgreement.tsx
git commit -m "feat(frontend): add marketing shell, nav, footer, and section rule; apply to legal pages"
```

---

## Task 5: The rota fixture and the static hero

Build the signature element as pure markup and CSS. **No animation in this task** — motion is added in Task 9, and the hero must be correct standing still before it moves.

**Files:**
- Create: `frontend/src/components/marketing/rotaData.ts`, `frontend/src/components/marketing/RotaHero.tsx`

**Interfaces:**
- Consumes: `marketing` from Task 3, `SectionRule` from Task 4.
- Produces:
  - `rotaData.ts` exporting `DAYS: readonly string[]`, `BANDS: readonly Band[]`, `CELLS: readonly Cell[]`, `TOTALS: { shifts: number; people: number; violations: number }`
  - `type Cell = { day: number; band: number; role: string; hours: string; retried: boolean }`
  - `<RotaHero />` — self-contained, no props

- [ ] **Step 1: Write the fixture**

Create `frontend/src/components/marketing/rotaData.ts`. **Role names are illustrative fixture data for a marketing graphic, not application role records** — the "roles are never hardcoded" rule governs the `roles` table, which this does not touch. Keep them generic:

```ts
export type Cell = {
  /** Column index into DAYS. */
  day: number;
  /** Row index into BANDS. */
  band: number;
  /** Short role label shown in the cell. */
  role: string;
  /** Shift window, rendered in tabular figures. */
  hours: string;
  /** True for the cells that flash `marker` before resolving —
   *  mirrors the conflict-retry the real pipeline performs. */
  retried: boolean;
};

export const DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"] as const;

export const BANDS = ["OPEN", "MID", "CLOSE"] as const;

export const CELLS: readonly Cell[] = [
  { day: 0, band: 0, role: "Front", hours: "06–14", retried: false },
  { day: 0, band: 1, role: "Line", hours: "11–19", retried: false },
  { day: 0, band: 2, role: "Close", hours: "16–00", retried: false },
  { day: 1, band: 0, role: "Front", hours: "06–14", retried: false },
  { day: 1, band: 1, role: "Line", hours: "11–19", retried: true },
  { day: 2, band: 0, role: "Front", hours: "06–14", retried: false },
  { day: 2, band: 1, role: "Prep", hours: "10–18", retried: false },
  { day: 2, band: 2, role: "Close", hours: "16–00", retried: false },
  { day: 3, band: 0, role: "Front", hours: "06–14", retried: false },
  { day: 3, band: 2, role: "Close", hours: "16–00", retried: true },
  { day: 4, band: 0, role: "Front", hours: "06–14", retried: false },
  { day: 4, band: 1, role: "Line", hours: "11–19", retried: false },
  { day: 4, band: 2, role: "Close", hours: "16–00", retried: false },
  { day: 5, band: 0, role: "Prep", hours: "07–15", retried: false },
  { day: 5, band: 1, role: "Line", hours: "11–19", retried: false },
  { day: 5, band: 2, role: "Close", hours: "16–00", retried: true },
  { day: 6, band: 0, role: "Front", hours: "08–16", retried: false },
  { day: 6, band: 1, role: "Line", hours: "12–20", retried: false },
];

export const TOTALS = { shifts: 38, people: 12, violations: 0 } as const;
```

- [ ] **Step 2: Write the static hero**

Create `frontend/src/components/marketing/RotaHero.tsx`. The `data-cell` and `data-total` attributes exist so Task 9 can animate without restructuring:

```tsx
import { Link } from "react-router-dom";
import { useLanguage } from "../../i18n/LanguageContext";
import { marketing as m } from "../../theme";
import { BANDS, CELLS, DAYS, TOTALS } from "./rotaData";

export default function RotaHero() {
  const { t } = useLanguage();

  const cellAt = (day: number, band: number) =>
    CELLS.find((c) => c.day === day && c.band === band);

  return (
    <section className="max-w-[92rem] mx-auto px-6 pt-16 pb-20 grid gap-12 lg:grid-cols-[minmax(0,26rem)_minmax(0,1fr)] lg:gap-16 lg:items-center">
      {/* ── Copy ── */}
      <div>
        <p className={`${m.text.meta} mb-6`}>{t.landing.badge}</p>
        <h1
          className={`${m.text.display} font-display text-5xl sm:text-6xl lg:text-7xl font-semibold leading-[0.95] mb-6`}
        >
          {t.landing.heroTitle}{" "}
          <span className="text-marker">{t.landing.heroTitleAccent}</span>
        </h1>
        <p className={`${m.text.muted} text-lg leading-relaxed mb-9 max-w-[46ch]`}>
          {t.landing.heroDesc}
        </p>
        <div className="flex flex-wrap items-center gap-4">
          <Link to="/register" className={m.btn.primary}>
            {t.landing.getStarted}
          </Link>
          <a href="#pricing" className={m.btn.link}>
            {t.landing.viewPricing}
          </a>
        </div>
      </div>

      {/* ── The rota ── */}
      <div className={`${m.surface} border ${m.rule.heavy}`}>
        <div
          className="grid"
          style={{ gridTemplateColumns: `4.5rem repeat(${DAYS.length}, minmax(0, 1fr))` }}
        >
          {/* header row */}
          <div className={`border-b ${m.rule.grid}`} />
          {DAYS.map((d, i) => (
            <div
              key={d}
              data-rota-head={i}
              className={`${m.text.meta} border-b border-s ${m.rule.grid} px-2 py-2.5 text-center`}
            >
              {d}
            </div>
          ))}

          {/* body */}
          {BANDS.map((band, b) => (
            <div key={band} className="contents">
              <div
                className={`${m.text.meta} border-b ${m.rule.grid} px-2 py-3 flex items-center`}
              >
                {band}
              </div>
              {DAYS.map((_, d) => {
                const cell = cellAt(d, b);
                return (
                  <div
                    key={`${band}-${d}`}
                    data-cell={cell ? `${d}-${b}` : undefined}
                    data-retried={cell?.retried ? "true" : undefined}
                    className={`border-b border-s ${m.rule.grid} px-2 py-3 min-h-[4.25rem] transition-colors ${
                      cell ? "hover:bg-marker/10" : ""
                    }`}
                  >
                    {cell && (
                      <>
                        <div className={`${m.text.body} text-sm font-medium leading-tight`}>
                          {cell.role}
                        </div>
                        <div className={`${m.text.data} text-xs text-ink/60 mt-1`}>
                          {cell.hours}
                        </div>
                      </>
                    )}
                  </div>
                );
              })}
            </div>
          ))}
        </div>

        {/* footline */}
        <div className="px-4 py-3.5 flex flex-wrap items-center gap-x-6 gap-y-1">
          <span className={`${m.text.data} text-sm`}>
            <span data-total="shifts">{TOTALS.shifts}</span>{" "}
            <span className="text-ink/60">shifts</span>
          </span>
          <span className={`${m.text.data} text-sm`}>
            <span data-total="people">{TOTALS.people}</span>{" "}
            <span className="text-ink/60">people</span>
          </span>
          <span className={`${m.text.data} text-sm ${m.text.clear}`}>
            <span data-total="violations">{TOTALS.violations}</span> rest violations
          </span>
        </div>
      </div>
    </section>
  );
}
```

- [ ] **Step 3: Verify the i18n keys referenced actually exist**

Run:

```bash
cd frontend && for k in badge heroTitle heroTitleAccent heroDesc getStarted viewPricing; do
  printf "%-18s " "$k"; grep -c "  $k:" src/i18n/en.ts; done
```

Expected: `1` for every key. A `0` means the key name is wrong — correct `RotaHero.tsx` to match `en.ts`. **Never add a key to the locale files.**

- [ ] **Step 4: Build**

Run: `cd frontend && npm run build`
Expected: exits 0.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/marketing/rotaData.ts \
        frontend/src/components/marketing/RotaHero.tsx
git commit -m "feat(frontend): add static rota hero and its fixture"
```

---

## Task 6: Rebuild the landing page

**Files:**
- Modify: `frontend/src/pages/Landing.tsx` (currently 442 lines)

**Interfaces:**
- Consumes: `MarketingShell`, `SectionRule`, `RotaHero`, `marketing`.
- Produces: nothing downstream.

Target rhythm, replacing eight identically-shaped sections:

```
NAV        MarketingShell
HERO       RotaHero
FEATURES   3 principal + 9 ruled list
DEMO       full-bleed ink band
STRATEGY   4 across, AI weighted above the 3 rule-based
PRICING    receipt first, overage detail below
GDPR       4-up, quiet
CTA        one action
FOOTER     MarketingShell
```

- [ ] **Step 1: Rewrite the page skeleton**

Add `import { MarketingShell } from "../components/marketing/MarketingNav";`, `import SectionRule from "../components/marketing/SectionRule";`, `import RotaHero from "../components/marketing/RotaHero";`, and `import { marketing as m } from "../theme";`. Then replace the outer `<div className="min-h-screen">` with `<MarketingShell>`, delete the inline `<nav>` (lines 82-101) and `<footer>` (424-440), and keep the `<script type="application/ld+json">` block exactly as-is — it is SEO, not style. Replace `<RotaHero />` for the old hero section (98-129). Wrap the remaining sections in `<main className="max-w-[92rem] mx-auto px-6">`.

- [ ] **Step 2: Split the features array**

Replace the single `features` array (lines 11-24) with two, preserving every i18n key:

```tsx
  const principalFeatures = [
    { title: t.landing.featAITitle, desc: t.landing.featAIDesc },
    { title: t.landing.featStrategiesTitle, desc: t.landing.featStrategiesDesc },
    { title: t.landing.featMultiLocTitle, desc: t.landing.featMultiLocDesc },
  ];

  const supportingFeatures = [
    { title: t.landing.featHourCapsTitle, desc: t.landing.featHourCapsDesc },
    { title: t.landing.featDayRulesTitle, desc: t.landing.featDayRulesDesc },
    { title: t.landing.featSelfServiceTitle, desc: t.landing.featSelfServiceDesc },
    { title: t.landing.feat7shiftsTitle, desc: t.landing.feat7shiftsDesc },
    { title: t.landing.featDeputyTitle, desc: t.landing.featDeputyDesc },
    { title: t.landing.featGDPRTitle, desc: t.landing.featGDPRDesc },
    { title: t.landing.featAffinitiesTitle, desc: t.landing.featAffinitiesDesc },
    { title: t.landing.featRoleEquivTitle, desc: t.landing.featRoleEquivDesc },
    { title: t.landing.featLangsTitle, desc: t.landing.featLangsDesc },
  ];
```

All twelve keys survive. Nothing is deleted.

- [ ] **Step 3: Render the features section**

```tsx
      <SectionRule eyebrow="01 — Capability" title={t.landing.featuresTitle} />
      <div className="grid gap-10 md:grid-cols-3 mb-16">
        {principalFeatures.map((f) => (
          <div key={f.title}>
            <h3 className={`${m.text.display} font-display text-2xl font-semibold mb-3`}>
              {f.title}
            </h3>
            <p className={`${m.text.muted} leading-relaxed`}>{f.desc}</p>
          </div>
        ))}
      </div>
      <dl className={`border-t ${m.rule.line}`}>
        {supportingFeatures.map((f) => (
          <div
            key={f.title}
            className={`border-b ${m.rule.line} py-4 grid gap-1 md:grid-cols-[18rem_1fr] md:gap-8`}
          >
            <dt className={`${m.text.body} font-medium`}>{f.title}</dt>
            <dd className={`${m.text.muted} text-sm leading-relaxed`}>{f.desc}</dd>
          </div>
        ))}
      </dl>
```

Note the eyebrow numbering: these sections **are** a deliberate sequence (capability → proof → method → cost → assurance), so `01 —` through `05 —` encode real reading order rather than decorating.

- [ ] **Step 4: Make the demo the one dark band**

Wrap the demo section so it breaks the page ground. Keep the `<iframe>` and every one of its attributes byte-identical — only the wrapper changes:

```tsx
      <div className={`${m.inverse} -mx-6 px-6 py-20 mb-24`}>
        <div className="max-w-5xl mx-auto">
          <p className={`${m.text.meta} !text-newsprint/60 mb-4`}>02 — Proof</p>
          <h2 className="font-display text-3xl md:text-5xl font-semibold leading-[1.05] mb-10">
            {t.landing.demoTitle}
          </h2>
          <div className="relative w-full" style={{ paddingBottom: "56.25%" }}>
            {/* iframe unchanged */}
          </div>
          <div className="mt-10">
            <Link to="/features" className={`${m.btn.secondary} !border-newsprint/40 !text-newsprint hover:!border-newsprint`}>
              {t.landing.exploreDashboard}
            </Link>
          </div>
        </div>
      </div>
```

- [ ] **Step 5: Weight the strategies**

The AI strategy is the product; the three rule-based ones are the floor. Currently all four render as equal `glass-card`s (lines 190-201).

First, in the `strategies` array (lines 26-50), delete the `badge` field from all four entries — those colour strings are replaced by structure. Then render:

```tsx
      <SectionRule eyebrow="03 — Method" title={t.landing.strategiesTitle} />
      {/* The AI strategy is the product. */}
      <div className={`${m.surface} border ${m.rule.heavy} p-6 md:p-10 mb-10`}>
        <p className={`${m.text.meta} !text-marker mb-3`}>{strategies[3].tag}</p>
        <h3 className={`${m.text.display} font-display text-3xl font-semibold mb-4`}>
          {strategies[3].name}
        </h3>
        <p className={`${m.text.muted} leading-relaxed max-w-[62ch]`}>
          {strategies[3].desc}
        </p>
      </div>
      {/* The three rule-based strategies are the floor beneath it. */}
      <div className={`grid gap-px md:grid-cols-3 bg-rule border ${m.rule.line} mb-24`}>
        {strategies.slice(0, 3).map((s) => (
          <div key={s.name} className="bg-newsprint p-6">
            <p className={`${m.text.meta} mb-2`}>{s.tag}</p>
            <h3 className={`${m.text.body} font-medium mb-2`}>{s.name}</h3>
            <p className={`${m.text.muted} text-sm leading-relaxed`}>{s.desc}</p>
          </div>
        ))}
      </div>
```

`strategies[3]` is the AI entry — it is last in the existing array (lines 44-49) and the only one whose `badge` referenced `accent`. If you reordered the array, fix the indices; do not reorder it.

- [ ] **Step 6: Lead pricing with the receipt**

Move the example table (currently lines 339-393) to the **top** of the pricing section, directly under `<SectionRule id="pricing" eyebrow="04 — Cost" title={t.landing.pricingTitle} />`. Style it as an itemised bill:

```tsx
        <div className={`${m.surface} border ${m.rule.heavy} p-6 md:p-10 max-w-3xl`}>
          <p className={`${m.text.meta} mb-6`}>{t.landing.exampleTitle}</p>
          <table className="w-full font-data text-sm tabular-nums">
            {/* thead/tbody rows unchanged in content; restyle only:
                th  -> className={`${m.text.meta} text-start pb-3`}
                td  -> className="py-2.5 text-start"
                cost td -> className="py-2.5 text-end"
                row border -> className={`border-b ${m.rule.grid}`}
                savings cells -> className={`text-end ${m.text.clear}`}
                total row -> className={`border-t-2 border-ink pt-4 font-semibold`} */}
          </table>
        </div>
```

Then the `$18` base panel, then the four overage cards as a ruled 4-up — no card backgrounds, dividers only.

- [ ] **Step 7: Fix the four hardcoded badge literals**

At lines 255, 275, 295, 315 the badge text reads `AI`, `GEN`, `DB`, `#`. `DB` is database vocabulary on a page sold to restaurant managers. Change to `AI`, `RUNS`, `DATA`, `TEAM`, and replace the four ad-hoc colour classes (`bg-accent/10`, `bg-amber-100`, `bg-cyan-100`, `bg-emerald-100`) with a single `${m.text.meta}` treatment. **These four strings are the only text in this task you may change** — they are absent from `src/i18n/`.

- [ ] **Step 8: Quiet the GDPR section and single out the CTA**

GDPR: 4-up, `text-sm`, dividers only, no cards — it is reassurance, not a pitch. CTA: keep `t.landing.ctaBtn` as `m.btn.primary` and demote the "sign in" link to `m.btn.link`. Two competing primary buttons become one.

- [ ] **Step 9: Verify nothing but presentation changed**

```bash
cd frontend && git diff -U0 src/pages/Landing.tsx \
  | grep -E "^[-+]" | grep -vE "^(\+\+\+|---)" \
  | grep -E "useState|useEffect|useRef|onClick|onSubmit|onChange|fetch\(|api\.|href=\"http"
```

Expected: **no output.** Any hit means behaviour moved and must be reverted.

Then confirm no i18n key was dropped:

```bash
cd frontend && git show HEAD:src/pages/Landing.tsx | grep -o "t\.[a-zA-Z.]*" | sort -u > /tmp/keys-before.txt
grep -o "t\.[a-zA-Z.]*" src/pages/Landing.tsx | sort -u > /tmp/keys-after.txt
diff /tmp/keys-before.txt /tmp/keys-after.txt
```

Expected: **no output.** Every key that was rendered before is still rendered.

- [ ] **Step 10: Build, screenshot, interact**

```bash
cd frontend && npm run build && npm run verify:shoot -- --tag landing && npm run verify:interact
```

Expected: build exits 0, `verify:interact` exits 0. Review `landing-desktop-en.png` against `.verify/before/landing-desktop-en.png`, and check `landing-mobile-en.png` and `landing-desktop-ar.png` for layout breaks.

- [ ] **Step 11: Commit**

```bash
git add frontend/src/pages/Landing.tsx
git commit -m "feat(frontend): rebuild landing page on the Rota system"
```

---

## Task 7: Auth pages

**Files:**
- Create: `frontend/src/components/marketing/AuthLayout.tsx`
- Modify: `Login.tsx`, `Register.tsx`, `ForgotPassword.tsx`, `ResetPassword.tsx`, `AcceptInvite.tsx`, `AcceptManagerInvite.tsx`

**Interfaces:**
- Consumes: `marketing`, `rotaData`.
- Produces: `<AuthLayout title: string; children: React.ReactNode; footer?: React.ReactNode>`

- [ ] **Step 1: Write `AuthLayout.tsx`**

Create `frontend/src/components/marketing/AuthLayout.tsx`:

```tsx
import { Link } from "react-router-dom";
import LanguageSelector from "../shared/LanguageSelector";
import { useLanguage } from "../../i18n/LanguageContext";
import { marketing as m } from "../../theme";
import { BANDS, CELLS, DAYS } from "./rotaData";

interface Props {
  /** Page title. Must come from i18n. */
  title: string;
  children: React.ReactNode;
  /** Sign-in / register cross-links. */
  footer?: React.ReactNode;
}

/**
 * Split shell for all six auth pages: form on `paper`, a quiet static
 * rota field beside it. Replaces the centred max-w-md glass card.
 * The panel is decorative and hidden from assistive tech.
 */
export default function AuthLayout({ title, children, footer }: Props) {
  const { t } = useLanguage();

  return (
    <div className={`min-h-screen ${m.page} font-body lg:grid lg:grid-cols-2`}>
      {/* form side */}
      <div className="flex flex-col min-h-screen px-6 py-8 lg:px-14">
        <div className="flex items-center justify-between mb-14">
          <Link to="/" className="flex items-center gap-2.5">
            <img src="/favicon.svg" alt="" className="w-6 h-6" />
            <span
              className={`${m.text.display} font-display text-base font-semibold uppercase tracking-[0.06em]`}
            >
              {t.common.appName}
            </span>
          </Link>
          <LanguageSelector />
        </div>

        <div className="flex-1 flex flex-col justify-center max-w-[26rem] w-full">
          <h1
            className={`${m.text.display} font-display text-4xl font-semibold leading-tight mb-8`}
          >
            {title}
          </h1>
          {children}
          {footer && <div className="mt-8">{footer}</div>}
        </div>
      </div>

      {/* rota side — decorative */}
      <div
        aria-hidden="true"
        className={`hidden lg:flex items-center justify-center border-s ${m.rule.heavy} ${m.surface} p-14`}
      >
        <div
          className={`w-full max-w-lg border ${m.rule.heavy} grid`}
          style={{ gridTemplateColumns: `3.5rem repeat(${DAYS.length}, minmax(0, 1fr))` }}
        >
          <div className={`border-b ${m.rule.grid}`} />
          {DAYS.map((d) => (
            <div
              key={d}
              className={`${m.text.meta} !text-[0.6rem] border-b border-s ${m.rule.grid} px-1 py-1.5 text-center`}
            >
              {d}
            </div>
          ))}
          {BANDS.map((band, b) => (
            <div key={band} className="contents">
              <div
                className={`${m.text.meta} !text-[0.6rem] border-b ${m.rule.grid} px-1 py-2 flex items-center`}
              >
                {band}
              </div>
              {DAYS.map((_, d) => {
                const filled = CELLS.some((c) => c.day === d && c.band === b);
                return (
                  <div
                    key={`${band}-${d}`}
                    className={`border-b border-s ${m.rule.grid} h-11 ${
                      filled ? "bg-ink/[0.06]" : ""
                    }`}
                  />
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Convert `Login.tsx`**

At `Login.tsx:157-158`, replace:

```tsx
    <div className="min-h-screen flex items-center justify-center">
      <div className="glass-card p-8 w-full max-w-md">
        <div className="flex justify-end mb-4">
          {/* LanguageSelector — now provided by AuthLayout, delete this block */}
        </div>
```

with:

```tsx
    <AuthLayout title={t.login.title}>
```

Then, throughout the file: `glass-input` → `{m.input}`, `glass-label` → `{m.label}`, `glass-btn-primary` → `{m.btn.primary} w-full`, `glass-btn-secondary` → `{m.btn.secondary} w-full`, `glass-alert-error` → `{m.alert.error}`, and the demo-credentials block at 313/323 → `${m.surface} border ${m.rule.line} p-4 font-data text-xs`.

The `<img src="/favicon.svg" ... className="w-8 h-8 inline" />` at line 163 is now redundant — `AuthLayout` renders the mark. Delete it.

**Do not touch** `handleSubmit`, `handleGoogleLink`, the `googleBtnRef` div at line 294, or any `useState`.

- [ ] **Step 3: Convert the other five pages**

For `Register.tsx` (wrapper at line 86), `ForgotPassword.tsx`, `ResetPassword.tsx`, `AcceptInvite.tsx`, and `AcceptManagerInvite.tsx`:

1. Replace the `min-h-screen flex items-center justify-center` + `glass-card p-8 w-full max-w-md` wrapper pair with `<AuthLayout title={…}>`.
2. Delete the page's own `LanguageSelector` block and its inline `favicon.svg` — `AuthLayout` renders both.
3. Apply this exact class mapping:

| Current | Replacement |
|---|---|
| `glass-input` | `{m.input}` |
| `glass-input-sm` | `{m.input} !py-1` |
| `glass-select` | `{m.input} appearance-none` |
| `glass-label` | `{m.label}` |
| `glass-btn-primary` | `` {`${m.btn.primary} w-full`} `` |
| `glass-btn-secondary` | `` {`${m.btn.secondary} w-full`} `` |
| `glass-alert-error` | `{m.alert.error}` |
| `glass-alert-success` | `{m.alert.success}` |
| `glass-alert-info` | `{m.alert.info}` |
| `text.heading` | `m.text.display` |
| `text.body` | `m.text.body` |
| `text.muted` / `text.placeholder` | `m.text.muted` |
| `action.link` | `m.btn.link` |

4. Convert every `pl-`/`pr-`/`ml-`/`mr-`/`text-left`/`text-right` to its logical equivalent.

Each page's title comes from its existing i18n key — grep the current `<h1>`/`<h2>` in that file and reuse that exact key. **Do not invent titles and do not add keys.**

Do not touch any `useState`, `useEffect`, `useRef`, submit handler, or token-reading logic in these files. `AcceptInvite.tsx` and `AcceptManagerInvite.tsx` both read an invite token from the query string — that code is off limits.

- [ ] **Step 4: Verify class and behaviour hygiene**

```bash
cd frontend && grep -nE "glass-|\b(pl-|pr-|ml-|mr-|text-left|text-right)" \
  src/pages/Login.tsx src/pages/Register.tsx src/pages/ForgotPassword.tsx \
  src/pages/ResetPassword.tsx src/pages/AcceptInvite.tsx \
  src/pages/AcceptManagerInvite.tsx src/components/marketing/AuthLayout.tsx
```

Expected: **no output.**

```bash
cd frontend && git diff -U0 src/pages/Login.tsx src/pages/Register.tsx \
  src/pages/ForgotPassword.tsx src/pages/ResetPassword.tsx \
  src/pages/AcceptInvite.tsx src/pages/AcceptManagerInvite.tsx \
  | grep -E "^[-+]" | grep -vE "^(\+\+\+|---)" \
  | grep -E "useState|useEffect|useRef|onSubmit|onClick|onChange|await |api\."
```

Expected: **no output** except deleted `LanguageSelector` markup.

- [ ] **Step 5: Build and prove the forms still work**

```bash
cd frontend && npm run build && npm run verify:interact && npm run verify:shoot -- --tag auth
```

Expected: build exits 0; `verify:interact` exits 0 — **this is the gate that matters for this task.** Confirm `login-desktop-ar.png` places the rota panel on the correct side after the RTL flip.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/marketing/AuthLayout.tsx frontend/src/pages/Login.tsx \
        frontend/src/pages/Register.tsx frontend/src/pages/ForgotPassword.tsx \
        frontend/src/pages/ResetPassword.tsx frontend/src/pages/AcceptInvite.tsx \
        frontend/src/pages/AcceptManagerInvite.tsx
git commit -m "feat(frontend): restyle auth pages on a split rota layout"
```

---

## Task 8: Features page

**Files:**
- Modify: `frontend/src/pages/Features.tsx` (187 lines)

**Interfaces:** Consumes `MarketingShell`, `SectionRule`, `marketing`.

`Features.tsx` already has the strongest structure on the site — a `lg:grid-cols-[220px_1fr]` TOC beside screenshot panels (line 77). Keep that structure; restyle it.

- [ ] **Step 1: Apply the shell**

Add `import { MarketingShell } from "../components/marketing/MarketingNav";`, `import SectionRule from "../components/marketing/SectionRule";`, and `import { marketing as m } from "../theme";`. Wrap the returned JSX in `<MarketingShell>` and delete the inline nav (lines 36-58) and footer (154+). Keep `PAGE_SLUGS` and every `t.*` reference exactly as-is.

- [ ] **Step 2: Restyle the TOC**

The sticky TOC at lines 80-99 becomes a ruled index: each entry `border-b ${m.rule.line} py-2`, `${m.text.meta}` when inactive, `text-marker` when active. This is a real index into a document, so numbering it is warranted — prefix each with its zero-padded position.

- [ ] **Step 3: Restyle the panels**

At line 108, replace `glass-card p-6 lg:p-8` with `${m.surface} border ${m.rule.heavy} p-6 lg:p-10`. Headings at line 116 → `${m.text.display} font-display text-2xl font-semibold`. Body copy → `${m.text.muted} max-w-[62ch]`. Screenshot images get `border ${m.rule.line}`.

- [ ] **Step 4: Restyle hero and CTA**

Hero (60-73): `text-center` → `text-start`, heading → `${m.text.display} font-display text-5xl lg:text-6xl font-semibold leading-[0.95]`. CTA (133-151): single primary action.

- [ ] **Step 5: Verify**

```bash
cd frontend && grep -nE "glass-|\b(pl-|pr-|ml-|mr-|text-left|text-right)" src/pages/Features.tsx
npm run build && npm run verify:shoot -- --tag features
```

Expected: no grep output; build exits 0.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/Features.tsx
git commit -m "feat(frontend): restyle features page on the Rota system"
```

---

## Task 9: Motion

**Files:**
- Modify: `frontend/package.json`, `frontend/src/components/marketing/RotaHero.tsx`, `frontend/src/components/marketing/SectionRule.tsx`

**Interfaces:** Consumes the `data-cell` / `data-total` hooks placed in Task 5.

One orchestrated moment; everything else nearly still. Scattered fade-ups on every card are themselves a generated-design signature.

- [ ] **Step 1: Install the one authorised dependency**

Run: `cd frontend && npm install motion`
Expected: `package.json` gains `"motion"` under `dependencies` and nothing else appears there.

Verify: `cd frontend && git diff package.json` — the only added dependency line is `motion`.

- [ ] **Step 2: Animate the hero**

In `RotaHero.tsx`, import `motion` and `useReducedMotion`:

```tsx
import { motion, useReducedMotion } from "motion/react";
```

Add inside the component, above the return:

```tsx
  const reduce = useReducedMotion();

  // Diagonal wave: cells further from the top-start corner land later.
  const cellDelay = (day: number, band: number) => 0.4 + (day + band) * 0.024;

  const cellAnim = (c: { day: number; band: number; retried: boolean }) =>
    reduce
      ? {}
      : {
          initial: { opacity: 0, scale: 0.94 },
          animate: {
            opacity: 1,
            scale: 1,
            // Retried cells flash `marker` mid-sequence, then resolve —
            // mirroring the conflict-retry the real pipeline performs.
            backgroundColor: c.retried
              ? ["rgba(255,90,31,0)", "rgba(255,90,31,0.28)", "rgba(255,90,31,0)"]
              : undefined,
          },
          transition: {
            duration: 0.28,
            delay: cellDelay(c.day, c.band),
            backgroundColor: { duration: 0.5, delay: 1.1, times: [0, 0.4, 1] },
          },
        };
```

Convert the header `<div data-rota-head={i}>` to `<motion.div>` with `initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.2 + i * 0.03 }}` (skip entirely when `reduce`). Convert the filled cell `<div data-cell=…>` to `<motion.div {...cellAnim(cell)}>`.

- [ ] **Step 3: Animate the footline counts**

Add above the return:

```tsx
  const Count = ({ to }: { to: number }) => {
    const [n, setN] = useState(reduce ? to : 0);
    useEffect(() => {
      if (reduce || to === 0) { setN(to); return; }
      let raf = 0;
      const start = performance.now();
      const tick = (now: number) => {
        const p = Math.min(1, (now - start - 1400) / 600);
        if (p >= 0) setN(Math.round(to * p));
        if (p < 1) raf = requestAnimationFrame(tick);
      };
      raf = requestAnimationFrame(tick);
      return () => cancelAnimationFrame(raf);
    }, [to]);
    return <>{n}</>;
  };
```

Add `import { useEffect, useState } from "react";`. Replace `{TOTALS.shifts}` with `<Count to={TOTALS.shifts} />` and likewise for `people`. Leave `violations` as the literal `0` — counting up to zero animates nothing and the value is the point.

- [ ] **Step 4: Draw the section rules on scroll**

In `SectionRule.tsx`, replace the plain `<div className={`border-t ${m.rule.heavy}`} />` with:

```tsx
      <motion.div
        className={`border-t ${m.rule.heavy} origin-[left_center] rtl:origin-[right_center]`}
        initial={reduce ? false : { scaleX: 0 }}
        whileInView={{ scaleX: 1 }}
        viewport={{ once: true, margin: "-80px" }}
        transition={{ duration: 0.3, ease: "easeOut" }}
      />
```

with `const reduce = useReducedMotion();` in the component. **This is the only scroll-triggered animation on the site.** Do not add card stagger, parallax, or fade-ups.

- [ ] **Step 5: Verify reduced motion is honoured**

```bash
cd frontend && node -e '
import("playwright").then(async ({ chromium }) => {
  const b = await chromium.launch();
  const ctx = await b.newContext({ reducedMotion: "reduce" });
  const p = await ctx.newPage();
  await p.goto("http://localhost:5173/", { waitUntil: "domcontentloaded" });
  await p.waitForTimeout(120);   // well before the 400ms wave begins
  const vis = await p.evaluate(() => {
    const cells = [...document.querySelectorAll("[data-cell]")];
    return cells.filter(c => getComputedStyle(c).opacity === "1").length
         + "/" + cells.length;
  });
  console.log("cells fully visible at 120ms:", vis);
  await b.close();
});'
```

Expected: all cells visible (`18/18`). Under reduced motion the grid must be complete on first paint, not animating.

- [ ] **Step 6: Verify the animation actually runs without it**

Repeat the command above with `reducedMotion: "no-preference"`. Expected: **fewer** cells visible at 120ms. If the count is identical, the animation is not wired up.

- [ ] **Step 7: Build, screenshot, interact**

```bash
cd frontend && npm run build && npm run verify:interact && npm run verify:shoot -- --tag motion
```

Expected: both exit 0. `verify:shoot` runs with `reducedMotion: "reduce"`, so screenshots stay deterministic.

- [ ] **Step 8: Commit**

```bash
git add frontend/package.json frontend/package-lock.json \
        frontend/src/components/marketing/RotaHero.tsx \
        frontend/src/components/marketing/SectionRule.tsx
git commit -m "feat(frontend): add hero rota choreography with motion, reduced-motion respected"
```

---

## Task 10: Code splitting

`App.tsx` static-imports all 30+ pages into one bundle, so without this every logged-in manager downloads `motion` to look at a data table.

**Files:**
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Record the current bundle size**

```bash
cd frontend && npm run build && ls -la dist/assets/*.js | awk '{print $5, $9}'
```

Write the largest chunk size down — Step 5 compares against it.

- [ ] **Step 2: Convert the public routes to lazy imports**

In `App.tsx`, replace these ten static imports (lines 5-10, 29-31, 34-35) with lazy ones:

```tsx
import { Suspense, lazy } from "react";

const Login = lazy(() => import("./pages/Login"));
const Register = lazy(() => import("./pages/Register"));
const AcceptInvite = lazy(() => import("./pages/AcceptInvite"));
const AcceptManagerInvite = lazy(() => import("./pages/AcceptManagerInvite"));
const ForgotPassword = lazy(() => import("./pages/ForgotPassword"));
const ResetPassword = lazy(() => import("./pages/ResetPassword"));
const PrivacyPolicy = lazy(() => import("./pages/PrivacyPolicy"));
const TermsOfService = lazy(() => import("./pages/TermsOfService"));
const DataProcessingAgreement = lazy(() => import("./pages/DataProcessingAgreement"));
const Landing = lazy(() => import("./pages/Landing"));
const Features = lazy(() => import("./pages/Features"));
```

Leave every `manager/` and `employee/` import static — this task is not about them.

- [ ] **Step 3: Add the boundary**

Wrap the entire `<Routes>` element in:

```tsx
    <Suspense
      fallback={
        <div className="min-h-screen flex items-center justify-center bg-newsprint">
          <span className="font-data text-xs uppercase tracking-[0.14em] text-ink/50">
            Loading
          </span>
        </div>
      }
    >
      {/* <Routes>…</Routes> */}
    </Suspense>
```

The existing inline `Loading...` divs inside the `/` and `*` routes stay — they cover auth resolution, which is a different state from chunk loading.

- [ ] **Step 4: Build**

Run: `cd frontend && npm run build`
Expected: exits 0, and the output lists **more** JS chunks than before.

- [ ] **Step 5: Confirm the app bundle actually shrank**

```bash
cd frontend && ls -la dist/assets/*.js | awk '{print $5, $9}' | sort -rn | head -5
```

Expected: the largest chunk is **smaller** than the figure recorded in Step 1, despite `motion` now being installed. If it is not, the lazy boundaries are not splitting — investigate before committing.

- [ ] **Step 6: Confirm routing still works**

```bash
cd frontend && npm run verify:interact
```

Expected: exits 0. A missing `Suspense` boundary shows up here as a blank page.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "perf(frontend): lazy-load marketing and auth routes"
```

---

## Task 11: Full verification and critique

**Files:** whatever the findings require.

- [ ] **Step 1: Run every gate**

```bash
cd frontend && npm run build && npm run verify:contrast && npm run verify:interact \
  && npm run verify:shoot -- --tag final
```

Expected: all four exit 0; 30 screenshots in `.verify/final/`.

- [ ] **Step 2: Assemble the behaviour-unchanged evidence**

```bash
git diff main...HEAD -- frontend/src \
  | grep -E "^[-+]" | grep -vE "^(\+\+\+|---)" \
  | grep -E "useState|useEffect|useRef|onClick|onSubmit|onChange|fetch\(|api\.|localStorage" \
  | grep -v "components/marketing/" | grep -v "App.tsx"
```

Expected: **no output.** This is the spec's structural argument. Paste the result into the summary — an empty result is the evidence.

- [ ] **Step 3: Confirm no i18n file was touched**

```bash
cd /Users/robran/IdeaProjects/wiz_scheduler && git diff --stat main...HEAD -- frontend/src/i18n/
```

Expected: **no output.** All 19 locale files unchanged.

- [ ] **Step 4: Confirm the app surface is untouched**

```bash
cd /Users/robran/IdeaProjects/wiz_scheduler && git diff --stat main...HEAD \
  -- frontend/src/pages/manager frontend/src/pages/employee \
     frontend/src/components/shared frontend/src/components/layout
```

Expected: **no output.** The logged-in app is out of scope; any change here is a regression risk that was never authorised.

- [ ] **Step 5: Screenshot critique**

Open every PNG in `.verify/final/`. For each, ask specifically:

1. Does `newsprint` still read as newsprint, or has it drifted back toward the cream default? The grid rules and condensed display type must be carrying it.
2. Is `marker` used **once** per viewport? Count the orange elements. More than one and it stops being a highlighter.
3. Do `ar` and `hi` render coherently, or merely without crashing?
4. Does `ru` show Source Sans 3 900 headlines rather than a system fallback?
5. Do the mobile captures hold, or did the rota grid overflow?

Fix what fails, then re-run Step 1.

- [ ] **Step 6: Remove one accessory**

Look at the finished landing page and identify the single least necessary decorative element. Remove it. The signature is the hero; everything else should be quiet enough that the hero is what gets remembered.

- [ ] **Step 7: Final commit**

```bash
git add -A frontend
git commit -m "chore(frontend): final verification pass on marketing restyle"
```

---

## Deferred, deliberately

Recorded so they are not silently lost:

- **`public/favicon.svg` is 654 KB** of traced bezier paths and ships on every page load. Redrawing it as a hand-authored SVG is its own task; the spec kept the mark, and replacing the file is not the same as replacing the mark.
- **The ~30 logged-in pages** still use `cream`/`sage`/`accent` and the `.glass-*` classes. The `marketing` namespace was authored so they can inherit this system in a later pass.
- **Promoting the Playwright scripts into a committed test suite.** They are throwaway verification here. There is currently no frontend test suite at all, which is worth its own decision.
- **Copy rewrites.** Frozen by the 19-locale cost. A real pass over the marketing copy needs a translation budget.
- **The "168" week-clock** — the rejected direction's strongest idea, a candidate for a future Fair Workweek section.
