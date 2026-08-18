# Marketing & Auth Restyle — Design

**Date:** 2026-08-18
**Status:** Approved design, pending implementation plan
**Scope:** Public marketing + auth surface only. The logged-in app is untouched.

## Context

The frontend has a token layer (`frontend/src/theme.ts`) and a component-class layer
(`.glass-*` in `frontend/src/index.css`), so it is more disciplined than it looks. Its
problem is aesthetic, not structural, and it is concentrated in four places:

1. **No typography exists.** `font-family`, `@font-face`, `fontFamily`, and
   `fonts.googleapis` return zero matches across `src/`, `index.html`, and
   `tailwind.config.ts`. Everything renders in Tailwind's default system stack.
2. **The palette is a default.** Cream `#FFF8E7` + sage + muted gold is the most common
   AI-generated palette in circulation.
3. **`Landing.tsx` has eight sections with one shape.** Every one is
   `py-20 px-6` → centered `text-3xl font-bold` heading → centered muted subhead → grid
   of `glass-card`. Structure encodes nothing about content.
4. **Glass is applied universally** — cards, inputs, buttons, modals, alerts, nav — over
   two fixed radial-gradient blobs on `body`. When every surface is the special surface,
   none is.

## Decisions taken

| Question | Decision |
|---|---|
| Scope | Marketing + auth first. App surface is a later, separate pass. |
| Brand continuity | New palette and typography. **The wizard-hat mark and the Wiz Scheduler name stay.** |
| Dependencies | Self-hosted webfonts (no runtime dep) **and** `motion` (motion.dev, ~18 KB gz). |
| Direction | "The Rota" — identity derived from the printed weekly schedule taped up in back-of-house. |

Directions considered and rejected: **"168"** (week-as-168-hours, Fair Workweek-led) —
strong for the NYC compliance wedge but reads legalistic and undersells the AI. Its
week-clock idea is recorded here as a future candidate only — **there is no Fair Workweek
section in this pass**, and adding one is out of scope. **"Conjure"** (indigo night + gold, leaning into the hat) — dark
ground with a single warm accent is itself a generated-design default, and the whimsy
fights the compliance sale.

## Goals

- The marketing surface reads as deliberately designed and specific to shift operations.
- Zero behavioural change: no handler, API call, state hook, route path, or i18n key is altered.
- The token system is authored so the app surface can inherit it later without rework.

## Non-goals

- Restyling the ~30 logged-in pages (`src/pages/manager/`, `src/pages/employee/`,
  `Sidebar`, `TopBar`, `DataTable`, `Schedule`). Later pass.
- Copy rewrites. See "The i18n constraint".
- Replacing the logo mark. (Noted separately: `public/favicon.svg` is 654 KB of traced
  bezier paths and ships on every page load. Out of scope here; worth its own task.)
- Adopting a component library (kokonut / bklit). Both assume a shadcn + Radix scaffold
  this project does not have; adopting one is a migration, not a restyle, and it trades
  a distinct identity for a shared one.

## Foundation

### Color

| Token | Hex | Role |
|---|---|---|
| `ink` | `#1A1815` | All text; rules at full strength |
| `newsprint` | `#E9E4D8` | Page ground — deliberately greyer and cooler than the old cream |
| `paper` | `#F5F2EA` | Raised surfaces: form panels, hero grid |
| `rule` | `#C9C0B0` | Grid lines, dividers, input borders |
| `marker` | `#FF5A1F` | Highlighter **fill**. Never a text colour. Once per viewport. |
| `clear` | `#1D7357` | Compliant / success only |

**Measured, not estimated** (the harness in Task 1 checked these; an earlier draft of this
spec estimated `marker` at "near 3:1" and was wrong):

- `marker` `#FF5A1F` on `newsprint` is **2.46:1**. It fails AA for body text *and* the 3:1
  large-text threshold. `marker` is therefore **never a text colour at any size.** It is a
  highlighter, and a real highlighter marks by filling: it appears as a background fill with
  `ink` on top — **5.68:1**, comfortably AA — or as a decorative rule that is never the sole
  indicator of state. This is more faithful to the direction than tinting text was.
- `clear` was `#1F7A5C`, measuring 4.14:1 on `newsprint` — 0.36 short of AA. Darkened to
  **`#1D7357`**: 4.55:1 on `newsprint`, 5.15:1 on `paper`. The hue shift is imperceptible.

Every pair is checked programmatically against WCAG AA during implementation, and
`text-marker` is forbidden by a grep gate rather than trusted to a contrast number.

The `.glass-*` classes are not deleted (the app still uses them) but are **not used** on
any marketing or auth page. No `backdrop-blur`, no `bg-white/60`, and no radial-gradient
`body` background on these routes.

