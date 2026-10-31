/**
 * The marketing site ports `landing.*` and `features.*` from these files
 * (wiz_scheduler_marketing/scripts/port-copy.mjs) and serves them in all 19
 * locales. A string left in English shows up as English in the middle of,
 * say, the Japanese home page. Strings of 12 characters or fewer are skipped:
 * brand names, "AI", "CSV", short numerics legitimately match. The pricing
 * example table (`landing.example*`) is checked whatever its length, because
 * short cells like "200 / month" mix numbers with words; only cells with no
 * word (numbers, "$", units like GB/K) are exempt.
 */
import { describe, expect, it } from "vitest";
import en from "./en";
import { LANGUAGES } from "./types";

const MODULES = import.meta.glob("./*.ts", { import: "default", eager: true }) as Record<string, unknown>;

type Tree = { [k: string]: string | Tree };

function flatten(node: Tree, prefix = ""): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(node)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (typeof v === "string") out[key] = v;
    else Object.assign(out, flatten(v, key));
  }
  return out;
}

const ported = (t: unknown) => {
  const tree = t as { landing: Tree; features: Tree };
  return flatten({ landing: tree.landing, features: tree.features });
};

/** Units that may sit next to numbers in a pricing cell without being a word. */
const UNITS = new Set(["GB", "MB", "TB", "K", "M"]);

/** True when the string has a word of 3+ ASCII letters that is not a unit. */
const hasWord = (s: string) => (s.match(/[A-Za-z]{3,}/g) ?? []).some((w) => !UNITS.has(w));

/** Whether an English value must differ from English in every other locale. */
const checked = (k: string, v: string) =>
  k.startsWith("landing.example") ? hasWord(v) : v.length > 12;

/**
 * Per-locale strings deliberately left identical to English. Nigerian Pidgin
 * shares much of its vocabulary with English, so a natural Pidgin sentence can
 * match it word for word. Every entry here was reviewed by hand.
 */
const KEPT_IN_ENGLISH: Partial<Record<string, ReadonlySet<string>>> = {
  // "Item" is the native word for a line item in Portuguese and Indonesian
  pt: new Set<string>(["landing.exampleItem"]),
  id: new Set<string>(["landing.exampleItem"]),
  pcm: new Set<string>([
    // Product, plan and feature names that Pidgin speakers use as-is
    "landing.heroTitle",
    "landing.strategiesTitle",
    "landing.allInOnePlan",
    "landing.aiCredits",
    "landing.aiScheduling",
    "landing.featAITitle",
    "landing.normalGenTitle",
    "landing.featMultiLocTitle",
    "landing.feat7shiftsTitle",
    "landing.featAffinitiesTitle",
    "landing.featRoleEquivTitle",
    "landing.stratRotationHistory",
    "landing.stratAITag",
    // Pricing table labels and amounts
    "landing.storageIncluded",
    "landing.exampleItem",
    "landing.exampleUsage",
    "landing.exampleCost",
    "landing.exampleStorage",
    "landing.exampleBase",
    "landing.exampleAICost",
    "landing.exampleStorageCost",
    "landing.exampleTotal",
    // Pidgin keeps "month", "overage" and "blocks" (as in its own
    // pricePerMonth, overage and *OverageNote strings)
    "landing.exampleSchedulesUsage",
    "landing.exampleSchedulesCost",
    "landing.exampleEmployeesCost",
    // Privacy and compliance terms
    "landing.complianceTitle",
    "landing.featGDPRTitle",
    "landing.featGDPRDesc",
    "landing.compErasureTitle",
    "landing.compConsentTitle",
    "landing.compNoNamesTitle",
    // Button and section labels
    "landing.ctaBtn",
    "landing.inputAvailabilityTitle",
    "landing.inputRolesTitle",
    "features.tocTitle",
    // Screen names, kept as the app's Pidgin UI labels its screens
    // (the two input* titles: inputsDesc promises the app's own page names)
    "landing.inputHourRestrictionsTitle",
    "landing.inputAssociationTitle",
    "features.pages.role-equivalents.title",
    "features.pages.hour-restrictions.title",
    "features.pages.day-blackouts.title",
    "features.pages.day-preferences.title",
    "features.pages.hour-range-preferences.title",
    "features.pages.frequency-caps.title",
    "features.pages.employee-onboarding.title",
    "features.pages.employee-availability.title",
    "features.pages.employee-association.title",
    "features.pages.shift-templates.title",
    "features.pages.check-in-qr.title",
    "features.pages.check-in-report.title",
    "features.pages.special-hours.title",
    "features.pages.export-schedules.title",
    "features.pages.approved-schedules.title",
  ]),
};

const EN = ported(en);

describe("ported marketing copy is translated", () => {
  for (const { code } of LANGUAGES.filter((l) => l.code !== "en")) {
    it(`${code} has no landing/features string left in English`, () => {
      const t = ported(MODULES[`./${code}.ts`]);
      const missing = Object.keys(EN).filter((k) => !(k in t));
      expect(missing).toEqual([]);
      const kept = KEPT_IN_ENGLISH[code] ?? new Set<string>();
      const untranslated = Object.keys(EN).filter(
        (k) => checked(k, EN[k]) && t[k] === EN[k] && !kept.has(k),
      );
      expect(untranslated).toEqual([]);
    });
  }
});
