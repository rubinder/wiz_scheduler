/**
 * App strings that must not be left in English in a non-English locale (#205).
 * PR #201 shipped the manager-access strings to all 19 files in English, and
 * Arabic and Urdu users saw left-to-right English inside an RTL page. This is
 * the app-side sibling of landingTranslated.test.ts.
 *
 * Every key under the sections in CHECKED_SECTIONS is checked. Most of the
 * app is not yet translated, so the guard grows a section at a time: add a
 * section here once it is fully translated. Strings of 12 characters or fewer
 * are skipped (short labels such as "Admin", "Status" or "Email" are often
 * legitimately the same word).
 */
import { describe, expect, it } from "vitest";
import en from "./en";
import { LANGUAGES } from "./types";

const MODULES = import.meta.glob("./*.ts", { import: "default", eager: true }) as Record<string, unknown>;

type Tree = { [k: string]: string | Tree };

const CHECKED_SECTIONS = ["team", "access", "myPreferences", "preferenceReviews", "payroll"] as const;

function flatten(node: Tree, prefix = ""): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(node)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (typeof v === "string") out[key] = v;
    else Object.assign(out, flatten(v, key));
  }
  return out;
}

const checkedStrings = (t: unknown) => {
  const tree = t as Record<string, Tree>;
  return flatten(Object.fromEntries(CHECKED_SECTIONS.map((s) => [s, tree[s]])));
};

/** Keys identical to English in every locale on purpose. */
const KEPT_IN_ENGLISH_ALL = new Set<string>([
  // An example address, not prose
  "team.emailPlaceholder",
]);

/** Per-locale strings deliberately left identical to English, reviewed by hand. */
const KEPT_IN_ENGLISH: Partial<Record<string, ReadonlySet<string>>> = {
  // Nigerian Pidgin writes the weekdays as in English.
  pcm: new Set(["myPreferences.dayNames"]),
};

const EN = checkedStrings(en);

describe("app strings are translated", () => {
  it("checks the 23 manager-access strings from #201", () => {
    expect(Object.keys(EN).length).toBeGreaterThanOrEqual(23);
    expect(EN["access.billingAdminOnly"]).toBeDefined();
    expect(EN["team.archivedAssignmentNote"]).toBeDefined();
  });

  for (const { code } of LANGUAGES.filter((l) => l.code !== "en")) {
    it(`${code} has no ${CHECKED_SECTIONS.join("/")} string left in English`, () => {
      const t = checkedStrings(MODULES[`./${code}.ts`]);
      const missing = Object.keys(EN).filter((k) => !(k in t));
      expect(missing).toEqual([]);
      const kept = KEPT_IN_ENGLISH[code] ?? new Set<string>();
      const untranslated = Object.keys(EN).filter(
        (k) => EN[k].length > 12 && t[k] === EN[k] && !KEPT_IN_ENGLISH_ALL.has(k) && !kept.has(k),
      );
      expect(untranslated).toEqual([]);
    });

    it(`${code} keeps placeholders byte-identical`, () => {
      const t = checkedStrings(MODULES[`./${code}.ts`]);
      const placeholders = (s: string) => (s.match(/\{\w+\}/g) ?? []).sort();
      const mismatched = Object.keys(EN).filter(
        (k) => JSON.stringify(placeholders(t[k] ?? "")) !== JSON.stringify(placeholders(EN[k])),
      );
      expect(mismatched).toEqual([]);
    });
  }
});