### Type

Three roles. Self-hosted `woff2`, subset to Latin + Latin-ext + Cyrillic + Vietnamese.
All three are SIL Open Font License, so self-hosting is permitted. Per-face script
coverage (particularly Cyrillic and Vietnamese) is **verified against the actual font
files before subsetting** — where a face lacks a range, that range falls through to the
system stack described below rather than rendering tofu.

- **Display — Archivo** (variable, `wght` + `wdth`), run at `wdth: 75` for headlines.
  Industrial signage character; deliberately not Oswald.
- **Body — Source Sans 3.** Humanist, warm against the condensed display. Deliberately
  not Inter.
- **Data — IBM Plex Mono** with `font-variant-numeric: tabular-nums`. Every hour count,
  time range, price, and schedule cell. The app displays numbers constantly and they
  currently do not align.

### 19 locales, 9 writing systems

`src/i18n/` ships ar, bn, de, en, es, fr, hi, id, ja, mr, pcm, pt, ru, ta, te, tr, ur,
vi, zh. RTL is already wired — `LanguageContext.tsx:78` sets `document.documentElement.dir`.

Self-hosting all nine scripts is tens of megabytes and is not viable. Therefore:

1. Latin / Cyrillic / Vietnamese locales get the three faces above (~140 KB subsetted).
2. Arabic, Bengali, Devanagari, Tamil, Telugu, JP, and SC fall through to a **per-script
   system font stack**. Every current OS ships adequate coverage.
3. A `:lang()` override cancels `wdth: 75` and negative letter-spacing for those scripts.
   Condensing Devanagari or Arabic damages legibility; **the identity gives way to
   readability, never the reverse.**
4. All directional spacing uses logical properties (`ps-`/`pe-`, `ms-`/`me-`,
   `border-s`/`border-e`, `start-`/`end-`) so the hard-grid layout survives the RTL flip.
   This also fixes a latent bug: the current pages use physical `pl-`/`pr-` throughout.

### Where tokens live

`tailwind.config.ts` gains the palette and font families. `theme.ts` gains a `marketing`
namespace; the existing exports are untouched so the 30 app pages carry no regression
risk.

## Page architecture

Each section gets structure that encodes what it is, replacing the uniform rhythm.

```
NAV      hairline rule beneath. no blur, no float.
HERO     asymmetric split — copy start-side, LIVE ROTA end-side.
         full-bleed, ruled edges.  <- the signature element
FEATURES 3 principal + 9 in a ruled tabular list (was 12 equal cards)
DEMO     full-bleed ink ground — the one dark band on the page
STRATEGY 4 across, weighted: the AI strategy is the product,
         the 3 rule-based ones are the floor (currently all equal)
PRICING  THE RECEIPT leads — $20.30 itemised in Plex Mono, ruled
         like a real bill. Overage detail demoted beneath it.
GDPR     4-up, quiet, small. reassurance, not a pitch.
CTA      one action (currently two compete)
FOOTER   ruled, mono, dense
```

Three calls worth recording:

- **Features 12 → 3 + 9.** Nothing is removed; the remaining nine become a dense ruled
  list. Ranking is the design work — nobody reads twelve equal cards.
  The three promoted are `featAI`, `featStrategies`, and `featMultiLoc` — the AI
  generation engine, the strategy choice, and multi-location, being the three the pricing
  page and the product itself are built around. The implementation plan must not leave
  this to be decided at the keyboard.
- **Pricing leads with the receipt.** The itemised `$20.30` table at
  `Landing.tsx:339-393` is the strongest content on the page and currently sits at the
  bottom of the sixth section. An itemised bill in tabular mono is the same material as
  the rota. `clear` green earns its place here.
- **Auth pages become a split.** `Login.tsx:157` and `Register.tsx:86` are both a
  centred `max-w-md` glass card over the gradient blobs. They become form-on-`paper`
  beside a quiet static rota field. Same fields, same handlers, same Google button.

### The i18n constraint

Copy lives in 19 locale files. Changing one English heading means editing 19 files or
shipping partial translations. **i18n keys stay stable; the restyle works around them.**

The only text changed is hardcoded literals absent from the i18n layer — the overage
badges at `Landing.tsx:255, 275, 295, 315` read `AI` / `GEN` / `DB` / `#`. "DB" is
database vocabulary on a page sold to restaurant managers. Those four are corrected.

Real copy rewrites are a separate pass with their own translation cost.

## Motion

One orchestrated moment; everything else nearly still. Scattered fade-ups on every card
are themselves a generated-design signature.

**The hero rota fill** — the entire motion budget:

```
0ms     empty grid; rules drawn, cells blank
200ms   header row sets (MON..SUN), 30ms stagger
400ms   cells populate in a diagonal wave, 24ms apart,
        each a short scale + opacity settle (not a slide)
1100ms  three cells flash `marker`, then resolve to filled —
        mirroring the conflict-retry the real pipeline performs
1400ms  footline counts up: "38 shifts / 12 people / 0 rest violations"
```

This mirrors the product's actual NDJSON generation stream, so the hero is the product
working rather than a metaphor for it. `motion`'s `staggerChildren` + variants expresses
it in roughly 40 lines; a hand-rolled CSS diagonal wave with a mid-sequence state change
would be materially harder. This is what justifies the 18 KB.

**Everything else:** section rules draw in on scroll (`IntersectionObserver`, 300ms,
once). Nav hairline gains opacity past the hero. Cell hover lifts 1px and gains a
`marker` edge. No card stagger, no parallax, no counters outside the hero.

**`prefers-reduced-motion: reduce`** renders the grid complete on first paint with final
counts and drawn rules — the same experience without the choreography, not a degraded one.

## Code splitting

`App.tsx` currently static-imports all 30+ pages into a single bundle. Adding `motion`
without addressing this means every logged-in manager downloads 18 KB of animation
library to view a data table.

The marketing and auth routes therefore move to `React.lazy` + `Suspense`. This is a real
change to `App.tsx` and it leaves the app bundle **smaller than it is today**.

## Files affected

**Modified**
- `frontend/tailwind.config.ts` — palette, font families, `wdth` utilities
- `frontend/src/index.css` — `@font-face`, `:lang()` overrides, per-script stacks; marketing base layer
- `frontend/src/theme.ts` — add `marketing` namespace; existing exports unchanged
- `frontend/index.html` — font preloads
- `frontend/package.json` — add `motion`
- `frontend/src/App.tsx` — lazy routes for marketing + auth
- `frontend/src/pages/Landing.tsx`, `Features.tsx`
- `frontend/src/pages/Login.tsx`, `Register.tsx`, `ForgotPassword.tsx`, `ResetPassword.tsx`,
  `AcceptInvite.tsx`, `AcceptManagerInvite.tsx`
- `frontend/src/pages/PrivacyPolicy.tsx`, `TermsOfService.tsx`, `DataProcessingAgreement.tsx` — shared chrome only

**Added**
- `frontend/public/fonts/*.woff2`
- `frontend/src/components/marketing/RotaHero.tsx` — the signature element
- `frontend/src/components/marketing/MarketingNav.tsx`, `MarketingFooter.tsx`
- `frontend/src/components/marketing/SectionRule.tsx`
- `frontend/src/components/marketing/AuthLayout.tsx` — shared split shell for the 6 auth pages

## Verification

There is **no frontend test suite** — `tests/` is 30 pytest files, all backend. Playwright
sits in `devDependencies` with no config, no script, and no usage. "Functionality
unchanged" therefore cannot be proven by an existing suite, and is instead established by:

1. **`npm run build`** — `tsc` strict + Vite production build. Hard gate.
2. **Structural argument** — every edit is to JSX structure and `className`. No
   `useState`, `useEffect`, handler, API call, or i18n key is modified. The diff filtered
   to non-`className` lines must be near-empty outside `App.tsx` and `RotaHero.tsx`, and
   is reviewed as evidence.
3. **Playwright interaction pass** — login submit, register submit, forgot-password
   submit, Google button mount, every nav link, and the `#pricing` / `#demo` anchors.
4. **Screenshot matrix** — 390 / 768 / 1440 px across `en`, `ar` (RTL flip), `hi`
   (Devanagari `:lang` override), and `ja` (CJK fallback). The type strategy either holds
   here or it does not.
5. **Contrast and a11y** — all token pairs checked against WCAG AA programmatically;
   visible keyboard focus on every control; reduced-motion path verified.

Playwright scripts are throwaway verification, not a committed suite. Promoting them into
a real suite is a separate decision, deliberately left open.

## Risks

- **`newsprint` is one shade from the cream default.** The direction only works if the
  hard grid rules and condensed display type carry it; soft edges anywhere and it
  collapses back into the look being escaped. Mitigation: screenshot critique at each
  page, revise before proceeding.
- **Non-Latin locales get a visibly different type treatment** from Latin ones. This is
  accepted deliberately — legibility outranks identity — but the `ar`/`hi`/`ja`
  screenshots must confirm the result is coherent rather than broken.
- **`motion` is a new runtime dependency** against the standing CLAUDE.md rule. It was
  explicitly authorised for this work and is justified solely by the hero choreography.
  If the hero is cut, the dependency should be cut with it.
- **No test suite means regressions are caught by inspection**, not automation. The
  Playwright interaction pass narrows this but does not close it.
